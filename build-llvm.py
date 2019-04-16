#!/usr/bin/env python3
import argparse
import errno
import pathlib
import os
import subprocess
import shutil
import utils


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


def linker_test(cc, ld):
    echo = subprocess.Popen(['echo', 'int main() { return 0; }'], stdout=subprocess.PIPE)
    cc_call = subprocess.Popen([cc, '-fuse-ld=' + ld, '-o', '/dev/null', '-x', 'c', '-'], stdin=echo.stdout, stderr=subprocess.DEVNULL)
    cc_call.communicate()
    return cc_call.returncode


def check_cc_ld_variables():
    utils.header("Checking CC and LD")
    if not 'CC' in os.environ:
        possible_compilers = ['clang-9', 'clang-8', 'clang-7', 'clang', 'gcc']
        for compiler in possible_compilers:
            cc = shutil.which(compiler)
            if cc is not None:
                break
        if cc is None:
            raise RuntimeError("Neither gcc nor clang could be found on your system!")
    else:
        cc = shutil.which(os.environ['CC'])

    cc = os.path.realpath(cc)
    cc_folder = os.path.dirname(cc)

    if not 'CXX' in os.environ:
        if "clang" in cc:
            cxx = "clang++"
        else:
            cxx = "g++"
        cxx = shutil.which(cxx, path=cc_folder + ":" + os.environ['PATH'])
    else:
        cxx = shutil.which(os.environ['CXX'])
    cxx = cxx.rstrip()

    if not 'LD' in os.environ:
        if "clang" in cc:
            possible_linkers = ['lld-9', 'lld-8', 'lld-7', 'lld', 'gold', 'bfd']
            for linker in possible_linkers:
                ld = shutil.which("ld." + linker, path=cc_folder + ":" + os.environ['PATH'])
                if ld is not None:
                    break
            # TODO: cc_clang_version check
        else:
            ld = "gold"
            if linker_test(cc, ld):
                ld = None
    else:
        ld = os.environ['LD']
        # TODO: Evaluate full path if cc_clang_version is greater than 3.9
        if "clang" in cc:
            ld = shutil.which(ld)
        if linker_test(cc, ld):
            print("LD won't work with " + cc + ", saving you from yourself by ignoring LD value")
            ld = None

    print("CC: " + cc)
    print("CXX: " + cxx)
    if ld is not None:
        ld = ld.rstrip()
        ld_to_print = shutil.which("ld." + ld)
        if ld_to_print is None:
            ld_to_print = shutil.which(ld)
        print("LD: " + ld_to_print)

    return cc, cxx, ld


def check_dependencies():
    utils.header("Checking dependencies")
    required_commands = ["cmake", "curl", "git", "ninja"]
    for command in required_commands:
        output = shutil.which(command)
        if output is None:
            raise RuntimeError(command + " could not be found, please install it!")
        print(output)


def fetch_llvm_binutils(root, update, branch):
    utils.header("Updating LLVM")
    p = pathlib.Path(root + "/llvm-project")
    if p.is_dir():
        if update:
            os.chdir(p)
            subprocess.run(["git", "checkout", branch], check=True)
            subprocess.run(["git", "pull", "--rebase"], check=True)
    else:
        subprocess.run(["git", "clone", "-b", branch, "git://github.com/llvm/llvm-project", p], check=True)

    utils.download_binutils(root)


def cleanup(root, incremental):
    build = pathlib.Path(root + "/build/llvm")
    if not incremental and build.is_dir():
        shutil.rmtree(build)
    try:
        os.makedirs(build)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    os.chdir(build)


def invoke_cmake(cc, cxx, debug, install_folder, ld, projects, root, targets):
    utils.header("Configuring LLVM")

    defines = {}
    defines['CLANG_ENABLE_ARCMT'] = 'OFF'
    defines['CLANG_ENABLE_STATIC_ANALYZER'] = 'OFF'
    defines['CLANG_PLUGIN_SUPPORT'] = 'OFF'
    defines['CMAKE_C_COMPILER'] = cc
    defines['CMAKE_CXX_COMPILER'] = cxx
    defines['CMAKE_INSTALL_PREFIX'] = install_folder
    defines['LLVM_BINUTILS_INCDIR'] = root + "/" + utils.current_binutils() + "/include"
    defines['LLVM_ENABLE_PROJECTS'] = projects
    defines['LLVM_ENABLE_BINDINGS'] = 'OFF'
    defines['LLVM_ENABLE_OCAMLDOC'] = 'OFF'
    defines['LLVM_ENABLE_TERMINFO'] = 'OFF'
    defines['LLVM_EXTERNAL_CLANG_TOOLS_EXTRA_SOURCE_DIR'] = ''
    defines['LLVM_INCLUDE_DOCS'] = ''
    defines['LLVM_INCLUDE_EXAMPLES'] = 'OFF'
    defines['LLVM_TARGETS_TO_BUILD'] = targets

    if debug:
        defines['CMAKE_BUILD_TYPE'] = 'Debug'
        defines['CMAKE_C_FLAGS'] = '-march=native -mtune=native'
        defines['CMAKE_CXX_FLAGS'] = '-march=native -mtune=native'
        defines['LLVM_BUILD_TESTS'] = 'ON'
    else:
        defines['CMAKE_BUILD_TYPE'] = 'Release'
        defines['CMAKE_C_FLAGS'] = '-O2 -march=native -mtune=native'
        defines['CMAKE_CXX_FLAGS'] = '-O2 -march=native -mtune=native'
        defines['LLVM_INCLUDE_TESTS'] = 'OFF'
        defines['LLVM_ENABLE_WARNINGS'] = 'OFF'

    if "compiler-rt" in projects:
        defines['COMPILER_RT_BUILD_LIBFUZZER'] = 'OFF'

    if shutil.which("ccache") is not None:
        defines['LLVM_CCACHE_BUILD'] = 'ON'

    if ld is not None:
        defines['LLVM_USE_LINKER'] = ld

    cmake = ['cmake', '-G', 'Ninja', '-Wno-dev']
    for key in defines:
        newdef = '-D' + key + '=' + defines[key]
        cmake += [newdef]
    cmake += [root + "/llvm-project/llvm"]

    subprocess.run(cmake, check=True)


def main():
    root = os.path.dirname(os.path.realpath(__file__))
    os.chdir(root)
    args = parse_parameters()
    cc, cxx, ld = check_cc_ld_variables()
    check_dependencies()
    fetch_llvm_binutils(root, not args.no_pull, args.branch)
    cleanup(root, args.incremental)
    invoke_cmake(cc, cxx, args.debug, args.install_folder, ld, args.projects, root, args.targets)
    pass


if __name__ == '__main__':
    main()
