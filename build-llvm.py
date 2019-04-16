#!/usr/bin/env python3
import argparse
import datetime
import errno
import pathlib
import os
import subprocess
import shutil
import textwrap
import time
import utils


# Returns Clang's version as an integer
#
# Some backstory:
#     When this was written in bash, this was implemented along the lines of
#     the clang-version.sh script that the Linux kernel uses, dumping the
#     __clang_major__, __clang_minor__, and __clang_patchlevel__ preprocessor
#     defintions and then using printf to format them. In Python, that is a lot
#     uglier because of how subprocess pipes work (see the 'linker_test'
#     function below). While this isn't much better, it works and doesn't
#     depend on pipes.
#
# How it works:
#     Clang's version string is different on various distributions but they
#     all similar enough.
#
#     apt.llvm.org: clang version 9.0.0-svn357849-1~exp1+0~20190406231252.1~1.gbpd89028 (trunk)
#     Arch Linux: clang version 8.0.0 (tags/RELEASE_800/final)
#     Debian: clang version 3.8.1-24 (tags/RELEASE_381/final)
#     Fedora: clang version 7.0.1 (Fedora 7.0.1-6.fc29)
#     Ubuntu: clang version 6.0.0-1ubuntu2 (tags/RELEASE_600/final)
#
#     This one liner:
#         1. Gets the first line of 'clang --version' (.splitlines()[0])
#         2. Gets the third chunk, which contains the numeric verison (.split(" ")[2])
#         3. Removes everything after a hyphen if it exists (.split("-")[0])
#         4. Removes the periods (.replace(".", ""))
#
# FIXME: Is there a better way to do this?
def clang_version(cc):
    return int(subprocess.check_output([cc, "--version"]).decode("utf-8").splitlines()[0].split(" ")[2].split("-")[0].replace(".",""))


def parse_parameters(root):
    parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-b", "--branch",
                        help=textwrap.dedent("""\
                        By default, the script builds the master branch (tip of tree) of LLVM. If you would
                        like to build an older branch, use this parameter. This may be helpful in tracking
                        down an older bug to properly bisect. This value is just passed along to 'git checkout'
                        so it can be a branch name, tag name, or hash.

                        """), type=str, default="master")
    parser.add_argument("-d", "--debug",
                        help=textwrap.dedent("""\
                        By default, the script builds LLVM in the release configuration with all of
                        the tests turned off and optimization at O2. This disables that optimization,
                        builds the tests, and changes the configuration to debug. This can help with
                        reporting problems to LLVM developers but will make compilation of both LLVM
                        and the kernel go slower.

                        """), action="store_true")
    parser.add_argument("-i", "--incremental",
                        help=textwrap.dedent("""\
                        By default, the script removes all build artifacts from previous compiles. This
                        prevents that, allowing for dirty builds and faster compiles.

                        """), action="store_true")
    parser.add_argument("-I", "--install-folder",
                        help=textwrap.dedent("""\
                        By default, the script will create a "usr" folder in the same folder as this script
                        and install the LLVM toolchain there. If you'd like to have it installed somewhere
                        else, pass it to this parameter. This can either be an absolute or relative path.

                        """), type=str, default=os.path.join(root.as_posix(), "usr"))
    parser.add_argument("-n", "--no-pull",
                        help=textwrap.dedent("""\
                        By default, the script always updates the LLVM repo before building. This prevents
                        that, which can be helpful during something like bisecting.

                        """), action="store_true")
    parser.add_argument("-p", "--projects",
                        help=textwrap.dedent("""\
                        Currently, the script only enables the clang, compiler-rt, and lld folders in LLVM. If
                        you would like to override this, you can use this parameter and supply a list that is
                        supported by LLVM_ENABLE_PROJECTS.

                        See step #5 here: https://llvm.org/docs/GettingStarted.html#getting-started-quickly-a-summary

                        Example: -p \"clang;lld;libcxx\"

                        """), type=str, default="clang;lld;compiler-rt")
    parser.add_argument("-t", "--targets",
                        help=textwrap.dedent("""\
                        LLVM is multitargeted by default. Currently, this script only enables the arm32, aarch64,
                        powerpc, and x86 backends because that's what the Linux kernel is currently concerned with.
                        If you would like to override this, you can use this parameter and supply a list that is
                        supported by LLVM_TARGETS_TO_BUILD: https://llvm.org/docs/CMake.html#llvm-specific-variables

                        Example: -t "AArch64;X86"

                        """), type=str, default="AArch64;ARM;PowerPC;X86")
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
            if clang_version(cc) < 390:
                os.environ['PATH'] = cc_folder + ":" + os.environ['PATH']
                ld = linker
        else:
            ld = "gold"
            if linker_test(cc, ld):
                ld = None
    else:
        ld = os.environ['LD']
        if "clang" in cc and clang_version(cc) >= 390:
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
    p = root.joinpath("llvm-project")
    if p.is_dir():
        if update:
            utils.header("Updating LLVM")
            subprocess.run(["git", "-C", p.as_posix(), "checkout", branch], check=True)
            subprocess.run(["git", "-C", p.as_posix(), "pull", "--rebase"], check=True)
    else:
        utils.header("Downloading LLVM")
        subprocess.run(["git", "clone", "-b", branch, "git://github.com/llvm/llvm-project", p.as_posix()], check=True)

    utils.download_binutils(root)


def cleanup(build, incremental):
    if not incremental and build.is_dir():
        shutil.rmtree(build.as_posix())
    build.mkdir(parents=True, exist_ok=True)


def invoke_cmake(build, cc, cxx, debug, install_folder, ld, projects, root, targets):
    utils.header("Configuring LLVM")

    defines = {}
    defines['CLANG_ENABLE_ARCMT'] = 'OFF'
    defines['CLANG_ENABLE_STATIC_ANALYZER'] = 'OFF'
    defines['CLANG_PLUGIN_SUPPORT'] = 'OFF'
    defines['CMAKE_C_COMPILER'] = cc
    defines['CMAKE_CXX_COMPILER'] = cxx
    defines['CMAKE_INSTALL_PREFIX'] = install_folder.as_posix()
    defines['LLVM_BINUTILS_INCDIR'] = root.joinpath(utils.current_binutils(), "include").as_posix()
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
    cmake += [root.joinpath("llvm-project", "llvm").as_posix()]

    subprocess.run(cmake, check=True, cwd=build.as_posix())


def invoke_ninja(build, install_folder):
    utils.header("Building LLVM")

    timeStarted = time.time()

    subprocess.run('ninja', check=True, cwd=build.as_posix())

    print()
    print("LLVM build duration: " + str(datetime.timedelta(seconds=int(time.time() - timeStarted))))

    subprocess.run(['ninja', 'install'], check=True, cwd=build.as_posix(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with install_folder.joinpath(".gitignore").open("w") as gitignore:
        gitignore.write("*")


def main():
    root = pathlib.Path(__file__).resolve().parent
    build = root.joinpath("build", "llvm")

    args = parse_parameters(root)

    install_folder = pathlib.Path(args.install_folder)
    if not install_folder.is_absolute():
        install_folder = root.joinpath(install_folder)

    cc, cxx, ld = check_cc_ld_variables()
    check_dependencies()
    fetch_llvm_binutils(root, not args.no_pull, args.branch)
    cleanup(build, args.incremental)
    invoke_cmake(build, cc, cxx, args.debug, install_folder, ld, args.projects, root, args.targets)
    invoke_ninja(build, install_folder)


if __name__ == '__main__':
    main()
