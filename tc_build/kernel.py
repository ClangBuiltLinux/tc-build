#!/usr/bin/env python3

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import NamedTemporaryFile
import time
from typing import Optional, TypedDict, Union

from tc_build.builder import Builder
from tc_build.source import SourceManager
import tc_build.utils


class MakeVars(TypedDict, total=False):
    ARCH: str
    CC: Path
    CROSS_COMPILE: str
    HOSTCC: Union[Path, str]
    HOSTCXX: str
    HOSTLDFLAGS: str
    KCFLAGS: str
    KCONFIG_ALLCONFIG: str
    LD: str
    LLVM: str
    LLVM_IAS: str
    O: Path  # noqa: E741
    OBJCOPY: str


class KernelBuilder(Builder):
    # If the user supplies their own kernel source, it must be at least this
    # version to ensure that all the build commands work, as the build commands
    # were written to target at least this version.
    MINIMUM_SUPPORTED_VERSION = (6, 9, 0)

    def __init__(self, arch: str) -> None:
        super().__init__()

        self.bolt_instrumentation: bool = False
        self.bolt_sampling_output: Path = tc_build.utils.UNINIT_PATH
        self.config_targets: list[str] = []
        self.cross_compile: str = ''
        self.lsm: LinuxSourceManager = LinuxSourceManager()
        self.make_variables: MakeVars = {
            'ARCH': arch,
            # We do not want warnings to cause build failures when profiling.
            'KCFLAGS': '-Wno-error',
        }
        self.show_commands: bool = True
        self.toolchain_prefix: Path = tc_build.utils.UNINIT_PATH
        self.toolchain_version: tuple[int, ...] = ()

    def build(self) -> None:
        bin_folder = Path(self.toolchain_prefix, 'bin')
        if self.bolt_instrumentation:
            self.make_variables['CC'] = Path(bin_folder, 'clang.inst')
        # The user may have configured clang without the host target, in which
        # case we need to use GCC for compiling the host utilities.
        if self.can_use_clang_as_hostcc():
            if 'CC' in self.make_variables:
                self.make_variables['HOSTCC'] = self.make_variables['CC']
            if self._test_clang(ld_path := f"--ld-path={bin_folder}/ld.lld"):
                self.make_variables['HOSTLDFLAGS'] = ld_path
        else:
            self.make_variables['HOSTCC'] = 'gcc'
            self.make_variables['HOSTCXX'] = 'g++'
        if self.needs_binutils():
            if not shutil.which(f"{self.cross_compile}elfedit"):
                tc_build.utils.print_warning(
                    f"binutils for {self.make_variables['ARCH']} ('{self.cross_compile}') could not be found, skipping kernel build..."
                )
                return
            self.make_variables['CROSS_COMPILE'] = self.cross_compile
        self.make_variables['LLVM'] = f"{bin_folder}/"
        if not self.can_use_ias():
            self.make_variables['LLVM_IAS'] = '0'
        self.make_variables['O'] = self.folders.build

        self.clean_build_folder()

        kconfig_allconfig = None
        # allmodconfig enables CONFIG_WERROR and other subsystem specific
        # -Werror configurations. Ensure all known configurations get disabled
        # via KCONFIG_ALLCONFIG, as they may override KCFLAGS=-Werror.
        if 'allmodconfig' in self.config_targets:
            self.folders.build.mkdir(parents=True)

            # Using a context manager for this would seriously convolute this
            # code, as we need to use the name of the object in make_cmd but
            # delete it after actually running the command so the rest of the
            # code after this function would need another level of indent. We
            # know that from this point forward, the function can only throw an
            # exception when calling make_cmd, so we can just wrap that in a
            # try: ... finally: ... statement to ensure that this file is
            # always cleaned up.
            # pylint: disable-next=consider-using-with
            kconfig_allconfig = NamedTemporaryFile(dir=self.folders.build)  # noqa: SIM115

            configs_to_disable = ['DRM_WERROR', 'WERROR']
            kconfig_allconfig_text = ''.join(
                f"CONFIG_{val}=n\n" for val in configs_to_disable
            ).encode('utf-8')

            kconfig_allconfig.write(kconfig_allconfig_text)
            kconfig_allconfig.seek(0)
            self.make_variables['KCONFIG_ALLCONFIG'] = kconfig_allconfig.name

        make_cmd = []
        if tc_build.utils.path_is_set(self.bolt_sampling_output):
            make_cmd += [
                'perf', 'record',
                '--branch-filter', 'any,u',
                '--event', 'cycles:u',
                '--output', self.bolt_sampling_output,
                '--',
            ]  # fmt: off
        make_cmd += ['make', '-C', self.folders.source, f"-skj{os.cpu_count()}"]
        make_cmd += [f"{key}={self.make_variables[key]}" for key in sorted(self.make_variables)]
        make_cmd += [*self.config_targets, 'all']

        # If the user has any CFLAGS in their environment, they can cause issues when building tools.
        # Ideally, the kernel would always clobber user flags via ':=' but we deal with reality.
        os.environ.pop('CFLAGS', '')

        build_start = time.time()
        try:
            self.run_cmd(make_cmd)
        finally:
            if kconfig_allconfig:
                kconfig_allconfig.close()
        tc_build.utils.print_info(f"Build duration: {tc_build.utils.get_duration(build_start)}")

    def can_use_ias(self) -> bool:
        return True

    def get_toolchain_version(self) -> tuple[int, ...]:
        if self.toolchain_version:
            return self.toolchain_version

        if not tc_build.utils.path_is_set(self.toolchain_prefix):
            raise RuntimeError('get_toolchain_version(): No toolchain prefix set?')
        if not (clang := Path(self.toolchain_prefix, 'bin/clang')).exists():
            raise RuntimeError(f"clang could not be found in {self.toolchain_prefix}?")

        clang_cmd = [clang, '-E', '-P', '-x', 'c', '-']
        clang_input = '__clang_major__ __clang_minor__ __clang_patchlevel__'
        clang_output = subprocess.run(
            clang_cmd, capture_output=True, check=True, input=clang_input, text=True
        ).stdout.strip()

        self.toolchain_version = tuple(int(elem) for elem in clang_output.split(' '))
        return self.toolchain_version

    def can_use_clang_as_hostcc(self) -> bool:
        return self._test_clang('-c')

    def needs_binutils(self) -> bool:
        return not self.can_use_ias()

    def _test_clang(self, args: Optional[Union[str, list]] = None) -> bool:
        clang = Path(self.toolchain_prefix, 'bin/clang')

        clang_args = ['-x', 'c', '-o', '/dev/null', '-']
        if args:
            if isinstance(args, str):
                clang_args.append(args)
            elif isinstance(args, list):
                clang_args.extend(args)
            else:
                raise ValueError(f"Invalid type for args: {args}")

        prog = 'int main(void) { return 0; }'

        try:
            subprocess.run(
                [clang, *clang_args], capture_output=True, check=True, input=prog, text=True
            )
        except subprocess.CalledProcessError:
            return False
        return True


class ArmKernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('arm')

        self.cross_compile = 'arm-linux-gnueabi-'

    def can_use_ias(self) -> bool:
        return self.get_toolchain_version() >= (13, 0, 0)


class ArmV5KernelBuilder(ArmKernelBuilder):
    def __init__(self) -> None:
        super().__init__()

        self.config_targets = ['multi_v5_defconfig']


class ArmV6KernelBuilder(ArmKernelBuilder):
    def __init__(self) -> None:
        super().__init__()

        self.config_targets = ['aspeed_g5_defconfig']

    def build(self) -> None:
        if not tc_build.utils.path_is_set(self.lsm.location):
            raise RuntimeError('build() called without configured LinuxSourceManager?')

        if self.get_toolchain_version() < (14, 0, 0) and self.lsm.get_version() >= (6, 14, 0):
            # https://github.com/ClangBuiltLinux/continuous-integration2/pull/807
            tc_build.utils.print_warning(
                'aspeed_g5_defconfig does not build with LLVM < 14.0.0 and Linux >= 6.14.0, skipping build...'
            )
            return

        super().build()


class ArmV7KernelBuilder(ArmKernelBuilder):
    def __init__(self) -> None:
        super().__init__()

        self.config_targets = ['multi_v7_defconfig']


class Arm64KernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('arm64')


class HexagonKernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('hexagon')


class LoongArchKernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('loongarch')

    def build(self) -> None:
        # https://git.kernel.org/linus/4d35d6e56447a5d09ccd1c1b3a6d3783b2947670
        if self.get_toolchain_version() < (min_version := (18, 0, 0)):
            tc_build.utils.print_warning(
                f"LoongArch does not build with LLVM < {'.'.join(map(str, min_version))}, skipping build..."
            )
            return

        super().build()


class MIPSKernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('mips')

        self.config_targets = ['malta_defconfig']


class PowerPCKernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('powerpc')

    def can_use_ias(self) -> bool:
        return False


class PowerPC32KernelBuilder(PowerPCKernelBuilder):
    def __init__(self) -> None:
        super().__init__()

        self.config_targets = ['pmac32_defconfig', 'disable-werror.config']
        self.cross_compile = 'powerpc-linux-gnu-'


class PowerPC64KernelBuilder(PowerPCKernelBuilder):
    def __init__(self) -> None:
        super().__init__()

        self.config_targets = ['ppc64_guest_defconfig', 'disable-werror.config']
        self.cross_compile = 'powerpc64-linux-gnu-'

    # https://github.com/llvm/llvm-project/commit/33504b3bbe10d5d4caae13efcb99bd159c126070
    def can_use_ias(self) -> bool:
        return self.get_toolchain_version() >= (14, 0, 2)

    # https://github.com/ClangBuiltLinux/linux/issues/1601
    def needs_binutils(self) -> bool:
        return True


class PowerPC64LEKernelBuilder(PowerPC64KernelBuilder):
    def __init__(self) -> None:
        super().__init__()

        self.config_targets = ['powernv_defconfig', 'disable-werror.config']
        self.cross_compile = 'powerpc64le-linux-gnu-'

    def build(self) -> None:
        # https://github.com/ClangBuiltLinux/linux/issues/1260
        if self.get_toolchain_version() < (12, 0, 0):
            self.make_variables['LD'] = self.cross_compile + 'ld'

        super().build()


class RISCVKernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('riscv')

        self.cross_compile = 'riscv64-linux-gnu-'

    # https://github.com/llvm/llvm-project/commit/bbea64250f65480d787e1c5ff45c4de3ec2dcda8
    def can_use_ias(self) -> bool:
        return self.get_toolchain_version() >= (13, 0, 0)


class S390KernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('s390')

        self.cross_compile = 's390x-linux-gnu-'

    def build(self) -> None:
        if self.get_toolchain_version() <= (15, 0, 0):
            # https://git.kernel.org/linus/30d17fac6aaedb40d111bb159f4b35525637ea78
            tc_build.utils.print_warning(
                's390 does not build with LLVM < 15.0.0, skipping build...'
            )
            return

        # LD: https://github.com/ClangBuiltLinux/linux/issues/1524
        # OBJCOPY: https://github.com/ClangBuiltLinux/linux/issues/1530

        # https://github.com/llvm/llvm-project/pull/75643
        lld_res = subprocess.run(
            [Path(self.toolchain_prefix, 'bin/ld.lld'), '-m', 'elf64_s390'],
            capture_output=True,
            check=False,
            text=True,
        )
        if 'error: unknown emulation:' in lld_res.stderr:
            self.make_variables['LD'] = f"{self.cross_compile}ld"

        # https://github.com/llvm/llvm-project/pull/81841
        objcopy_res = subprocess.run(
            [
                Path(self.toolchain_prefix, 'bin/llvm-objcopy'),
                '-I',
                'binary',
                '-O',
                'elf64-s390',
                '-',
                '/dev/null',
            ],
            capture_output=True,
            check=False,
            input='',
            text=True,
        )
        if 'error: invalid output format:' in objcopy_res.stderr:
            self.make_variables['OBJCOPY'] = f"{self.cross_compile}objcopy"

        super().build()

    def can_use_ias(self) -> bool:
        return True

    def needs_binutils(self) -> bool:
        return 'LD' in self.make_variables or 'OBJCOPY' in self.make_variables


class X8664KernelBuilder(KernelBuilder):
    def __init__(self) -> None:
        super().__init__('x86_64')

    def build(self) -> None:
        if not tc_build.utils.path_is_set(self.lsm.location):
            raise RuntimeError('build() called without configured LinuxSourceManager?')

        if self.get_toolchain_version() < (15, 0, 0) and self.lsm.get_version() >= (6, 15, 0):
            # https://git.kernel.org/linus/7861640aac52bbbb3dc2cd40fb93dfb3b3d0f43c
            tc_build.utils.print_warning(
                'x86_64 does not build with LLVM < 15.0.0 and Linux >= 6.15.0, skipping build...'
            )
            return

        super().build()


class LLVMKernelBuilder(Builder):
    def __init__(self) -> None:
        super().__init__()

        self.bolt_instrumentation = False
        self.bolt_sampling_output: Path = tc_build.utils.UNINIT_PATH
        self.matrix = {}
        self.toolchain_prefix: Path = tc_build.utils.UNINIT_PATH

    def build(self) -> None:
        lsm = LinuxSourceManager()
        lsm.location = self.folders.source

        builders = []

        allconfig_capable_builders: dict[str, type] = {
            'AArch64': Arm64KernelBuilder,
            'ARM': ArmKernelBuilder,
            'Hexagon': HexagonKernelBuilder,
            'PowerPC': PowerPC64KernelBuilder,
            'RISCV': RISCVKernelBuilder,
            'SystemZ': S390KernelBuilder,
            'X86': X8664KernelBuilder,
        }

        # https://git.kernel.org/stable/c/ab3f300524697919f64ae920e904d0836b4057b0
        # is needed to build ARCH=loongarch without disabling any
        # configurations (in addition to a copy of clang > 18.0.0 but that is
        # check when the build is invoked because it depends on the build of
        # the compiler).
        if lsm.get_version() >= (6, 6, 8):
            allconfig_capable_builders['LoongArch'] = LoongArchKernelBuilder

        # This is a little convoluted :/
        # The overall idea here is to avoid duplicating builds, so the
        # matrix consists of a series of configuration targets ("defconfig",
        # "allmodconfig", etc) and a list of LLVM targets to build for each
        # configuration target. From there, this block filters out the
        # architectures that cannot build their "all*configs" with clang, so
        # they are duplicated if both "defconfig" and "allmodconfig" are
        # requested.
        for config_target, llvm_targets in self.matrix.items():
            for llvm_target in llvm_targets:
                if config_target == 'defconfig' and llvm_target == 'AArch64':
                    builder = allconfig_capable_builders[llvm_target]()
                    # For arm64, defconfig is quite large. To be quicker and
                    # not use as much space for profiling data, use
                    # 'virtconfig'.
                    builder.config_targets = ['virtconfig']
                    builders.append(builder)
                elif config_target == 'defconfig' and llvm_target == 'ARM':
                    builders += [
                        ArmV5KernelBuilder(),
                        ArmV6KernelBuilder(),
                        ArmV7KernelBuilder(),
                    ]
                elif config_target == 'defconfig' and llvm_target == 'Mips':
                    builders.append(MIPSKernelBuilder())
                elif config_target == 'defconfig' and llvm_target == 'PowerPC':
                    builders += [
                        PowerPC32KernelBuilder(),
                        PowerPC64KernelBuilder(),
                        PowerPC64LEKernelBuilder(),
                    ]
                elif llvm_target in allconfig_capable_builders:
                    builder = allconfig_capable_builders[llvm_target]()
                    builder.config_targets = [config_target]
                    builders.append(builder)

        tc_build.utils.print_info(f"Building Linux {lsm.get_kernelversion()} for profiling...")

        for builder in builders:
            builder.bolt_instrumentation = self.bolt_instrumentation
            builder.bolt_sampling_output = self.bolt_sampling_output
            builder.folders.build = self.folders.build
            builder.folders.source = self.folders.source
            builder.lsm = lsm
            builder.toolchain_prefix = self.toolchain_prefix
            builder.build()


class LinuxSourceManager(SourceManager):
    def __init__(self, location: Optional[Path] = None) -> None:
        super().__init__(location)

        self.patches: list[Path] = []
        self._version: tuple[int, ...] = ()

    def get_kernelversion(self) -> str:
        return subprocess.run(
            ['make', '-s', 'kernelversion'],
            capture_output=True,
            check=True,
            cwd=self.location,
            text=True,
        ).stdout.strip()

    # Dynamically get the version of the supplied kernel source as a tuple,
    # which can be used to check if a provided kernel source is at least a
    # particular version.
    def get_version(self) -> tuple[int, ...]:
        # elem.split('-')[0] in case we are dealing with an -rc release.
        if not self._version:
            self._version = tuple(
                int(elem.split('-')[0]) for elem in self.get_kernelversion().split('.', 3)
            )
        return self._version

    def prepare(self) -> None:
        self.tarball.download()
        # If patches are specified, remove the source folder, we cannot assume
        # it has already been patched.
        if self.patches:
            shutil.rmtree(self.location, ignore_errors=True)
        if not self.location.exists():
            self.tarball.extract(self.location)
        for patch in self.patches:
            patch_cmd = [
                'patch',
                f"--directory={self.location}",
                '--forward',
                f"--input={patch}",
                '--strip=1',
            ]
            try:
                subprocess.run(patch_cmd, capture_output=True, check=True, text=True)
            except subprocess.CalledProcessError as err:
                # Turns 'patch -N' into a warning versus a hard error; it is
                # not the user's fault if we forget to drop a patch that has
                # been applied.
                if 'Reversed (or previously applied) patch detected' in err.stdout:
                    tc_build.utils.print_warning(
                        f"{patch} has already been applied in {self.location}, consider removing it"
                    )
                else:
                    raise err
            else:
                tc_build.utils.print_info(f"Applied {patch} to {self.location}")
        tc_build.utils.print_info(f"Source successfully prepared in {self.location}")
