#!/usr/bin/env python3
# Copyright (C) 2019 The ClangBuiltLinux Authors
# Description: Builds an LLVM toolchain suitable for kernel development

import argparse
import datetime
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
    return int(
        subprocess.check_output([
            cc, "--version"
        ]).decode("utf-8").splitlines()[0].split(" ")[2].split("-")[0].replace(
            ".", ""))


def parse_parameters(root):
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-b",
                        "--branch",
                        help=textwrap.dedent("""\
                        By default, the script builds the master branch (tip of tree) of LLVM. If you would
                        like to build an older branch, use this parameter. This may be helpful in tracking
                        down an older bug to properly bisect. This value is just passed along to 'git checkout'
                        so it can be a branch name, tag name, or hash.

                        """),
                        type=str,
                        default="master")
    parser.add_argument("-d",
                        "--debug",
                        help=textwrap.dedent("""\
                        By default, the script builds LLVM in the release configuration with all of
                        the tests turned off and optimization at O2. This disables that optimization,
                        builds the tests, and changes the configuration to debug. This can help with
                        reporting problems to LLVM developers but will make compilation of both LLVM
                        and the kernel go slower.

                        """),
                        action="store_true")
    parser.add_argument("-i",
                        "--incremental",
                        help=textwrap.dedent("""\
                        By default, the script removes all build artifacts from previous compiles. This
                        prevents that, allowing for dirty builds and faster compiles.

                        """),
                        action="store_true")
    parser.add_argument("-I",
                        "--install-folder",
                        help=textwrap.dedent("""\
                        By default, the script will create a "usr" folder in the same folder as this script
                        and install the LLVM toolchain there. If you'd like to have it installed somewhere
                        else, pass it to this parameter. This can either be an absolute or relative path.

                        """),
                        type=str,
                        default=os.path.join(root.as_posix(), "usr"))
    parser.add_argument("-n",
                        "--no-pull",
                        help=textwrap.dedent("""\
                        By default, the script always updates the LLVM repo before building. This prevents
                        that, which can be helpful during something like bisecting.

                        """),
                        action="store_true")
    parser.add_argument("-p",
                        "--projects",
                        help=textwrap.dedent("""\
                        Currently, the script only enables the clang, compiler-rt, and lld folders in LLVM. If
                        you would like to override this, you can use this parameter and supply a list that is
                        supported by LLVM_ENABLE_PROJECTS.

                        See step #5 here: https://llvm.org/docs/GettingStarted.html#getting-started-quickly-a-summary

                        Example: -p \"clang;lld;libcxx\"

                        """),
                        type=str,
                        default="clang;lld;compiler-rt")
    parser.add_argument("-t",
                        "--targets",
                        help=textwrap.dedent("""\
                        LLVM is multitargeted by default. Currently, this script only enables the arm32, aarch64,
                        powerpc, and x86 backends because that's what the Linux kernel is currently concerned with.
                        If you would like to override this, you can use this parameter and supply a list that is
                        supported by LLVM_TARGETS_TO_BUILD: https://llvm.org/docs/CMake.html#llvm-specific-variables

                        Example: -t "AArch64;X86"

                        """),
                        type=str,
                        default="AArch64;ARM;PowerPC;X86")
    return parser.parse_args()


# Test to see if the supplied ld value will work with cc -fuse=ld
def linker_test(cc, ld):
    echo = subprocess.Popen(['echo', 'int main() { return 0; }'],
                            stdout=subprocess.PIPE)
    cc_call = subprocess.Popen(
        [cc, '-fuse-ld=' + ld, '-o', '/dev/null', '-x', 'c', '-'],
        stdin=echo.stdout,
        stderr=subprocess.DEVNULL)
    cc_call.communicate()
    return cc_call.returncode


# Sets the cc, cxx, and ld variables, which will be passed to cmake
def check_cc_ld_variables():
    utils.print_header("Checking CC and LD")
    # If the user didn't supply a C compiler, try to find one
    if 'CC' not in os.environ:
        possible_compilers = ['clang-9', 'clang-8', 'clang-7', 'clang', 'gcc']
        for compiler in possible_compilers:
            cc = shutil.which(compiler)
            if cc is not None:
                break
        if cc is None:
            raise RuntimeError(
                "Neither gcc nor clang could be found on your system!")
    # Otherwise, get its full path
    else:
        cc = shutil.which(os.environ['CC'])

    # Evaluate if CC is a symlink. Certain packages of clang (like from
    # apt.llvm.org) symlink the clang++ binary to clang++-<version> in
    # /usr/bin, which then points to something like /usr/lib/llvm-<version/bin.
    # This won't be found by the dumb logic below and trying to parse and figure
    # out a heuristic for that is a lot more effort than just going into the
    # folder that clang is actually installed in and getting clang++ from there.
    cc = os.path.realpath(cc)
    cc_folder = os.path.dirname(cc)

    # If the user didn't supply a C++ compiler
    if 'CXX' not in os.environ:
        if "clang" in cc:
            cxx = "clang++"
        else:
            cxx = "g++"
        # Use the one that is located where CC is
        cxx = shutil.which(cxx, path=cc_folder + ":" + os.environ['PATH'])
    # Otherwise, get its full path
    else:
        cxx = shutil.which(os.environ['CXX'])
    cxx = cxx.rstrip()

    # If the user didn't specify a linker
    if 'LD' not in os.environ:
        # and we're using clang, try to find the fastest one
        if "clang" in cc:
            possible_linkers = [
                'lld-9', 'lld-8', 'lld-7', 'lld', 'gold', 'bfd'
            ]
            for linker in possible_linkers:
                # We want to find lld wherever the clang we are using is located
                ld = shutil.which("ld." + linker,
                                  path=cc_folder + ":" + os.environ['PATH'])
                if ld is not None:
                    break
            # If clang is older than 3.9, it won't accept absolute paths so we
            # need to just pass it the name (and modify PATH so that it is found properly)
            # https://github.com/llvm/llvm-project/commit/e43b7413597d8102a4412f9de41102e55f4f2ec9
            if clang_version(cc) < 390:
                os.environ['PATH'] = cc_folder + ":" + os.environ['PATH']
                ld = linker
        # and we're using gcc, try to use gold
        else:
            ld = "gold"
            if linker_test(cc, ld):
                ld = None
    # If the user did specify a linker
    else:
        # evaluate its full path with clang to avoid weird issues and check to
        # see if it will work with '-fuse-ld', which is what cmake will do. Doing
        # it now prevents a hard error later.
        ld = os.environ['LD']
        if "clang" in cc and clang_version(cc) >= 390:
            ld = shutil.which(ld)
        if linker_test(cc, ld):
            print("LD won't work with " + cc +
                  ", saving you from yourself by ignoring LD value")
            ld = None

    # Print what binaries we are using to compile/link with so the user can
    # decide if that is proper or not
    print("CC: " + cc)
    print("CXX: " + cxx)
    if ld is not None:
        ld = ld.rstrip()
        ld_to_print = shutil.which("ld." + ld)
        if ld_to_print is None:
            ld_to_print = shutil.which(ld)
        print("LD: " + ld_to_print)

    return cc, cxx, ld


# Make sure that the base dependencies of cmake, curl, git, and ninja are installed
def check_dependencies():
    utils.print_header("Checking dependencies")
    required_commands = ["cmake", "curl", "git", "ninja"]
    for command in required_commands:
        output = shutil.which(command)
        if output is None:
            raise RuntimeError(command +
                               " could not be found, please install it!")
        print(output)


# Download llvm and binutils or update them if they exist
def fetch_llvm_binutils(root, update, branch):
    p = root.joinpath("llvm-project")
    if p.is_dir():
        if update:
            utils.print_header("Updating LLVM")
            subprocess.run(
                ["git", "-C", p.as_posix(), "checkout", branch], check=True)
            subprocess.run(
                ["git", "-C", p.as_posix(), "pull", "--rebase"], check=True)
    else:
        utils.print_header("Downloading LLVM")
        subprocess.run([
            "git", "clone", "-b", branch, "git://github.com/llvm/llvm-project",
            p.as_posix()
        ],
            check=True)

    # One might wonder why we are downloading binutils in an LLVM build script :)
    # We need it for the LLVMgold plugin, which can be used for LTO with ld.gold,
    # which at the time of writing this, is how the Google Pixel 3 kernel is built
    # and linked.
    utils.download_binutils(root)


# Clean up and create the build folder
def cleanup(build, incremental):
    if not incremental and build.is_dir():
        shutil.rmtree(build.as_posix())
    build.mkdir(parents=True, exist_ok=True)


# Invoke cmake to generate the build files
def invoke_cmake(build, cc, cxx, debug, install_folder, ld, projects, root,
                 targets):
    utils.print_header("Configuring LLVM")

    # Base cmake defintions, which don't depend on any user supplied options
    defines = {
        # Objective-C Automatic Reference Counting (we don't use Objective-C)
        'CLANG_ENABLE_ARCMT': 'OFF',
        # We don't (currently) use the static analyzer
        'CLANG_ENABLE_STATIC_ANALYZER': 'OFF',
        # We don't use the plugin system and this saves cycles according to Chromium OS
        'CLANG_PLUGIN_SUPPORT': 'OFF',
        # The C compiler to use
        'CMAKE_C_COMPILER': cc,
        # The C++ compiler to use
        'CMAKE_CXX_COMPILER': cxx,
        # Where the toolchain should be installed
        'CMAKE_INSTALL_PREFIX': install_folder.as_posix(),
        # For LLVMgold.so, which is used for LTO with ld.gold
        'LLVM_BINUTILS_INCDIR': root.joinpath(utils.current_binutils(), "include").as_posix(),
        # The projects to build
        'LLVM_ENABLE_PROJECTS': projects,
        # Don't build bindings; they are for other languages that the kernel does not use
        'LLVM_ENABLE_BINDINGS': 'OFF',
        # Don't build Ocaml documentation
        'LLVM_ENABLE_OCAMLDOC': 'OFF',
        # Removes system dependency on terminfo and almost every major clang provider turns this off
        'LLVM_ENABLE_TERMINFO': 'OFF',
        # Don't build clang-tools-extras to cut down on build targets (about 400 files or so)
        'LLVM_EXTERNAL_CLANG_TOOLS_EXTRA_SOURCE_DIR': '',
        # Don't include documentation build targets because it is available on the web
        'LLVM_INCLUDE_DOCS': '',
        # Don't include example build targets to save on cmake cycles
        'LLVM_INCLUDE_EXAMPLES': 'OFF',
        # The architectures to build backends for
        'LLVM_TARGETS_TO_BUILD': targets}

    # If a debug build was requested
    if debug:
        defines['CMAKE_BUILD_TYPE'] = 'Debug'
        defines['CMAKE_C_FLAGS'] = '-march=native -mtune=native'
        defines['CMAKE_CXX_FLAGS'] = '-march=native -mtune=native'
        defines['LLVM_BUILD_TESTS'] = 'ON'
    # If a release build was requested
    else:
        defines['CMAKE_BUILD_TYPE'] = 'Release'
        defines['CMAKE_C_FLAGS'] = '-O2 -march=native -mtune=native'
        defines['CMAKE_CXX_FLAGS'] = '-O2 -march=native -mtune=native'
        defines['LLVM_INCLUDE_TESTS'] = 'OFF'
        defines['LLVM_ENABLE_WARNINGS'] = 'OFF'

    # Don't build libfuzzer when compiler-rt is enabled, it invokes cmake again and we don't use it
    if "compiler-rt" in projects:
        defines['COMPILER_RT_BUILD_LIBFUZZER'] = 'OFF'

    # Use ccache if it is available for faster incremental builds
    if shutil.which("ccache") is not None:
        defines['LLVM_CCACHE_BUILD'] = 'ON'

    # If we found a linker, we should use it
    if ld is not None:
        defines['LLVM_USE_LINKER'] = ld

    # Add the defines, point them to our build folder, and invoke cmake
    cmake = ['cmake', '-G', 'Ninja', '-Wno-dev']
    for key in defines:
        newdef = '-D' + key + '=' + defines[key]
        cmake += [newdef]
    cmake += [root.joinpath("llvm-project", "llvm").as_posix()]

    subprocess.run(cmake, check=True, cwd=build.as_posix())


# Build the world
def invoke_ninja(build, install_folder):
    utils.print_header("Building LLVM")

    time_started = time.time()

    subprocess.run('ninja', check=True, cwd=build.as_posix())

    print()
    print("LLVM build duration: " +
          str(datetime.timedelta(seconds=int(time.time() - time_started))))

    subprocess.run(['ninja', 'install'],
                   check=True,
                   cwd=build.as_posix(),
                   stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)

    utils.create_gitignore(install_folder)


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
    invoke_cmake(build, cc, cxx, args.debug, install_folder, ld, args.projects,
                 root, args.targets)
    invoke_ninja(build, install_folder)

    print("LLVM toolchain installed to: " + install_folder.as_posix())
    print("\nTo use, either run:\n")
    print("    $ export PATH=" + install_folder.as_posix() + ":${PATH}\n")
    print("or add:\n")
    print("    PATH=" + install_folder.as_posix() + ":${PATH}\n")
    print("to the command you want to use this toolchain.\n")


if __name__ == '__main__':
    main()
