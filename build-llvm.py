#!/usr/bin/env python3
import argparse


def parse_parameters():
    parser = argparse.ArgumentParser()
    parser.add_argument("-b", "--branch",
                        help="""
                        By default, the script builds the master branch (tip of tree) of LLVM. If you would
                        like to build an older branch, use this parameter. This may be helpful in tracking
                        down an older bug to properly bisect. This value is just passed along to 'git checkout'
                        so it can be a branch name, tag name, or hash.
                        """, type=str, default="master")
    parser.add_argument("-d", "--debug",
                        help="""
                        By default, the script builds LLVM in the release configuration with all of
                        the tests turned off and optimization at O2. This disables that optimization,
                        builds the tests, and changes the configuration to debug. This can help with
                        reporting problems to LLVM developers but will make compilation of both LLVM
                        and the kernel go slower.
                        """, action="store_true")
    parser.add_argument("-i", "--incremental",
                        help="""
                        By default, the script removes all build artifacts from previous compiles. This
                        prevents that, allowing for dirty builds and faster compiles.
                        """, action="store_true")
    parser.add_argument("-I", "--install-folder",
                        help="""
                        By default, the script will create a "usr" folder in the same folder as this script
                        and install the LLVM toolchain there. If you'd like to have it installed somewhere
                        else, pass it to this parameter. This can either be an absolute or relative path.

                        Example: ~/llvm
                        """, type=str, default=os.getcwd() + "/usr")
    parser.add_argument("-n", "--no-pull",
                        help="""
                        By default, the script always updates the LLVM repo before building. This prevents
                        that, which can be helpful during something like bisecting.
                        """, action="store_true")
    # FIXME: Formatting for help could use some work
    parser.add_argument("-p", "--projects",
                        help="""
                        Currently, the script only enables the clang, compiler-rt, and lld folders in LLVM. If
                        you would like to override this, you can use this parameter and supply a list that is
                        supported by LLVM_ENABLE_PROJECTS.

                        See step #5 here: https://llvm.org/docs/GettingStarted.html#getting-started-quickly-a-summary

                        Example: -p \"clang;lld;libcxx\"
                        """, type=str, default="clang;lld;compiler-rt")
    parser.add_argument("-t", "--targets",
                        help="""
                        LLVM is multitargeted by default. Currently, this script only enables the arm32, aarch64,
                        powerpc, and x86 backends because that's what the Linux kernel is currently concerned with.
                        If you would like to override this, you can use this parameter and supply a list that is
                        supported by LLVM_TARGETS_TO_BUILD: https://llvm.org/docs/CMake.html#llvm-specific-variables

                        Example: -t "AArch64;X86"
                        """, type=str, default="AArch64;ARM;PowerPC;X86")
    return parser.parse_args()


def main():
    args = parse_parameters()
    pass


if __name__ == '__main__':
    main()
