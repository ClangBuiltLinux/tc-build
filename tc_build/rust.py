import subprocess
import time
from pathlib import Path

import tc_build.utils
from tc_build.builder import Builder
from tc_build.source import GitSourceManager


class RustBuilder(Builder):
    def __init__(self):
        super().__init__()

        self.configure_set_args: list[str] = []
        self.llvm_install_folder: Path = tc_build.utils.UNINIT_PATH
        self.debug: bool = False
        self.vendor_string: str = ''

    def build(self) -> None:
        if not tc_build.utils.path_is_set(self.folders.build):
            msg = 'No build folder set for build()?'
            raise RuntimeError(msg)
        if not Path(self.folders.build, 'bootstrap.toml').exists():
            msg = 'No bootstrap.toml in build folder, run configure()?'
            raise RuntimeError(msg)

        build_start = time.time()
        self.run_cmd([Path(self.folders.source, 'x.py'), 'install'], cwd=self.folders.build)

        tc_build.utils.print_info(f"Build duration: {tc_build.utils.get_duration(build_start)}")

        if tc_build.utils.path_is_set(self.folders.install):
            tc_build.utils.create_gitignore(self.folders.install)

    def configure(self) -> None:
        if not tc_build.utils.path_is_set(self.llvm_install_folder):
            msg = 'No LLVM install folder set?'
            raise RuntimeError(msg)
        if not tc_build.utils.path_is_set(self.folders.source):
            msg = 'No source folder set?'
            raise RuntimeError(msg)
        if not tc_build.utils.path_is_set(self.folders.build):
            msg = 'No build folder set?'
            raise RuntimeError(msg)

        # Configure the build
        #
        # 'codegen-tests' requires '-DLLVM_INSTALL_UTILS=ON'.
        install_folder = (
            self.folders.install
            if tc_build.utils.path_is_set(self.folders.install)
            else self.folders.build
        )

        # fmt: off
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
        # fmt: on

        if self.debug:
            configure_cmd.append('--enable-debug')

        for val in self.configure_set_args:
            configure_cmd += ['--set', val]

        self.clean_build_folder()
        self.make_build_folder()
        self.run_cmd(configure_cmd, cwd=self.folders.build)

    def show_install_info(self) -> None:
        # Installation folder is optional, show build folder as the
        # installation location in that case.
        install_folder = (
            self.folders.install
            if tc_build.utils.path_is_set(self.folders.install)
            else self.folders.build
        )
        if not tc_build.utils.path_is_set(install_folder):
            msg = 'Installation folder not set?'
            raise RuntimeError(msg)
        if not install_folder.exists():
            msg = 'Installation folder does not exist, run build()?'
            raise RuntimeError(msg)
        if not (bin_folder := Path(install_folder, 'bin')).exists():
            msg = 'bin folder does not exist in installation folder, run build()?'
            raise RuntimeError(msg)

        tc_build.utils.print_header('Rust installation information')
        install_info = (
            f"Toolchain is available at: {install_folder}\n\n"
            'To use, either run:\n\n'
            f"\t$ export PATH={bin_folder}:$PATH\n\n"
            'or add:\n\n'
            f"\tPATH={bin_folder}:$PATH\n\n"
            'before the command you want to use this toolchain.\n'
        )
        print(install_info)

        for tool in ['rustc', 'rustdoc', 'rustfmt', 'clippy-driver', 'cargo']:
            if (binary := Path(bin_folder, tool)).exists():
                subprocess.run([binary, '--version', '--verbose'], check=True)
                print()
        tc_build.utils.flush_std_err_out()


class RustSourceManager(GitSourceManager):
    def __init__(self, repo: Path) -> None:
        super().__init__(repo)

        self._pretty_name = 'Rust'
        self._repo_url = 'https://github.com/rust-lang/rust.git'
