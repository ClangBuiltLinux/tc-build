#!/usr/bin/env python3

from pathlib import Path
import subprocess
import time

from tc_build.builder import Builder
from tc_build.source import GitSourceManager
import tc_build.utils


class RustBuilder(Builder):

    def __init__(self):
        super().__init__()

        self.llvm_install_folder = None
        self.debug = False
        self.vendor_string = ''

    def build(self):
        if not self.folders.build:
            raise RuntimeError('No build folder set for build()?')
        if not Path(self.folders.build, 'bootstrap.toml').exists():
            raise RuntimeError('No bootstrap.toml in build folder, run configure()?')

        build_start = time.time()
        self.run_cmd([Path(self.folders.source, 'x.py'), 'install'], cwd=self.folders.build)

        tc_build.utils.print_info(f"Build duration: {tc_build.utils.get_duration(build_start)}")

        if self.folders.install:
            tc_build.utils.create_gitignore(self.folders.install)

    def configure(self):
        if not self.llvm_install_folder:
            raise RuntimeError('No LLVM install folder set?')
        if not self.folders.source:
            raise RuntimeError('No source folder set?')
        if not self.folders.build:
            raise RuntimeError('No build folder set?')

        # Configure the build
        #
        # 'codegen-tests' requires '-DLLVM_INSTALL_UTILS=ON'.
        install_folder = self.folders.install if self.folders.install else self.folders.build

        # yapf: disable
        configure_cmd = [
            Path(self.folders.source, 'configure'),
            '--release-description', self.vendor_string,
            '--disable-docs',
            '--enable-locked-deps',
            '--enable-verbose-configure',
            '--tools', 'cargo,clippy,rustdoc,rustfmt,src',
            '--prefix', install_folder,
            '--sysconfdir', 'etc',
            '--disable-codegen-tests',
            '--disable-lld',
            '--disable-llvm-bitcode-linker',
            '--llvm-root', self.llvm_install_folder,
        ]
        # yapf: enable

        if self.debug:
            configure_cmd.append('--enable-debug')

        self.clean_build_folder()
        self.make_build_folder()
        self.run_cmd(configure_cmd, cwd=self.folders.build)

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

        tc_build.utils.print_header('Rust installation information')
        install_info = (f"Toolchain is available at: {install_folder}\n\n"
                        'To use, either run:\n\n'
                        f"\t$ export PATH={bin_folder}:$PATH\n\n"
                        'or add:\n\n'
                        f"\tPATH={bin_folder}:$PATH\n\n"
                        'before the command you want to use this toolchain.\n')
        print(install_info)

        for tool in ['rustc', 'rustdoc', 'rustfmt', 'clippy-driver', 'cargo']:
            if (binary := Path(bin_folder, tool)).exists():
                subprocess.run([binary, '--version', '--verbose'], check=True)
                print()
        tc_build.utils.flush_std_err_out()


class RustSourceManager(GitSourceManager):

    def __init__(self, repo):
        super().__init__(repo)

        self._pretty_name = 'Rust'
        self._repo_url = 'https://github.com/rust-lang/rust.git'
