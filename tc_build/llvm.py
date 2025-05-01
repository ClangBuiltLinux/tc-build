#!/usr/bin/env python3

import contextlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

from tc_build.builder import Builder
import tc_build.utils

LLVM_VER_FOR_RUNTIMES = 20


def get_all_targets(llvm_folder):
    contents = Path(llvm_folder, 'llvm/CMakeLists.txt').read_text(encoding='utf-8')
    if not (match := re.search(r'set\(LLVM_ALL_TARGETS([\w|\s]+)\)', contents)):
        raise RuntimeError('Could not find LLVM_ALL_TARGETS?')
    return [val for target in match.group(1).splitlines() if (val := target.strip())]


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
        }
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

        # clang-#: original binary
        # clang.bolt: BOLT optimized binary
        # .bolt will become original binary after optimization
        clang = Path(self.folders.build, 'bin/clang').resolve()
        clang_bolt = clang.with_name('clang.bolt')

        bolt_profile = Path(self.folders.build, 'clang.fdata')

        if mode == 'instrumentation':
            # clang.inst: instrumented binary, will be removed after generating profiles
            clang_inst = clang.with_name('clang.inst')

            clang_inst_cmd = [
                self.tools.llvm_bolt,
                '--instrument',
                f"--instrumentation-file={bolt_profile}",
                '--instrumentation-file-append-pid',
                '-o',
                clang_inst,
                clang,
            ]
            self.run_cmd(clang_inst_cmd)

            self.bolt_builder.bolt_instrumentation = True

        if mode == 'sampling':
            self.bolt_builder.bolt_sampling_output = Path(self.folders.build, 'perf.data')

        self.bolt_builder.toolchain_prefix = self.folders.build
        self.bolt_builder.build()

        # With instrumentation, we need to combine the profiles we generated,
        # as they are separated by PID
        if mode == 'instrumentation':
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
                clang,
            ]
            self.run_cmd(perf2bolt_cmd)
            self.bolt_builder.bolt_sampling_output.unlink()

        # Now actually optimize clang
        bolt_readme = Path(self.folders.source, 'bolt/README.md').read_text(encoding='utf-8')
        use_cache_plus = '-reorder-blocks=cache+' in bolt_readme
        use_sf_val = '-split-functions=2' in bolt_readme
        clang_opt_cmd = [
            self.tools.llvm_bolt,
            f"--data={bolt_profile}",
            '--dyno-stats',
            '--icf=1',
            '-o',
            clang_bolt,
            f"--reorder-blocks={'cache+' if use_cache_plus else 'ext-tsp'}",
            '--reorder-functions=hfsort+',
            '--split-all-cold',
            f"--split-functions{'=3' if use_sf_val else ''}",
            '--use-gnu-stack',
            clang,
        ]
        self.run_cmd(clang_opt_cmd)
        clang_bolt.replace(clang)
        if mode == 'instrumentation':
            clang_inst.unlink()

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
        # Utilize thin archives to save space. Use the deprecated -T for
        # compatibility with binutils<2.38 and llvm-ar<14. Unfortunately, thin
        # archives make compiler-rt archives not easily distributable, so we
        # disable the optimization when compiler-rt is enabled and there is an
        # install directory. Ideally thin archives should still be usable for
        # non-compiler-rt projects.
        if not (self.folders.install and self.project_is_enabled('compiler-rt')):
            self.cmake_defines['CMAKE_CXX_ARCHIVE_CREATE'] = '<CMAKE_AR> DqcT <TARGET> <OBJECTS>'
        self.cmake_defines['CMAKE_CXX_ARCHIVE_FINISH'] = 'true'

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
        self.cmake_defines['LLVM_TARGETS_TO_BUILD'] = ';'.join(self.targets)
        if self.tools.ld:
            self.cmake_defines['LLVM_USE_LINKER'] = self.tools.ld

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

        cmake_cmd += [f'-D{key}={self.cmake_defines[key]}' for key in sorted(self.cmake_defines)]

        self.clean_build_folder()
        self.run_cmd(cmake_cmd)

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

        all_targets = get_all_targets(self.folders.source)

        for target in self.targets:
            if target in ('all', 'host'):
                continue

            if target not in all_targets:
                # tuple() for shorter pretty printing versus instead of
                # ('{"', '".join(all_targets)}')
                raise RuntimeError(
                    f"Requested target ('{target}') was not found in LLVM_ALL_TARGETS {tuple(all_targets)}, check spelling?"
                )


class LLVMSlimBuilder(LLVMBuilder):

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

        llvm_build_runtime = self.cmake_defines.get('LLVM_BUILD_RUNTIME', 'ON') == 'ON'
        build_compiler_rt = self.project_is_enabled('compiler-rt') and llvm_build_runtime

        llvm_build_tools = self.cmake_defines.get('LLVM_BUILD_TOOLS', 'ON') == 'ON'

        self.set_llvm_major_version()

        distribution_components = []
        runtime_distribution_components = []
        if llvm_build_tools:
            distribution_components += [
                'llvm-ar',
                'llvm-nm',
                'llvm-objcopy',
                'llvm-objdump',
                'llvm-ranlib',
                'llvm-readelf',
                'llvm-strip',
            ]
        if self.project_is_enabled('bolt'):
            distribution_components.append('bolt')
        if self.project_is_enabled('clang'):
            distribution_components += ['clang', 'clang-resource-headers']
        if self.project_is_enabled('lld'):
            distribution_components.append('lld')
        if build_compiler_rt:
            distribution_components.append('llvm-profdata')
            if self.llvm_major_version >= LLVM_VER_FOR_RUNTIMES:
                distribution_components.append('runtimes')
                runtime_distribution_components.append('profile')
            else:
                distribution_components.append('profile')

        slim_llvm_defines = {
            # Tools needed by bootstrapping
            'LLVM_DISTRIBUTION_COMPONENTS': ';'.join(distribution_components),
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
        if runtime_distribution_components:
            slim_llvm_defines['LLVM_RUNTIME_DISTRIBUTION_COMPONENTS'] = ';'.join(runtime_distribution_components)

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
        if build_compiler_rt:
            self.cmake_defines.update(slim_compiler_rt_defines)

        super().configure()


class LLVMBootstrapBuilder(LLVMSlimBuilder):

    def __init__(self):
        super().__init__()

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
        self.cmake_defines['LLVM_LINK_LLVM_DYLIB'] = 'ON'

    def configure(self):
        # The following defines are needed to avoid thousands of warnings
        # along the lines of:
        # "Unable to track new values: Running out of static counters."
        # They require LLVM_LINK_DYLIB to be enabled, which is done above.
        cmake_options = Path(self.folders.source, 'llvm/cmake/modules/HandleLLVMOptions.cmake')
        cmake_text = cmake_options.read_text(encoding='utf-8')
        if 'LLVM_VP_COUNTERS_PER_SITE' in cmake_text:
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


class LLVMSourceManager:

    def __init__(self, repo):
        self.repo = repo

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

    def download(self, ref, shallow=False):
        if self.repo.exists():
            return

        tc_build.utils.print_header('Downloading LLVM')

        git_clone = ['git', 'clone']
        if shallow:
            git_clone.append('--depth=1')
            if ref != 'main':
                git_clone.append('--no-single-branch')
        git_clone += ['https://github.com/llvm/llvm-project', self.repo]

        subprocess.run(git_clone, check=True)

        self.git(['checkout', ref])

    def git(self, cmd, capture_output=False):
        return subprocess.run(['git', *cmd],
                              capture_output=capture_output,
                              check=True,
                              cwd=self.repo,
                              text=True)

    def git_capture(self, cmd):
        return self.git(cmd, capture_output=True).stdout.strip()

    def is_shallow(self):
        git_dir = self.git_capture(['rev-parse', '--git-dir'])
        return Path(git_dir, 'shallow').exists()

    def ref_exists(self, ref):
        try:
            self.git(['show-branch', ref])
        except subprocess.CalledProcessError:
            return False
        return True

    def update(self, ref):
        tc_build.utils.print_header('Updating LLVM')

        self.git(['fetch', 'origin'])

        if self.is_shallow() and not self.ref_exists(ref):
            raise RuntimeError(f"Repo is shallow and supplied ref ('{ref}') does not exist!")

        self.git(['checkout', ref])

        local_ref = None
        with contextlib.suppress(subprocess.CalledProcessError):
            local_ref = self.git_capture(['symbolic-ref', '-q', 'HEAD'])
        if local_ref and local_ref.startswith('refs/heads/'):
            self.git(['pull', '--rebase', 'origin', local_ref.replace('refs/heads/', '')])
