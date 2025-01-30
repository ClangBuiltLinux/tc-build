#!/usr/bin/env python3

import os
from pathlib import Path
import platform
from tempfile import TemporaryDirectory

from tc_build.builder import Builder
from tc_build.source import SourceManager
import tc_build.utils


class BinutilsBuilder(Builder):

    def __init__(self):
        super().__init__()

        self.cflags = ['-O2']
        self.configure_flags = [
            '--disable-compressed-debug-sections',
            '--disable-gdb',
            '--disable-gprofng',
            '--disable-nls',
            '--disable-werror',
            '--enable-deterministic-archives',
            '--enable-new-dtags',
            '--enable-plugins',
            '--enable-threads',
            '--quiet',
            '--with-system-zlib',
        ]

        self.configure_vars = {
            'CC': 'gcc',
            'CXX': 'g++',
        }
        self.extra_targets = []
        self.native_arch = ''
        self.target = ''

    def build(self):
        if self.folders.install:
            self.configure_flags.append(f"--prefix={self.folders.install}")
        if platform.machine() != self.native_arch:
            self.configure_flags += [
                f"--program-prefix={self.target}-",
                f"--target={self.target}",
            ]
        if self.extra_targets:
            self.configure_flags.append(f"--enable-targets={','.join(self.extra_targets)}")

        self.configure_vars['CFLAGS'] = ' '.join(self.cflags)
        self.configure_vars['CXXFLAGS'] = ' '.join(self.cflags)

        self.clean_build_folder()
        self.folders.build.mkdir(exist_ok=True, parents=True)
        tc_build.utils.print_header(f"Building {self.target} binutils")

        # Binutils does not provide a configuration flag to disable installation of documentation directly.
        # Instead, we redirect generated docs to a temporary directory, deleting them after installation.
        with TemporaryDirectory() as tmpdir:
            doc_dirs = ('info', 'html', 'pdf', 'man')
            self.configure_flags += [f"--{doc}dir={tmpdir}" for doc in doc_dirs]

            configure_cmd = [
                Path(self.folders.source, 'configure'),
                *self.configure_flags,
            ] + [f"{var}={val}" for var, val in self.configure_vars.items()]
            self.run_cmd(configure_cmd, cwd=self.folders.build)

            make_cmd = ['make', '-C', self.folders.build, '-s', f"-j{os.cpu_count()}", 'V=0']
            self.run_cmd(make_cmd)

            if self.folders.install:
                self.run_cmd([*make_cmd, 'install'])
                tc_build.utils.create_gitignore(self.folders.install)


class StandardBinutilsBuilder(BinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.configure_flags += [
            '--disable-sim',
            '--enable-lto',
            '--enable-relro',
            '--with-pic',
        ]


class NoMultilibBinutilsBuilder(BinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.configure_flags += [
            '--disable-multilib',
            '--with-gnu-as',
            '--with-gnu-ld',
        ]


class ArmBinutilsBuilder(NoMultilibBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.native_arch = 'armv7l'
        self.target = 'arm-linux-gnueabi'


class AArch64BinutilsBuilder(NoMultilibBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.native_arch = 'aarch64'
        self.target = 'aarch64-linux-gnu'


class LoongArchBinutilsBuilder(StandardBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.native_arch = 'loongarch64'
        self.target = 'loongarch64-linux-gnu'


class MipsBinutilsBuilder(StandardBinutilsBuilder):

    def __init__(self, endian_suffix=''):
        super().__init__()

        target_64 = f"mips64{endian_suffix}"
        self.extra_targets = [f"{target_64}-linux-gnueabi64", f"{target_64}-linux-gnueabin32"]

        target_32 = f"mips{endian_suffix}"
        self.native_target = target_32
        self.target = f"{target_32}-linux-gnu"


class MipselBinutilsBuilder(MipsBinutilsBuilder):

    def __init__(self):
        super().__init__('el')


class PowerPCBinutilsBuilder(StandardBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.native_arch = 'ppc'
        self.target = 'powerpc-linux-gnu'


class PowerPC64BinutilsBuilder(StandardBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.native_arch = 'ppc64'
        self.target = 'powerpc64-linux-gnu'


class PowerPC64LEBinutilsBuilder(StandardBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.native_arch = 'ppc64le'
        self.target = 'powerpc64le-linux-gnu'


class RISCV64BinutilsBuilder(StandardBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.native_arch = 'riscv64'
        self.target = 'riscv64-linux-gnu'


class S390XBinutilsBuilder(StandardBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.extra_targets.append('s390-linux-gnu')
        self.native_arch = 's390x'
        self.target = 's390x-linux-gnu'


class X8664BinutilsBuilder(StandardBinutilsBuilder):

    def __init__(self):
        super().__init__()

        self.extra_targets.append('x86_64-pep')
        self.native_arch = 'x86_64'
        self.target = 'x86_64-linux-gnu'


class BinutilsSourceManager(SourceManager):

    def default_targets(self):
        targets = [
            'aarch64',
            'arm',
            'mips',
            'mipsel',
            'powerpc',
            'powerpc64',
            'powerpc64le',
            'riscv64',
            's390x',
            'x86_64',
        ]
        if Path(self.location, 'gas/config/tc-loongarch.c').exists():
            targets.append('loongarch64')
        return targets

    def prepare(self):
        if not self.location:
            raise RuntimeError('No source location set?')
        if self.location.exists():
            return  # source already set up

        if not self.tarball.local_location:
            raise RuntimeError('No local tarball location set?')
        if not self.tarball.local_location.exists():
            self.tarball.download()

        self.tarball.extract(self.location)
        tc_build.utils.print_info(f"Source sucessfully prepared in {self.location}")
