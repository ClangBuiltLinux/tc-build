# pylint: disable=invalid-name

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import tc_build.utils


def cc_is_multicall(cc: Path | str) -> bool:
    return Path(cc).resolve().name == 'llvm'


def generate_versioned_binaries() -> list[str]:
    try:
        llvmversion_cmake = 'cmake/Modules/LLVMVersion.cmake'
        llvmversion_cmake_txt = tc_build.utils.curl(
            f"https://raw.githubusercontent.com/llvm/llvm-project/main/{llvmversion_cmake}"
        )
    except subprocess.CalledProcessError:
        llvm_tot_ver = 23
    else:
        if not (match := re.search(r'set\(LLVM_VERSION_MAJOR\s+(\d+)', llvmversion_cmake_txt)):
            msg = f"Could not find LLVM_VERSION_MAJOR in {llvmversion_cmake}"
            raise RuntimeError(msg)
        llvm_tot_ver = int(match.groups()[0])

    return [f'clang-{num}' for num in range(llvm_tot_ver, 6, -1)]


class Tools:
    def __init__(self) -> None:
        self.cc: Path = tc_build.utils.UNINIT_PATH
        self.cc_is_clang: bool = False

        self.ar: Path = tc_build.utils.UNINIT_PATH
        self.cxx: Path = tc_build.utils.UNINIT_PATH
        self.ld: Path = tc_build.utils.UNINIT_PATH
        self.ranlib: Path = tc_build.utils.UNINIT_PATH

        self.clang_tblgen: Path = tc_build.utils.UNINIT_PATH
        self.llvm_bolt: Path = tc_build.utils.UNINIT_PATH
        self.llvm_profdata: Path = tc_build.utils.UNINIT_PATH
        self.llvm_tblgen: Path = tc_build.utils.UNINIT_PATH
        self.merge_fdata: Path = tc_build.utils.UNINIT_PATH
        self.perf2bolt: Path = tc_build.utils.UNINIT_PATH


class HostTools(Tools):
    def __init__(self) -> None:
        super().__init__()

        self.cc = self.find_host_cc()
        self.cc_is_clang = 'clang' in self.cc.name

        self.ar = self.find_host_ar()
        self.cxx = self.find_host_cxx()
        self.ld = self.find_host_ld()
        self.ranlib = self.find_host_ranlib()

    def find_host_ar(self) -> Path:
        # GNU ar is the default, no need for llvm-ar if using GCC
        if not self.cc_is_clang:
            return tc_build.utils.UNINIT_PATH

        if (ar := Path(self.cc.parent, 'llvm-ar')).exists():
            return ar

        return tc_build.utils.UNINIT_PATH

    def find_host_cc(self) -> Path:
        # resolve() is called here and below to get /usr/lib/llvm-#/bin/... for
        # versioned LLVM binaries on Debian and Ubuntu. We do not want to
        # resolve a multicall binary though, as the symlink is how it works
        # properly.
        if tc_build.utils.path_is_set(cc := self.from_env('CC')):
            return cc if cc_is_multicall(cc) else cc.resolve()

        # As a special case, see if the first clang command in PATH is a
        # multicall binary, as there will be no clang-<ver> binary or symlink,
        # so the versioned binary logic below may result in a clang-<ver>
        # binary from PATH "overriding" the clang symlink to llvm. We generally
        # want clang-<ver> to override clang though because clang-<ver> may be
        # newer than a plain clang binary (such as when using apt.llvm.org).
        if (clang := shutil.which('clang')) and cc_is_multicall(clang):
            return Path(clang)

        possible_c_compilers = [*generate_versioned_binaries(), 'clang', 'gcc']
        for compiler in possible_c_compilers:
            if cc := shutil.which(compiler):
                break
        else:
            msg = 'Neither clang nor gcc could be found on your system?'
            raise RuntimeError(msg)

        return Path(cc).resolve()  # resolve() for Debian/Ubuntu variants

    def find_host_cxx(self) -> Path:
        if tc_build.utils.path_is_set(cxx := self.from_env('CXX')):
            return cxx

        possible_cxx_compiler = 'clang++' if self.cc_is_clang else 'g++'

        # Use CXX from the 'bin' folder of CC if it exists
        if (cxx := Path(self.cc.parent, possible_cxx_compiler)).exists():
            return cxx

        if not (cxx := shutil.which(possible_cxx_compiler)):
            msg = f"CXX ('{possible_cxx_compiler}') could not be found on your system?"
            raise RuntimeError(msg)

        return Path(cxx)

    def find_host_ld(self) -> Path:
        if tc_build.utils.path_is_set(ld := self.from_env('LD')):
            return ld

        if self.cc_is_clang:
            # First, see if there is an ld.lld installed in the same folder as
            # CC; if so, we know it can be used.
            if (ld := Path(self.cc.parent, 'ld.lld')).exists():
                return ld

            # If not, try to find a suitable linker via PATH
            possible_linkers = ['lld', 'gold', 'bfd']
            for linker in possible_linkers:
                if ld := shutil.which(f"ld.{linker}"):
                    break
            if not ld:
                return tc_build.utils.UNINIT_PATH
            return self.validate_ld(Path(ld))

        # For GCC, it is only worth testing 'gold'
        return self.validate_ld('gold')

    def find_host_ranlib(self) -> Path:
        # GNU ranlib is the default, no need for llvm-ranlib if using GCC
        if not self.cc_is_clang:
            return tc_build.utils.UNINIT_PATH

        if (ranlib := Path(self.cc.parent, 'llvm-ranlib')).exists():
            return ranlib

        return tc_build.utils.UNINIT_PATH

    def from_env(self, key: str) -> Path:
        if key not in os.environ:
            return tc_build.utils.UNINIT_PATH

        if key == 'LD':
            return self.validate_ld(os.environ[key], warn=True)

        if not (tool := shutil.which(os.environ[key])):
            msg = f"{key} value ('{os.environ[key]}') could not be found on your system?"
            raise RuntimeError(msg)
        return Path(tool)

    def show_compiler_linker(self) -> None:
        print(f"CC: {self.cc}")
        print(f"CXX: {self.cxx}")
        if self.ld:
            if isinstance(self.ld, Path):
                print(f"LD: {self.ld}")
            else:
                ld_to_print = self.ld if 'ld.' in self.ld else f"ld.{self.ld}"
                print(f"LD: {shutil.which(ld_to_print)}")
        tc_build.utils.flush_std_err_out()

    def validate_ld(self, ld: Path | str, warn=False) -> Path:
        cc_cmd = [self.cc, f'-fuse-ld={ld}', '-o', '/dev/null', '-x', 'c', '-']
        try:
            subprocess.run(
                cc_cmd,
                capture_output=True,
                check=True,
                input='int main(void) { return 0; }',
                text=True,
            )
        except subprocess.CalledProcessError:
            if warn:
                tc_build.utils.print_warning(
                    f"LD value ('{ld}') is not supported by CC ('{self.cc}'), ignoring it..."
                )
            return tc_build.utils.UNINIT_PATH

        return Path(ld)


class StageTools(Tools):
    def __init__(self, bin_folder: Path) -> None:
        super().__init__()

        # Used by cmake
        self.ar = Path(bin_folder, 'llvm-ar')
        self.cc = Path(bin_folder, 'clang')
        self.clang_tblgen = Path(bin_folder, 'clang-tblgen')
        self.cxx = Path(bin_folder, 'clang++')
        self.ld = Path(bin_folder, 'ld.lld')
        self.llvm_tblgen = Path(bin_folder, 'llvm-tblgen')
        self.ranlib = Path(bin_folder, 'llvm-ranlib')
        # Used by the builder
        self.llvm_bolt = Path(bin_folder, 'llvm-bolt')
        self.llvm_profdata = Path(bin_folder, 'llvm-profdata')
        self.merge_fdata = Path(bin_folder, 'merge-fdata')
        self.perf2bolt = Path(bin_folder, 'perf2bolt')
