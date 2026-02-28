#!/usr/bin/env python3

import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

from tc_build.builder import Builder
from tc_build.source import GitSourceManager
import tc_build.utils

LLVM_VER_FOR_RUNTIMES = 20
VALID_DISTRIBUTION_PROFILES = ('none', 'bootstrap', 'kernel', 'rust')


def get_all_targets(llvm_folder, experimental=False):
    contents = Path(llvm_folder, 'llvm/CMakeLists.txt').read_text(encoding='utf-8')
    targets = []

    variables = ['LLVM_ALL_TARGETS']
    if experimental:
        # Introduced by https://github.com/llvm/llvm-project/commit/1908820d6de5004964e85608070e7c869fc81eac in LLVM 17
        if 'LLVM_ALL_EXPERIMENTAL_TARGETS' in contents:
            variables.append('LLVM_ALL_EXPERIMENTAL_TARGETS')
        else:
            # Manually populate experimental targets based on list above
            possible_experimental_targets = ('ARC', 'CSKY', 'DirectX', 'M68k', 'SPIRV', 'Xtensa')
            targets += [
                target for target in possible_experimental_targets
                if Path(llvm_folder, 'llvm/lib/Target', target).exists()
            ]

    for variable in variables:
        if not (match := re.search(fr"set\({variable}([\w|\s]+)\)", contents)):
            raise RuntimeError(f"Could not find {variables}?")
        targets += [val for target in match.group(1).splitlines() if (val := target.strip())]
    return targets


class LLVMBuilder(Builder):

    def __init__(self):
        super().__init__()

        self.bolt = False
        self.bolt_builder = None
        self.build_targets = ['all']
        self.ccache = False
        self.check_targets = []
        self.cmake_defines = {
            # Reduce dynamic dependencies
            'LLVM_ENABLE_LIBXML2': 'OFF',
            # While this option reduces build resources and disk space, it
            # increases start up time for the tools dynamically linked against
            # it and limits optimization opportunities for LTO, PGO, and BOLT.
            'LLVM_LINK_LLVM_DYLIB': 'OFF',
        }
        self.distribution_profile = 'none'
        self.install_targets = []
        self.llvm_major_version = 0
        self.tools = None
        self.projects = []
        self.quiet_cmake = False
        self.targets = []

    def bolt_clang(self):
        # Default to instrumentation, as it should be universally available.
        mode = 'instrumentation'
        # If we can use perf for branch sampling, we switch to that mode, as
        # it is much quicker and it can result in more performance gains
        if self.can_use_perf():
            mode = 'sampling'

        tc_build.utils.print_header(f"Performing BOLT with {mode}")

        # real_binary: clang-# or llvm (if multicall is enabled)
        # bolt_binary: clang.bolt or llvm.bolt
        # .bolt will become original binary after optimization
        real_binary = Path(self.folders.build, 'bin/clang').resolve()
        binary_prefix = 'llvm' if self.multicall_is_enabled() else 'clang'
        bolt_binary = real_binary.with_name(f"{binary_prefix}.bolt")

        bolt_profile = Path(self.folders.build, f"{binary_prefix}.fdata")

        if mode == 'instrumentation':
            # clang.inst / llvm.inst: instrumented binary, will be removed after generating profiles
            inst_binary = real_binary.with_name(f"{binary_prefix}.inst")

            clang_inst_cmd = [
                self.tools.llvm_bolt,
                '--instrument',
                f"--instrumentation-file={bolt_profile}",
                '--instrumentation-file-append-pid',
                '-o',
                inst_binary,
                real_binary,
            ]
            # When running an instrumented binary on certain platforms (namely
            # Apple Silicon), there may be hangs due to instrumentation in
            # between exclusive load and store instructions:
            # https://github.com/llvm/llvm-project/issues/153492
            # Enable conservative instrumentation to avoid this.
            if tc_build.utils.cpu_is_apple_silicon():
                clang_inst_cmd.append('--conservative-instrumentation')
            self.run_cmd(clang_inst_cmd)

            if binary_prefix == 'llvm':
                # The multicall tools are all symlinked to the 'llvm' binary.
                # To avoid having to mess with those symlinks, perform a
                # shuffle of the real binary with the instrumented binary for
                # the build process.
                orig_binary = real_binary.with_name('llvm.orig')
                real_binary.replace(orig_binary)  # mv llvm llvm.orig
                inst_binary.replace(real_binary)  # mv llvm.inst llvm
            else:
                # This option changes CC when building Linux, which is only
                # needed when just clang is instrumented.
                self.bolt_builder.bolt_instrumentation = True

        if mode == 'sampling':
            self.bolt_builder.bolt_sampling_output = Path(self.folders.build, 'perf.data')

        self.bolt_builder.toolchain_prefix = self.folders.build
        self.bolt_builder.build()

        # With instrumentation, we need to combine the profiles we generated,
        # as they are separated by PID
        if mode == 'instrumentation':
            # Undo shuffle from above
            if binary_prefix == 'llvm':
                real_binary.replace(inst_binary)  # mv llvm llvm.inst
                orig_binary.replace(real_binary)  # mv llvm.orig llvm

            fdata_files = bolt_profile.parent.glob(f"{bolt_profile.name}.*.fdata")

            # merge-fdata will print one line for each .fdata it merges.
            # Redirect the output to a log file in case it ever needs to be
            # inspected.
            merge_fdata_log = Path(self.folders.build, 'merge-fdata.log')

            with bolt_profile.open('w', encoding='utf-8') as out_file, \
                 merge_fdata_log.open('w', encoding='utf-8') as err_file:
                tc_build.utils.print_info('Merging .fdata files, this might take a while...')
                subprocess.run([self.tools.merge_fdata, *list(fdata_files)],
                               check=True,
                               stderr=err_file,
                               stdout=out_file)
            for fdata_file in fdata_files:
                fdata_file.unlink()

        if mode == 'sampling':
            perf2bolt_cmd = [
                self.tools.perf2bolt,
                '-p',
                self.bolt_builder.bolt_sampling_output,
                '-o',
                bolt_profile,
                real_binary,
            ]
            self.run_cmd(perf2bolt_cmd)
            self.bolt_builder.bolt_sampling_output.unlink()

        # Now actually optimize clang
        bolt_readme = Path(self.folders.source, 'bolt/README.md').read_text(encoding='utf-8')
        use_cache_plus = '-reorder-blocks=cache+' in bolt_readme
        use_sf_val = '-split-functions=2' in bolt_readme
        if (bolt_cmd_ref := Path(self.folders.source,
                                 'bolt/docs/CommandLineArgumentReference.md')).exists():
            bolt_cmd_ref_txt = bolt_cmd_ref.read_text(encoding='utf-8')
            # https://github.com/llvm/llvm-project/commit/3c357a49d61e4c81a1ac016502ee504521bc8dda
            icf_val = 'all' if '--icf=<value>' in bolt_cmd_ref_txt else '1'
        else:
            icf_val = '1'
        # https://github.com/llvm/llvm-project/commit/9058503d2690022642d952ee80ecde5ecdbc79ca
        if Path(self.folders.source, 'bolt/lib/Passes/HFSortPlus.cpp').exists():
            reorder_funcs_val = 'hfsort+'
        else:
            reorder_funcs_val = 'cdsort'
        clang_opt_cmd = [
            self.tools.llvm_bolt,
            f"--data={bolt_profile}",
            '--dyno-stats',
            f"--icf={icf_val}",
            '-o',
            bolt_binary,
            f"--reorder-blocks={'cache+' if use_cache_plus else 'ext-tsp'}",
            f"--reorder-functions={reorder_funcs_val}",
            '--split-all-cold',
            f"--split-functions{'=3' if use_sf_val else ''}",
            '--use-gnu-stack',
            real_binary,
        ]
        self.run_cmd(clang_opt_cmd)
        bolt_binary.replace(real_binary)
        if mode == 'instrumentation':
            inst_binary.unlink()

    def build(self):
        if not self.folders.build:
            raise RuntimeError('No build folder set for build()?')
        if not Path(self.folders.build, 'build.ninja').exists():
            raise RuntimeError('No build.ninja in build folder, run configure()?')
        if self.bolt and not self.bolt_builder:
            raise RuntimeError('BOLT requested without a builder?')

        build_start = time.time()
        base_ninja_cmd = ['ninja', '-C', self.folders.build]
        self.run_cmd([*base_ninja_cmd, *self.build_targets])

        if self.check_targets:
            check_targets = [f"check-{target}" for target in self.check_targets]
            self.run_cmd([*base_ninja_cmd, *check_targets])

        tc_build.utils.print_info(f"Build duration: {tc_build.utils.get_duration(build_start)}")

        if self.bolt:
            self.bolt_clang()

        if self.folders.install:
            if self.install_targets:
                install_targets = [f"install-{target}" for target in self.install_targets]
            else:
                install_targets = ['install']
            self.run_cmd([*base_ninja_cmd, *install_targets], capture_output=True)
            tc_build.utils.create_gitignore(self.folders.install)

    def can_use_perf(self):
        # Make sure perf is in the environment
        if shutil.which('perf'):
            try:
                perf_cmd = [
                    'perf', 'record',
                    '--branch-filter', 'any,u',
                    '--event', 'cycles:u',
                    '--output', '/dev/null',
                    '--', 'sleep', '1',
                ]  # yapf: disable
                subprocess.run(perf_cmd, capture_output=True, check=True)
            except subprocess.CalledProcessError:
                pass  # Fallthrough to False below
            else:
                return True

        return False

    def check_dependencies(self):
        deps = ['cmake', 'curl', 'git', 'ninja']
        for dep in deps:
            if not shutil.which(dep):
                raise RuntimeError(f"Dependency ('{dep}') could not be found!")

    def configure(self):
        if not self.folders.build:
            raise RuntimeError('No build folder set?')
        if not self.folders.source:
            raise RuntimeError('No source folder set?')
        if not self.tools:
            raise RuntimeError('No build tools set?')
        if not self.projects:
            raise RuntimeError('No projects set?')
        if not self.targets:
            raise RuntimeError('No targets set?')

        self.validate_targets()
        self.set_llvm_major_version()

        # yapf: disable
        cmake_cmd = [
            'cmake',
            '-B', self.folders.build,
            '-G', 'Ninja',
            '-S', Path(self.folders.source, 'llvm'),
            '-Wno-dev',
        ]
        # yapf: enable
        if self.quiet_cmake:
            cmake_cmd.append('--log-level=NOTICE')

        if self.ccache:
            if shutil.which('ccache'):
                self.cmake_defines['CMAKE_C_COMPILER_LAUNCHER'] = 'ccache'
                self.cmake_defines['CMAKE_CXX_COMPILER_LAUNCHER'] = 'ccache'
            else:
                tc_build.utils.print_warning(
                    'ccache requested but could not be found on your system, ignoring...')

        if self.tools.clang_tblgen:
            self.cmake_defines['CLANG_TABLEGEN'] = self.tools.clang_tblgen

        if self.tools.ar:
            self.cmake_defines['CMAKE_AR'] = self.tools.ar
        if self.tools.ranlib:
            self.cmake_defines['CMAKE_RANLIB'] = self.tools.ranlib
        if 'CMAKE_BUILD_TYPE' not in self.cmake_defines:
            self.cmake_defines['CMAKE_BUILD_TYPE'] = 'Release'
        self.cmake_defines['CMAKE_C_COMPILER'] = self.tools.cc
        self.cmake_defines['CMAKE_CXX_COMPILER'] = self.tools.cxx
        if self.bolt:
            self.cmake_defines['CMAKE_EXE_LINKER_FLAGS'] = '-Wl,--emit-relocs'
        if self.folders.install:
            self.cmake_defines['CMAKE_INSTALL_PREFIX'] = self.folders.install

        # https://github.com/llvm/llvm-project/commit/b593110d89aea76b8b10152b24ece154bff3e4b5
        llvm_enable_projects = self.projects.copy()
        if self.llvm_major_version >= LLVM_VER_FOR_RUNTIMES and self.project_is_enabled(
                'compiler-rt'):
            llvm_enable_projects.remove('compiler-rt')
            self.cmake_defines['LLVM_ENABLE_RUNTIMES'] = 'compiler-rt'
        self.cmake_defines['LLVM_ENABLE_PROJECTS'] = ';'.join(llvm_enable_projects)
        # Remove system dependency on terminfo to keep the dynamic library
        # dependencies slim. This can be done unconditionally when the option
        # exists, as it does not impact clang's ability to show colors for
        # certain output like warnings. If the option does not exist, it means
        # that the linked change from clang-19 is present, which basically
        # makes LLVM_ENABLE_TERMINFO=OFF the default, so do not add it in that
        # case to avoid a cmake warning.
        # https://github.com/llvm/llvm-project/commit/6bf450c7a60fa62c642e39836566da94bb9bbc91
        llvm_cmakelists = Path(self.folders.source, 'llvm/CMakeLists.txt')
        llvm_cmakelists_txt = llvm_cmakelists.read_text(encoding='utf-8')
        if 'LLVM_ENABLE_TERMINFO' in llvm_cmakelists_txt:
            self.cmake_defines['LLVM_ENABLE_TERMINFO'] = 'OFF'
        # execinfo.h might not exist (Alpine Linux) but the GWP ASAN library
        # depends on it. Disable the option to avoid breaking the build, the
        # kernel does not depend on it.
        if self.project_is_enabled('compiler-rt') and not Path('/usr/include/execinfo.h').exists():
            self.cmake_defines['COMPILER_RT_BUILD_GWP_ASAN'] = 'OFF'
        if self.cmake_defines['CMAKE_BUILD_TYPE'] == 'Release':
            self.cmake_defines['LLVM_ENABLE_WARNINGS'] = 'OFF'
        if self.tools.llvm_tblgen:
            self.cmake_defines['LLVM_TABLEGEN'] = self.tools.llvm_tblgen
        if self.tools.ld:
            self.cmake_defines['LLVM_USE_LINKER'] = self.tools.ld

        # Separate "standard" targets from experimental targets. We know that
        # all values in targets are valid at this point from the
        # validate_targets() call above. If a value is not in the supported
        # standard targets list, it must be an experimental target.
        supported_standard_targets = get_all_targets(self.folders.source)
        standard_targets = []
        experimental_targets = []
        for target in self.targets:
            if target in supported_standard_targets:
                standard_targets.append(target)
            else:
                experimental_targets.append(target)
        if standard_targets:
            self.cmake_defines['LLVM_TARGETS_TO_BUILD'] = ';'.join(standard_targets)
        if experimental_targets:
            self.cmake_defines['LLVM_EXPERIMENTAL_TARGETS_TO_BUILD'] = ';'.join(
                experimental_targets)

        # Clear Linux needs a different target to find all of the C++ header files, otherwise
        # stage 2+ compiles will fail without this
        # We figure this out based on the existence of x86_64-generic-linux in the C++ headers path
        if list(Path('/usr/include/c++').glob('*/x86_64-generic-linux')):
            self.cmake_defines['LLVM_HOST_TRIPLE'] = 'x86_64-generic-linux'

        # By default, the Linux triples are for glibc, which might not work on
        # musl-based systems. If clang is available, get the default target triple
        # from it so that clang without a '--target' flag always works. This
        # behavior can be opted out of by setting DISTRIBUTING=1 in the
        # script's environment, in case the builder intends to distribute the
        # toolchain, as this may not be portable. Since distribution is not a
        # primary goal of tc-build, this is not abstracted further.
        if shutil.which('clang') and not os.environ.get('DISTRIBUTING'):
            default_target_triple = subprocess.run(['clang', '-print-target-triple'],
                                                   capture_output=True,
                                                   check=True,
                                                   text=True).stdout.strip()
            self.cmake_defines['LLVM_DEFAULT_TARGET_TRIPLE'] = default_target_triple

        self.handle_distribution_profile()

        cmake_cmd += [f'-D{key}={self.cmake_defines[key]}' for key in sorted(self.cmake_defines)]

        self.clean_build_folder()
        self.run_cmd(cmake_cmd)

    def handle_distribution_profile(self):
        if self.distribution_profile == 'none':
            return
        if self.distribution_profile not in VALID_DISTRIBUTION_PROFILES:
            raise RuntimeError(f"Unknown distribution profile: {self.distribution_profile}")

        self.set_llvm_major_version()

        llvm_build_runtime = self.cmake_defines.get('LLVM_BUILD_RUNTIME', 'ON') == 'ON'
        build_compiler_rt = self.project_is_enabled('compiler-rt') and llvm_build_runtime
        llvm_build_tools = self.cmake_defines.get('LLVM_BUILD_TOOLS', 'ON') == 'ON'

        distribution_components = []
        runtime_distribution_components = []

        # There are two distribution profiles.
        # bootstrap: Used for stage one to build the rest of LLVM
        # kernel: All tools used to build the kernel
        # rust: All tools, libraries, and headers needed to build Rust
        # For the most part, bootstrap is a subset of kernel, aside from the
        # tools and libraries for building an instrumented compiler.
        # rust is a superset of kernel for ease of implementation.
        if self.distribution_profile == 'rust':
            distribution_components += [
                'llvm-config',
                'llvm-headers',
                'llvm-libraries',
            ]
        if llvm_build_tools:
            distribution_components += [
                'llvm-ar',
                'llvm-ranlib',
            ]
            if self.distribution_profile in ('kernel', 'rust'):
                distribution_components += [
                    'llvm-nm',
                    'llvm-objcopy',
                    'llvm-objdump',
                    'llvm-readelf',
                    'llvm-strip',
                ]
            if self.distribution_profile == 'rust':
                distribution_components += [
                    'llc',
                    'llvm-as',
                    'llvm-cov',
                    'llvm-dis',
                    'llvm-link',
                    'llvm-size',
                    'opt',
                ]
            # If multicall is enabled, we need to add all possible tools to the
            # distribution components list to prevent them from being built as
            # standalone tools, which may break the build for tools like
            # llvm-symbolizer because they need LLVMDebuginfod but it is not
            # linked in that configuration. While this does build a little more
            # code for the 'distribution' target, it should result in only a
            # slight increase in installation size due to being a multicall
            # binary.
            if self.multicall_is_enabled():
                distribution_components += [
                    item for item in self.llvm_driver_binaries('llvm')
                    if item not in distribution_components
                ]
        if self.project_is_enabled('bolt'):
            distribution_components.append('bolt')
        if self.project_is_enabled('clang'):
            distribution_components += ['clang', 'clang-resource-headers']
            if self.multicall_is_enabled():
                distribution_components += [
                    item for item in self.llvm_driver_binaries('clang')
                    if item not in distribution_components
                ]
        if self.project_is_enabled('lld'):
            distribution_components.append('lld')

        if self.distribution_profile in ('bootstrap', 'rust'):
            distribution_components.append('llvm-profdata')

        if self.distribution_profile == 'bootstrap' and build_compiler_rt:
            if self.llvm_major_version >= LLVM_VER_FOR_RUNTIMES:
                distribution_components.append('runtimes')
                runtime_distribution_components.append('profile')
            else:
                distribution_components.append('profile')

        if self.distribution_profile == 'rust' and self.project_is_enabled('polly'):
            distribution_components.append('PollyISL')

        if distribution_components:
            self.cmake_defines['LLVM_DISTRIBUTION_COMPONENTS'] = ';'.join(distribution_components)
        if runtime_distribution_components:
            self.cmake_defines['LLVM_RUNTIME_DISTRIBUTION_COMPONENTS'] = ';'.join(
                runtime_distribution_components)

    def host_target(self):
        uname_to_llvm = {
            'aarch64': 'AArch64',
            'armv7l': 'ARM',
            'i386': 'X86',
            'mips': 'Mips',
            'mips64': 'Mips',
            'ppc': 'PowerPC',
            'ppc64': 'PowerPC',
            'ppc64le': 'PowerPC',
            'riscv32': 'RISCV',
            'riscv64': 'RISCV',
            's390x': 'SystemZ',
            'x86_64': 'X86',
        }
        return uname_to_llvm.get(platform.machine())

    def host_target_is_enabled(self):
        return 'all' in self.targets or self.host_target() in self.targets

    def llvm_driver_binaries(self, project):
        # Find all CMakeLists.txt for LLVM or clang tools that have multicall driver support
        cmakelists_txts = [
            cmakelists_txt
            for path in Path(self.folders.source, project).glob('tools/*/CMakeLists.txt')
            if '  GENERATE_DRIVER' in (cmakelists_txt := path.read_text(encoding='utf-8'))
        ]
        skip_tools = (
            # llvm-mt depends on libxml2, which we explicitly do not link against
            'llvm-mt', )
        # Return the values of the add_clang_tool() or add_llvm_tool() CMake macros
        return [
            tool for cmakelists_txt in cmakelists_txts
            if (match := re.search(r"^add_(?:clang|llvm)_tool\((.*)$", cmakelists_txt, flags=re.M))
            and (tool := match.groups()[0]) not in skip_tools
        ]

    def multicall_is_enabled(self):
        return self.cmake_defines.get('LLVM_TOOL_LLVM_DRIVER_BUILD', 'OFF') == 'ON'

    def project_is_enabled(self, project):
        return 'all' in self.projects or project in self.projects

    def set_llvm_major_version(self):
        if self.llvm_major_version:
            return  # no need to set if already set
        if not self.folders.source:
            raise RuntimeError('No source folder set?')
        if (llvmversion_cmake := Path(self.folders.source,
                                      'cmake/Modules/LLVMVersion.cmake')).exists():
            text_to_search = llvmversion_cmake.read_text(encoding='utf-8')
        else:
            text_to_search = Path(self.folders.source,
                                  'llvm/CMakeLists.txt').read_text(encoding='utf-8')
        if not (match := re.search(r'set\(LLVM_VERSION_MAJOR (\d+)\)', text_to_search)):
            raise RuntimeError('Could not find LLVM_VERSION_MAJOR in text?')
        self.llvm_major_version = int(match.group(1))

    def show_install_info(self):
        # Installation folder is optional, show build folder as the
        # installation location in that case.
        install_folder = self.folders.install if self.folders.install else self.folders.build
        if not install_folder:
            raise RuntimeError('Installation folder not set?')
        if not install_folder.exists():
            raise RuntimeError('Installation folder does not exist, run build()?')
        if not (bin_folder := Path(install_folder, 'bin')).exists():
            raise RuntimeError('bin folder does not exist in installation folder, run build()?')

        tc_build.utils.print_header('LLVM installation information')
        install_info = (f"Toolchain is available at: {install_folder}\n\n"
                        'To use, either run:\n\n'
                        f"\t$ export PATH={bin_folder}:$PATH\n\n"
                        'or add:\n\n'
                        f"\tPATH={bin_folder}:$PATH\n\n"
                        'before the command you want to use this toolchain.\n')
        print(install_info)

        for tool in ['clang', 'ld.lld']:
            if (binary := Path(bin_folder, tool)).exists():
                subprocess.run([binary, '--version'], check=True)
                print()
        tc_build.utils.flush_std_err_out()

    def validate_targets(self):
        if not self.folders.source:
            raise RuntimeError('No source folder set?')
        if not self.targets:
            raise RuntimeError('No targets set?')

        all_targets = get_all_targets(self.folders.source, experimental=True)

        for target in self.targets:
            if target in ('all', 'host'):
                continue

            if target not in all_targets:
                # tuple() for shorter pretty printing versus instead of
                # ('{"', '".join(all_targets)}')
                raise RuntimeError(
                    f"Requested target ('{target}') was not found in LLVM_ALL_TARGETS or LLVM_ALL_EXPERIMENTAL_TARGETS {tuple(all_targets)}, check spelling?"
                )


class LLVMSlimBuilder(LLVMBuilder):

    def __init__(self):
        super().__init__()

        self.distribution_profile = 'kernel'

    def configure(self):
        # yapf: disable
        slim_clang_defines = {
            # We don't (currently) use the static analyzer and it saves cycles
            # according to Chromium OS:
            # https://crrev.com/44702077cc9b5185fc21e99485ee4f0507722f82
            'CLANG_ENABLE_STATIC_ANALYZER': 'OFF',
            # We don't use the plugin system and it will remove unused symbols:
            # https://crbug.com/917404
            'CLANG_PLUGIN_SUPPORT': 'OFF',
        }

        # Objective-C Automatic Reference Counting (we don't use Objective-C)
        # https://clang.llvm.org/docs/AutomaticReferenceCounting.html
        # Disable this option only if it exists to prevent CMake warnings.
        # CLANG_ENABLE_ARCMT was deprecated in favor of CLANG_ENABLE_OBJC_REWRITER,
        # which is disabled by default.
        # https://github.com/llvm/llvm-project/commit/c4a019747c98ad9326a675d3cb5a70311ba170a2
        arcmt_cmakelists = Path(self.folders.source, 'clang/lib/ARCMigrate/CMakeLists.txt')
        if arcmt_cmakelists.exists():
            slim_clang_defines['CLANG_ENABLE_ARCMT'] = 'OFF'

        slim_llvm_defines = {
            # Don't build bindings; they are for other languages that the kernel does not use
            'LLVM_ENABLE_BINDINGS': 'OFF',
            # Don't build Ocaml documentation
            'LLVM_ENABLE_OCAMLDOC': 'OFF',
            # Don't build clang-tools-extras to cut down on build targets (about 400 files or so)
            'LLVM_EXTERNAL_CLANG_TOOLS_EXTRA_SOURCE_DIR': '',
            # Don't include documentation build targets because it is available on the web
            'LLVM_INCLUDE_DOCS': 'OFF',
            # Don't include example build targets to save on cmake cycles
            'LLVM_INCLUDE_EXAMPLES': 'OFF',
        }

        slim_compiler_rt_defines = {
            # Don't build libfuzzer when compiler-rt is enabled, it invokes cmake again and we don't use it
            'COMPILER_RT_BUILD_LIBFUZZER': 'OFF',
            # We only use compiler-rt for the sanitizers, disable some extra stuff we don't need
            # Chromium OS also does this: https://crrev.com/c/1629950
            'COMPILER_RT_BUILD_CRT': 'OFF',
            'COMPILER_RT_BUILD_XRAY': 'OFF',
        }
        # yapf: enable

        self.cmake_defines.update(slim_llvm_defines)
        if self.project_is_enabled('clang'):
            self.cmake_defines.update(slim_clang_defines)

        llvm_build_runtime = self.cmake_defines.get('LLVM_BUILD_RUNTIME', 'ON') == 'ON'
        build_compiler_rt = self.project_is_enabled('compiler-rt') and llvm_build_runtime

        if build_compiler_rt:
            self.cmake_defines.update(slim_compiler_rt_defines)

        super().configure()


class LLVMBootstrapBuilder(LLVMSlimBuilder):

    def __init__(self):
        super().__init__()

        self.distribution_profile = 'bootstrap'
        self.projects = ['clang', 'lld']
        self.targets = ['host']

    def configure(self):
        if self.project_is_enabled('compiler-rt'):
            self.cmake_defines['COMPILER_RT_BUILD_SANITIZERS'] = 'OFF'

        self.cmake_defines['CMAKE_BUILD_TYPE'] = 'Release'
        self.cmake_defines['LLVM_BUILD_UTILS'] = 'OFF'
        self.cmake_defines['LLVM_ENABLE_ASSERTIONS'] = 'OFF'
        self.cmake_defines['LLVM_ENABLE_BACKTRACES'] = 'OFF'
        self.cmake_defines['LLVM_INCLUDE_TESTS'] = 'OFF'

        super().configure()


class LLVMInstrumentedBuilder(LLVMBuilder):

    def __init__(self):
        super().__init__()

        self.cmake_defines['LLVM_BUILD_INSTRUMENTED'] = 'IR'
        self.cmake_defines['LLVM_BUILD_RUNTIME'] = 'OFF'

    def configure(self):
        no_multicall = not self.multicall_is_enabled()
        # The following defines are needed to avoid thousands of warnings
        # along the lines of:
        # "Unable to track new values: Running out of static counters."
        # LLVM_VP_COUNTERS_PER_SITE requires LLVM_LINK_DYLIB, which is only
        # done when multicall is not enabled. If multicall is enabled, we need
        # to use CMAKE_C{,XX}_FLAGS.
        cmake_options = Path(self.folders.source, 'llvm/cmake/modules/HandleLLVMOptions.cmake')
        cmake_text = cmake_options.read_text(encoding='utf-8')
        if no_multicall and 'LLVM_VP_COUNTERS_PER_SITE' in cmake_text:
            self.cmake_defines['LLVM_VP_COUNTERS_PER_SITE'] = '6'
        else:
            cflags = []
            cxxflags = []

            if 'CMAKE_C_FLAGS' in self.cmake_defines:
                cflags += self.cmake_defines['CMAKE_C_FLAGS'].split(' ')
            if 'CMAKE_CXX_FLAGS' in self.cmake_defines:
                cxxflags += self.cmake_defines['CMAKE_CXX_FLAGS'].split(' ')

            vp_counters = [
                '-Xclang',
                '-mllvm',
                '-Xclang',
                '-vp-counters-per-site=6',
            ]
            cflags += vp_counters
            cxxflags += vp_counters

            self.cmake_defines['CMAKE_C_FLAGS'] = ' '.join(cflags)
            self.cmake_defines['CMAKE_CXX_FLAGS'] = ' '.join(cxxflags)

        # These are currently incompatible:
        # https://github.com/llvm/llvm-project/pull/133596
        # But that should not matter much in this case because multicall uses
        # much less disk space.
        if no_multicall:
            self.cmake_defines['LLVM_LINK_LLVM_DYLIB'] = 'ON'

        super().configure()

    def generate_profdata(self):
        if not (profiles := list(self.folders.build.joinpath('profiles').glob('*.profraw'))):
            raise RuntimeError('No profiles generated?')

        llvm_prof_data_cmd = [
            self.tools.llvm_profdata,
            'merge',
            f"-output={Path(self.folders.build, 'profdata.prof')}",
            *profiles,
        ]
        subprocess.run(llvm_prof_data_cmd, check=True)


class LLVMSlimInstrumentedBuilder(LLVMInstrumentedBuilder, LLVMSlimBuilder):
    # No methods to override, this class inherits everyting from these super classes
    pass


class LLVMSourceManager(GitSourceManager):

    def __init__(self, repo):
        super().__init__(repo)

        self._pretty_name = 'LLVM'
        self._repo_url = 'https://github.com/llvm/llvm-project.git'

    def default_projects(self):
        return ['clang', 'compiler-rt', 'lld', 'polly']

    def default_targets(self):
        all_targets = get_all_targets(self.repo)
        targets = [
            'AArch64', 'ARM', 'BPF', 'Hexagon', 'Mips', 'PowerPC', 'RISCV', 'Sparc', 'SystemZ',
            'X86'
        ]

        if 'LoongArch' in all_targets:
            targets.append('LoongArch')

        return targets
