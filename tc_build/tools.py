#!/usr/bin/env python3
# pylint: disable=invalid-name

import os
from pathlib import Path
import re
import shutil
import subprocess

import tc_build.utils


class HostTools:

    def __init__(self):
        self.cc = self.find_host_cc()
        self.cc_is_clang = 'clang' in self.cc.name

        self.ar = self.find_host_ar()
        self.cxx = self.find_host_cxx()
        self.ld = self.find_host_ld()
        self.ranlib = self.find_host_ranlib()

        self.clang_tblgen = None
        self.llvm_bolt = None
        self.llvm_profdata = None
        self.llvm_tblgen = None
        self.merge_fdata = None
        self.perf2bolt = None

    def find_host_ar(self):
        # GNU ar is the default, no need for llvm-ar if using GCC
        if not self.cc_is_clang:
            return None

        if (ar := Path(self.cc.parent, 'llvm-ar')).exists():
            return ar

        return None

    def find_host_cc(self):
        # resolve() is called here and below to get /usr/lib/llvm-#/bin/... for
        # versioned LLVM binaries on Debian and Ubuntu.
        if (cc := self.from_env('CC')):
            return cc.resolve()

        possible_c_compilers = [*self.generate_versioned_binaries(), 'clang', 'gcc']
        for compiler in possible_c_compilers:
            if (cc := shutil.which(compiler)):
                break

        if not cc:
            raise RuntimeError('Neither clang nor gcc could be found on your system?')

        return Path(cc).resolve()  # resolve() for Debian/Ubuntu variants

    def find_host_cxx(self):
        if (cxx := self.from_env('CXX')):
            return cxx

        possible_cxx_compiler = 'clang++' if self.cc_is_clang else 'g++'

        # Use CXX from the 'bin' folder of CC if it exists
        if (cxx := Path(self.cc.parent, possible_cxx_compiler)).exists():
            return cxx

        if not (cxx := shutil.which(possible_cxx_compiler)):
            raise RuntimeError(
                f"CXX ('{possible_cxx_compiler}') could not be found on your system?")

        return Path(cxx)

    def find_host_ld(self):
        if (ld := self.from_env('LD')):
            return ld

        if self.cc_is_clang:
            # First, see if there is an ld.lld installed in the same folder as
            # CC; if so, we know it can be used.
            if (ld := Path(self.cc.parent, 'ld.lld')).exists():
                return ld

            # If not, try to find a suitable linker via PATH
            possible_linkers = ['lld', 'gold', 'bfd']
            for linker in possible_linkers:
                if (ld := shutil.which(f"ld.{linker}")):
                    break
            if not ld:
                return None
            return self.validate_ld(Path(ld))

        # For GCC, it is only worth testing 'gold'
        return self.validate_ld('gold')

    def find_host_ranlib(self):
        # GNU ranlib is the default, no need for llvm-ranlib if using GCC
        if not self.cc_is_clang:
            return None

        if (ranlib := Path(self.cc.parent, 'llvm-ranlib')).exists():
            return ranlib

        return None

    def from_env(self, key):
        if key not in os.environ:
            return None

        if key == 'LD':
            return self.validate_ld(os.environ[key], warn=True)

        if not (tool := shutil.which(os.environ[key])):
            raise RuntimeError(
                f"{key} value ('{os.environ[key]}') could not be found on your system?")
        return Path(tool)

    def generate_versioned_binaries(self):
        try:
            cmakelists_txt = tc_build.utils.curl(
                'https://raw.githubusercontent.com/llvm/llvm-project/main/llvm/CMakeLists.txt')
        except subprocess.CalledProcessError:
            llvm_tot_ver = 16
        else:
            if not (match := re.search(r'set\(LLVM_VERSION_MAJOR\s+(\d+)', cmakelists_txt)):
                raise RuntimeError('Could not find LLVM_VERSION_MAJOR in CMakeLists.txt?')
            llvm_tot_ver = int(match.groups()[0])

        return [f'clang-{num}' for num in range(llvm_tot_ver, 6, -1)]

    def show_compiler_linker(self):
        print(f"CC: {self.cc}")
        print(f"CXX: {self.cxx}")
        if self.ld:
            if isinstance(self.ld, Path):
                print(f"LD: {self.ld}")
            else:
                ld_to_print = self.ld if 'ld.' in self.ld else f"ld.{self.ld}"
                print(f"LD: {shutil.which(ld_to_print)}")
        tc_build.utils.flush_std_err_out()

    def validate_ld(self, ld, warn=False):
        if not ld:
            return None

        cc_cmd = [self.cc, f'-fuse-ld={ld}', '-o', '/dev/null', '-x', 'c', '-']
        try:
            subprocess.run(cc_cmd,
                           capture_output=True,
                           check=True,
                           input='int main(void) { return 0; }',
                           text=True)
        except subprocess.CalledProcessError:
            if warn:
                tc_build.utils.print_warning(
                    f"LD value ('{ld}') is not supported by CC ('{self.cc}'), ignoring it...")
            return None

        return ld


class StageTools:

    def __init__(self, bin_folder):
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
