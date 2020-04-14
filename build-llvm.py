#!/usr/bin/env python3
# Description: Builds an LLVM toolchain suitable for kernel development

import argparse
import datetime
import glob
import pathlib
import os
import subprocess
import shutil
import textwrap
import time
import utils
import re
import urllib.request as request
from urllib.error import URLError

# This is a known good revision of LLVM for building the kernel
# To bump this, run 'PATH_OVERRIDE=<path_to_updated_toolchain>/bin kernel/build.sh --allyesconfig'
GOOD_REVISION = 'ebad678857a94c32ce7b6931e9c642b32d278b67'


class Directories:
    def __init__(self, build_folder, install_folder, root_folder):
        self.build_folder = build_folder
        self.install_folder = install_folder
        self.root_folder = root_folder


class EnvVars:
    def __init__(self, cc, cxx, ld):
        self.cc = cc
        self.cxx = cxx
        self.ld = ld


def clang_version(cc, root_folder):
    """
    Returns Clang's version as an integer
    :param cc: The compiler to check the version of
    :param root_folder: Top of the script folder
    :return: an int denoting the version of the given compiler
    """
    command = [root_folder.joinpath("clang-version.sh").as_posix(), cc]
    return int(subprocess.check_output(command).decode())


def parse_parameters(root_folder):
    """
    Parses parameters passed to the script into options
    :param root_folder: The directory where the script is being invoked from
    :return: A 'Namespace' object with all the options parsed from supplied parameters
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--assertions",
                        help=textwrap.dedent("""\
                        In a release configuration, assertions are not enabled. Assertions can help catch
                        issues when compiling but it will increase compile times by 15-20%%.

                        """),
                        action="store_true")
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
    parser.add_argument("-B",
                        "--build-folder",
                        help=textwrap.dedent("""\
                        By default, the script will create a "build" folder in the same folder as this script,
                        then an "llvm" folder within that one and build the files there. If you would like
                        that done somewhere else, pass it to this parameter. This can either be an absolute
                        or relative path.

                        """),
                        type=str,
                        default=os.path.join(root_folder.as_posix(), "build",
                                             "llvm"))
    parser.add_argument("--build-stage1-only",
                        help=textwrap.dedent("""\
                        By default, the script does a multi-stage build: it builds a more lightweight version of
                        LLVM first (stage 1) then uses that build to build the full toolchain (stage 2). This
                        is also known as bootstrapping.

                        This option avoids that, building the first stage as if it were the final stage. Note,
                        this does not install the first stage only toolchain by default to avoid overwritting an
                        installed mutlt-stage LLVM toolchain; this option is more intended for quick testing
                        and verification of issues and not regular use. However, if your system is slow or can't
                        handle 2+ stage builds, you may need this flag. If you would like to install a toolchain
                        built with this flag, see '--install-stage1-only' below.

                        """),
                        action="store_true")
    # yapf: disable
    parser.add_argument("--build-type",
                        metavar='BUILD_TYPE',
                        help=textwrap.dedent("""\
                        By default, the script does a Release build; Debug may be useful for tracking down
                        particularly nasty bugs.

                        See https://llvm.org/docs/GettingStarted.html#compiling-the-llvm-suite-source-code for
                        more information.

                        """),
                        type=str,
                        choices=['Release', 'Debug', 'RelWithDebInfo', 'MinSizeRel'],
                        default="Release")
    # yapf: enable
    parser.add_argument("--check-targets",
                        help=textwrap.dedent("""\
                        By default, no testing is run on the toolchain. If you would like to run unit/regression
                        tests, use this parameter to specify a list of check targets to run with ninja. Common
                        ones include check-llvm, check-clang, and check-lld.

                        The values passed to this parameter will be automatically concatenated with 'check-'.

                        Example: '--check-targets clang llvm' will make ninja invokve 'check-clang' and 'check-llvm'.

                        """),
                        nargs="+")
    parser.add_argument("--clang-vendor",
                        help=textwrap.dedent("""\
                        Add this value to the clang version string (like "Apple clang version..." or
                        "Android clang version..."). Useful when reverting or applying patches on top
                        of upstream clang to differentiate a toolchain built with this script from
                        upstream clang or to distinguish a toolchain built with this script from the
                        system's clang. Defaults to ClangBuiltLinux.

                        """),
                        type=str,
                        default="ClangBuiltLinux")
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
                        By default, the script will create an "install" folder in the same folder as this script
                        and install the LLVM toolchain there. If you'd like to have it installed somewhere
                        else, pass it to this parameter. This can either be an absolute or relative path.

                        """),
                        type=str,
                        default=os.path.join(root_folder.as_posix(),
                                             "install"))
    parser.add_argument("--install-stage1-only",
                        help=textwrap.dedent("""\
                        When doing a stage 1 only build with '--build-stage1-only', install the toolchain to
                        the value of INSTALL_FOLDER.

                        """),
                        action="store_true")
    parser.add_argument("--lto",
                        metavar="LTO_TYPE",
                        help=textwrap.dedent("""\
                        Build the final compiler with either full LTO (full) or ThinLTO (thin), which can
                        improve compile time performance.

                        See the two links below for more information.

                        https://llvm.org/docs/LinkTimeOptimization.html
                        https://clang.llvm.org/docs/ThinLTO.html

                        """),
                        type=str,
                        choices=['full', 'thin'])
    parser.add_argument("-m",
                        "--march",
                        metavar="ARCH",
                        help=textwrap.dedent("""\
                        Add -march=ARCH and -mtune=ARCH to CFLAGS to further optimize the toolchain for the
                        target host processor.

                        """),
                        type=str)
    parser.add_argument("-n",
                        "--no-update",
                        help=textwrap.dedent("""\
                        By default, the script always updates the LLVM repo before building. This prevents
                        that, which can be helpful during something like bisecting or manually managing the
                        repo to pin it to a particular revision.

                        """),
                        action="store_true")
    parser.add_argument("--no-ccache",
                        help=textwrap.dedent("""\
                        Don't enable LLVM_CCACHE_BUILD. Useful for benchmarking clean builds.

                        """),
                        action="store_true")
    parser.add_argument("-s",
                        "--shallow-clone",
                        help=textwrap.dedent("""\
                        Only fetch the required objects and omit history when cloning the LLVM repo. This
                        speeds up the initial clone, but may break updating to later revisions and thus
                        necessitate a re-clone in the future.

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
                        default="clang;compiler-rt;lld;polly")
    parser.add_argument("--pgo",
                        help=textwrap.dedent("""\
                        Build the final compiler with PGO, which can improve compile time performance.

                        See https://llvm.org/docs/HowToBuildWithPGO.html for more information.

                        """),
                        action="store_true")
    parser.add_argument("-t",
                        "--targets",
                        help=textwrap.dedent("""\
                        LLVM is multitargeted by default. Currently, this script only enables the arm32, aarch64,
                        mips, powerpc, riscv, s390, and x86 backends because that's what the Linux kernel is
                        currently concerned with. If you would like to override this, you can use this parameter
                        and supply a list that is supported by LLVM_TARGETS_TO_BUILD:

                        https://llvm.org/docs/CMake.html#llvm-specific-variables

                        Example: -t "AArch64;X86"

                        """),
                        type=str,
                        default="AArch64;ARM;Mips;PowerPC;RISCV;SystemZ;X86")
    parser.add_argument("--use-good-revision",
                        help=textwrap.dedent("""\
                        By default, the script updates LLVM to the latest tip of tree revision, which may at times be
                        broken or not work right. With this option, it will checkout a known good revision of LLVM
                        that builds and works properly. If you use this option often, please remember to update the
                        script as the known good revision will change.

                        """),
                        action="store_true")
    return parser.parse_args()


def linker_test(cc, ld):
    """
    Test to see if the supplied ld value will work with cc -fuse=ld
    :param cc: A working C compiler to compile the test program
    :param ld: A linker to test -fuse=ld against
    :return: 0 if the linker supports -fuse=ld, 1 otherwise
    """
    echo = subprocess.Popen(['echo', 'int main() { return 0; }'],
                            stdout=subprocess.PIPE)
    return subprocess.run(
        [cc, '-fuse-ld=' + ld, '-o', '/dev/null', '-x', 'c', '-'],
        stdin=echo.stdout,
        stderr=subprocess.DEVNULL).returncode


def versioned_binaries(binary_name):
    """
    Returns a list of versioned binaries that may be used on Debian/Ubuntu
    :param binary_name: The name of the binary that we're checking for
    :return: List of versioned binaries
    """

    # There might be clang-7 to clang-11
    tot_llvm_ver = 11
    try:
        response = request.urlopen(
            'https://raw.githubusercontent.com/llvm/llvm-project/master/llvm/CMakeLists.txt'
        )
        to_parse = None
        data = response.readlines()
        for line in data:
            line = line.decode('utf-8').strip()
            if "set(LLVM_VERSION_MAJOR" in line:
                to_parse = line
                break
        tot_llvm_ver = re.search('\d+', to_parse).group(0)
    except URLError:
        pass
    return [
        '%s-%s' % (binary_name, i) for i in range(int(tot_llvm_ver), 6, -1)
    ]


def check_cc_ld_variables(root_folder):
    """
    Sets the cc, cxx, and ld variables, which will be passed to cmake
    :return: A tuple of valid cc, cxx, ld values that can be used to compile LLVM
    """
    utils.print_header("Checking CC and LD")
    cc, linker, ld = None, None, None
    # If the user specified a C compiler, get its full path
    if 'CC' in os.environ:
        cc = shutil.which(os.environ['CC'])
    # Otherwise, try to find one
    else:
        possible_compilers = versioned_binaries("clang") + ['clang', 'gcc']
        for compiler in possible_compilers:
            cc = shutil.which(compiler)
            if cc is not None:
                break
        if cc is None:
            raise RuntimeError(
                "Neither gcc nor clang could be found on your system!")

    # Evaluate if CC is a symlink. Certain packages of clang (like from
    # apt.llvm.org) symlink the clang++ binary to clang++-<version> in
    # /usr/bin, which then points to something like /usr/lib/llvm-<version/bin.
    # This won't be found by the dumb logic below and trying to parse and figure
    # out a heuristic for that is a lot more effort than just going into the
    # folder that clang is actually installed in and getting clang++ from there.
    cc = os.path.realpath(cc)
    cc_folder = os.path.dirname(cc)

    # If the user specified a C++ compiler, get its full path
    if 'CXX' in os.environ:
        cxx = shutil.which(os.environ['CXX'])
    # Otherwise, use the one where CC is
    else:
        if "clang" in cc:
            cxx = "clang++"
        else:
            cxx = "g++"
        cxx = shutil.which(cxx, path=cc_folder + ":" + os.environ['PATH'])
    cxx = cxx.strip()

    # If the user specified a linker
    if 'LD' in os.environ:
        # evaluate its full path with clang to avoid weird issues and check to
        # see if it will work with '-fuse-ld', which is what cmake will do. Doing
        # it now prevents a hard error later.
        ld = os.environ['LD']
        if "clang" in cc and clang_version(cc, root_folder) >= 30900:
            ld = shutil.which(ld)
        if linker_test(cc, ld):
            print("LD won't work with " + cc +
                  ", saving you from yourself by ignoring LD value")
            ld = None
    # If the user didn't specify a linker
    else:
        # and we're using clang, try to find the fastest one
        if "clang" in cc:
            possible_linkers = versioned_binaries("lld") + [
                'lld', 'gold', 'bfd'
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
            if clang_version(cc, root_folder) < 30900:
                os.environ['PATH'] = cc_folder + ":" + os.environ['PATH']
                ld = linker
        # and we're using gcc, try to use gold
        else:
            ld = "gold"
            if linker_test(cc, ld):
                ld = None

    # Print what binaries we are using to compile/link with so the user can
    # decide if that is proper or not
    print("CC: " + cc)
    print("CXX: " + cxx)
    if ld is not None:
        ld = ld.strip()
        ld_to_print = shutil.which("ld." + ld)
        if ld_to_print is None:
            ld_to_print = shutil.which(ld)
        print("LD: " + ld_to_print)

    return cc, cxx, ld


def check_dependencies():
    """
    Makes sure that the base dependencies of cmake, curl, git, and ninja are installed
    """
    utils.print_header("Checking dependencies")
    required_commands = ["cmake", "curl", "git", "ninja"]
    for command in required_commands:
        output = shutil.which(command)
        if output is None:
            raise RuntimeError(command +
                               " could not be found, please install it!")
        print(output)


def fetch_llvm_binutils(root_folder, update, shallow, ref):
    """
    Download llvm and binutils or update them if they exist
    :param root_folder: Working directory
    :param update: Boolean indicating whether sources need to be updated or not
    :param ref: The ref to checkout the monorepo to
    """
    p = root_folder.joinpath("llvm-project")
    cwd = p.as_posix()
    if p.is_dir():
        if update:
            utils.print_header("Updating LLVM")
            subprocess.run(["git", "fetch", "origin"], check=True, cwd=cwd)
            subprocess.run(["git", "checkout", ref], check=True, cwd=cwd)
            local_ref = None
            try:
                local_ref = subprocess.check_output(
                    ["git", "symbolic-ref", "-q", "HEAD"],
                    cwd=cwd).decode("utf-8")
            except subprocess.CalledProcessError:
                # This is thrown when we're on a revision that cannot be mapped to a symbolic reference, like a tag
                # or a git hash. Swallow and move on with the rest of our business.
                pass
            if local_ref and local_ref.startswith("refs/heads/"):
                # This is a branch, pull from remote
                subprocess.run([
                    "git", "pull", "--rebase", "origin",
                    local_ref.strip().replace("refs/heads/", "")
                ],
                               check=True,
                               cwd=cwd)
    else:
        extra_args = ("--depth", "1") if shallow else ()
        utils.print_header("Downloading LLVM")
        subprocess.run([
            "git", "clone", *extra_args, "git://github.com/llvm/llvm-project",
            p.as_posix()
        ],
                       check=True)
        subprocess.run(["git", "checkout", ref], check=True, cwd=cwd)

    # One might wonder why we are downloading binutils in an LLVM build script :)
    # We need it for the LLVMgold plugin, which can be used for LTO with ld.gold,
    # which at the time of writing this, is how the Google Pixel 3 kernel is built
    # and linked.
    utils.download_binutils(root_folder)


def cleanup(build_folder, incremental):
    """
    Clean up and create the build folder
    :param build_folder: The build directory
    :param incremental: Whether the build is incremental or not.
    :return:
    """
    if not incremental and build_folder.is_dir():
        shutil.rmtree(build_folder.as_posix())
    build_folder.mkdir(parents=True, exist_ok=True)


def get_final_stage(args):
    """
    Gets the final stage number, which depends on PGO or a stage one only build
    :param args: The args variable generated by parse_parameters
    :return: The final stage number
    """
    if args.build_stage1_only:
        return 1
    elif args.pgo:
        return 3
    else:
        return 2


def should_install_toolchain(args, stage):
    """
    Returns true if the just built toolchain should be installed
    :param args: The args variable generated by parse_parameters
    :param stage: What stage we are at
    :return: True when the toolchain should be installed; see function comments for more details
    """
    # We shouldn't install the toolchain if we are not on the final stage
    if stage != get_final_stage(args):
        return False

    # We shouldn't install the toolchain if the user is only building stage 1 build
    # and they didn't explicitly request an install
    if args.build_stage1_only and not args.install_stage1_only:
        return False

    # Otherwise, we should install the toolchain to the install folder
    return True


def bootstrap_stage(args, stage):
    """
    Returns true if we are doing a multistage build and on stage 1
    :param args: The args variable generated by parse_parameters
    :param stage: What stage we are at
    :return: True if doing a multistage build  and on stage 1, false if not
    """
    return not args.build_stage1_only and stage == 1


def instrumented_stage(args, stage):
    """
    Returns true if we are using PGO and on stage 2
    :param args: The args variable generated by parse_parameters
    :param stage: What stage we are at
    :return: True if using PGO and on stage 2, false if not
    """
    return args.pgo and stage == 2


def base_cmake_defines(dirs):
    """
    Generate base cmake defines, which will always be present, regardless of
    user input and stage
    :param dirs: An instance of the Directories class with the paths to use
    :return: A set of defines
    """
    # yapf: disable
    defines = {
        # Objective-C Automatic Reference Counting (we don't use Objective-C)
        # https://clang.llvm.org/docs/AutomaticReferenceCounting.html
        'CLANG_ENABLE_ARCMT': 'OFF',
        # We don't (currently) use the static analyzer and it saves cycles
        # according to Chromium OS:
        # https://crrev.com/44702077cc9b5185fc21e99485ee4f0507722f82
        'CLANG_ENABLE_STATIC_ANALYZER': 'OFF',
        # We don't use the plugin system and it will remove unused symbols:
        # https://crbug.com/917404
        'CLANG_PLUGIN_SUPPORT': 'OFF',
        # For LLVMgold.so, which is used for LTO with ld.gold
        'LLVM_BINUTILS_INCDIR': dirs.root_folder.joinpath(utils.current_binutils(), "include").as_posix(),
        # Don't build bindings; they are for other languages that the kernel does not use
        'LLVM_ENABLE_BINDINGS': 'OFF',
        # We need to enable LLVM plugin support so that LLVMgold.so is loadable
        'LLVM_ENABLE_PLUGINS': 'ON',
        # Don't build Ocaml documentation
        'LLVM_ENABLE_OCAMLDOC': 'OFF',
        # Removes system dependency on terminfo and almost every major clang provider turns this off
        'LLVM_ENABLE_TERMINFO': 'OFF',
        # Don't build clang-tools-extras to cut down on build targets (about 400 files or so)
        'LLVM_EXTERNAL_CLANG_TOOLS_EXTRA_SOURCE_DIR': '',
        # Don't include documentation build targets because it is available on the web
        'LLVM_INCLUDE_DOCS': 'OFF',
        # Don't include example build targets to save on cmake cycles
        'LLVM_INCLUDE_EXAMPLES': 'OFF',

    }
    # yapf: enable

    return defines


def get_stage1_binary(binary, dirs):
    """
    Generate a path from the stage 1 bin directory for the requested binary
    :param binary: Name of the binary
    :param dirs: An instance of the Directories class with the paths to use
    :return: A path suitable for a cmake define
    """
    return dirs.build_folder.joinpath("stage1", "bin", binary).as_posix()


def cc_ld_cmake_defines(dirs, env_vars, stage):
    """
    Generate compiler and linker cmake defines, which change depending on what
    stage we are at
    :param dirs: An instance of the Directories class with the paths to use
    :param env_vars: An instance of the EnvVars class with the compilers/linker to use
    :param stage: What stage we are at
    :return: A set of defines
    """
    defines = {}

    if stage == 1:
        ar = None
        cc = env_vars.cc
        clang_tblgen = None
        cxx = env_vars.cxx
        ld = env_vars.ld
        llvm_tblgen = None
        ranlib = None
    else:
        ar = get_stage1_binary("llvm-ar", dirs)
        cc = get_stage1_binary("clang", dirs)
        clang_tblgen = get_stage1_binary("clang-tblgen", dirs)
        cxx = get_stage1_binary("clang++", dirs)
        ld = get_stage1_binary("ld.lld", dirs)
        llvm_tblgen = get_stage1_binary("llvm-tblgen", dirs)
        ranlib = get_stage1_binary("llvm-ranlib", dirs)

    # Use llvm-ar for stage 2+ builds to avoid errors with bfd plugin
    # bfd plugin: LLVM gold plugin has failed to create LTO module: Unknown attribute kind (60) (Producer: 'LLVM9.0.0svn' Reader: 'LLVM 8.0.0')
    if ar:
        defines['CMAKE_AR'] = ar

    # The C compiler to use
    defines['CMAKE_C_COMPILER'] = cc

    if clang_tblgen:
        defines['CLANG_TABLEGEN'] = clang_tblgen

    # The C++ compiler to use
    defines['CMAKE_CXX_COMPILER'] = cxx

    # If we have a linker, use it
    if ld:
        defines['LLVM_USE_LINKER'] = ld

    if llvm_tblgen:
        defines['LLVM_TABLEGEN'] = llvm_tblgen

    # Use llvm-ranlib for stage 2+ builds
    if ranlib:
        defines['CMAKE_RANLIB'] = ranlib

    return defines


def project_target_cmake_defines(args, stage):
    """
    Generate project and target cmake defines, which change depending on what
    stage we are at
    :param args: The args variable generated by parse_parameters
    :param stage: What stage we are at
    :return: A set of defines
    """
    defines = {}

    if bootstrap_stage(args, stage):
        projects = "clang;lld"
        if args.pgo:
            projects += ';compiler-rt'
        targets = "host"
    else:
        if instrumented_stage(args, stage):
            projects = "clang;lld"
        else:
            projects = args.projects
        targets = args.targets

    # The projects to build
    defines['LLVM_ENABLE_PROJECTS'] = projects

    # The architectures to build backends for
    defines['LLVM_TARGETS_TO_BUILD'] = targets

    if "compiler-rt" in projects:
        # Don't build libfuzzer when compiler-rt is enabled, it invokes cmake again and we don't use it
        defines['COMPILER_RT_BUILD_LIBFUZZER'] = 'OFF'
        # We only use compiler-rt for the sanitizers, disable some extra stuff we don't need
        # Chromium OS also does this: https://crrev.com/c/1629950
        defines['COMPILER_RT_BUILD_BUILTINS'] = 'OFF'
        defines['COMPILER_RT_BUILD_CRT'] = 'OFF'
        defines['COMPILER_RT_BUILD_XRAY'] = 'OFF'
        # We don't need the sanitizers for the stage 1 bootstrap
        if bootstrap_stage(args, stage):
            defines['COMPILER_RT_BUILD_SANITIZERS'] = 'OFF'

    return defines


def stage_specific_cmake_defines(args, dirs, stage):
    """
    Generate other stage specific defines
    :param args: The args variable generated by parse_parameters
    :param dirs: An instance of the Directories class with the paths to use
    :param stage: What stage we are at
    :return: A set of defines
    """
    defines = {}

    # Use ccache for the stage 1 build as it will usually be done with a consistent
    # compiler and won't need a full rebuild very often
    if stage == 1 and not args.no_ccache and shutil.which("ccache"):
        defines['LLVM_CCACHE_BUILD'] = 'ON'

    if bootstrap_stage(args, stage):
        # Based on clang/cmake/caches/Apple-stage1.cmake
        defines['CMAKE_BUILD_TYPE'] = 'Release'
        defines['LLVM_ENABLE_BACKTRACES'] = 'OFF'
        defines['LLVM_ENABLE_WARNINGS'] = 'OFF'
        defines['LLVM_INCLUDE_TESTS'] = 'OFF'
        defines['LLVM_INCLUDE_UTILS'] = 'OFF'
    else:
        # https://llvm.org/docs/CMake.html#frequently-used-cmake-variables
        defines['CMAKE_BUILD_TYPE'] = args.build_type

        # We don't care about warnings if we are building a release build
        if args.build_type == "Release":
            defines['LLVM_ENABLE_WARNINGS'] = 'OFF'

        # Build with assertions enabled if requested (will slow down compilation
        # so it is not on by default)
        if args.assertions:
            defines['LLVM_ENABLE_ASSERTIONS'] = 'ON'

        # Where the toolchain should be installed
        defines['CMAKE_INSTALL_PREFIX'] = dirs.install_folder.as_posix()

        # Build with instrumentation if we are using PGO and on stage 2
        if instrumented_stage(args, stage):
            defines['LLVM_BUILD_INSTRUMENTED'] = 'IR'
            defines['LLVM_BUILD_RUNTIME'] = 'OFF'

        # If we are at the final stage, use PGO/Thin LTO if requested
        if stage == get_final_stage(args):
            if args.pgo:
                defines['LLVM_PROFDATA_FILE'] = dirs.build_folder.joinpath(
                    "profdata.prof").as_posix()
            if args.lto:
                defines['LLVM_ENABLE_LTO'] = args.lto.capitalize()

    return defines


def build_cmake_defines(args, dirs, env_vars, stage):
    """
    Generate cmake defines
    :param args: The args variable generated by parse_parameters
    :param dirs: An instance of the Directories class with the paths to use
    :param env_vars: An instance of the EnvVars class with the compilers/linker to use
    :param stage: What stage we are at
    :return: A set of defines
    """

    # Get base defines, which don't depend on any user inputs
    defines = base_cmake_defines(dirs)

    # Add compiler/linker defines, which change based on stage
    defines.update(cc_ld_cmake_defines(dirs, env_vars, stage))

    # Add project and target defines, which change based on stage
    defines.update(project_target_cmake_defines(args, stage))

    # Add other stage specific defines
    defines.update(stage_specific_cmake_defines(args, dirs, stage))

    # Add {-march,-mtune} flags if the user wants them
    if args.march:
        defines['CMAKE_C_FLAGS'] = '-march=%s -mtune=%s' % (args.march,
                                                            args.march)
        defines['CMAKE_CXX_FLAGS'] = '-march=%s -mtune=%s' % (args.march,
                                                              args.march)

    # Add the vendor string if necessary
    if args.clang_vendor:
        defines['CLANG_VENDOR'] = args.clang_vendor

    return defines


def invoke_cmake(args, dirs, env_vars, stage):
    """
    Invoke cmake to generate the build files
    :param args: The args variable generated by parse_parameters
    :param dirs: An instance of the Directories class with the paths to use
    :param env_vars: An instance of the EnvVars class with the compilers/linker to use
    :param stage: What stage we are at
    :return:
    """
    # Add the defines, point them to our build folder, and invoke cmake
    cmake = ['cmake', '-G', 'Ninja', '-Wno-dev']
    defines = build_cmake_defines(args, dirs, env_vars, stage)
    for key in defines:
        newdef = '-D' + key + '=' + defines[key]
        cmake += [newdef]
    cmake += [dirs.root_folder.joinpath("llvm-project", "llvm").as_posix()]

    cwd = dirs.build_folder.joinpath("stage%d" % stage).as_posix()

    utils.print_header("Configuring LLVM stage %d" % stage)

    subprocess.run(cmake, check=True, cwd=cwd)


def print_install_info(install_folder):
    """
    Prints out where the LLVM toolchain is installed and how to add to PATH
    :param install_folder: Where the LLVM toolchain is installed
    :return:
    """
    bin_folder = install_folder.joinpath("bin").as_posix()
    print("\nLLVM toolchain installed to: %s" % install_folder.as_posix())
    print("\nTo use, either run:\n")
    print("    $ export PATH=%s:${PATH}\n" % bin_folder)
    print("or add:\n")
    print("    PATH=%s:${PATH}\n" % bin_folder)
    print("to the command you want to use this toolchain.\n")


def invoke_ninja(args, dirs, stage):
    """
    Invoke ninja to run the actual build
    :param args: The args variable generated by parse_parameters
    :param dirs: An instance of the Directories class with the paths to use
    :param stage: The current stage we're building
    :return:
    """
    utils.print_header("Building LLVM stage %d" % stage)

    build_folder = dirs.build_folder.joinpath("stage%d" % stage)

    install_folder = None
    if should_install_toolchain(args, stage):
        install_folder = dirs.install_folder
    elif stage == 1 and args.build_stage1_only and not args.install_stage1_only:
        install_folder = build_folder

    build_folder = build_folder.as_posix()

    time_started = time.time()

    subprocess.run('ninja', check=True, cwd=build_folder)

    if args.check_targets and stage == get_final_stage(args):
        subprocess.run(['ninja'] +
                       ['check-%s' % s for s in args.check_targets],
                       check=True,
                       cwd=build_folder)

    print()
    print("LLVM build duration: " +
          str(datetime.timedelta(seconds=int(time.time() - time_started))))

    if should_install_toolchain(args, stage):
        subprocess.run(['ninja', 'install'],
                       check=True,
                       cwd=build_folder,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

        utils.create_gitignore(install_folder)

    if install_folder is not None:
        print_install_info(install_folder)


def generate_pgo_profiles(args, dirs):
    """
    Build a set of kernels across a few architectures to generate PGO profiles
    :param args: The args variable generated by parse_parameters
    :param dirs: An instance of the Directories class with the paths to use
    :return:
    """

    utils.print_header("Building PGO profiles")

    # Run kernel/build.sh
    subprocess.run([
        dirs.root_folder.joinpath("kernel", "build.sh"), '-b',
        dirs.build_folder, '-t', args.targets
    ],
                   check=True,
                   cwd=dirs.build_folder.as_posix())

    # Combine profiles
    subprocess.run([
        dirs.build_folder.joinpath("stage1", "bin", "llvm-profdata"), "merge",
        "-output=%s" % dirs.build_folder.joinpath("profdata.prof").as_posix()
    ] + glob.glob(
        dirs.build_folder.joinpath("stage2", "profiles",
                                   "*.profraw").as_posix()),
                   check=True)


def do_multistage_build(args, dirs, env_vars):
    stages = [1]

    if not args.build_stage1_only:
        stages += [2]
        if args.pgo:
            stages += [3]

    for stage in stages:
        dirs.build_folder.joinpath("stage%d" % stage).mkdir(parents=True,
                                                            exist_ok=True)
        invoke_cmake(args, dirs, env_vars, stage)
        invoke_ninja(args, dirs, stage)
        # Build profiles after stage 2 when using PGO
        if instrumented_stage(args, stage):
            generate_pgo_profiles(args, dirs)


def main():
    root_folder = pathlib.Path(__file__).resolve().parent

    args = parse_parameters(root_folder)

    build_folder = pathlib.Path(args.build_folder)
    if not build_folder.is_absolute():
        build_folder = root_folder.joinpath(build_folder)

    install_folder = pathlib.Path(args.install_folder)
    if not install_folder.is_absolute():
        install_folder = root_folder.joinpath(install_folder)

    env_vars = EnvVars(*check_cc_ld_variables(root_folder))
    check_dependencies()
    if args.use_good_revision:
        ref = GOOD_REVISION
    else:
        ref = args.branch
    fetch_llvm_binutils(root_folder, not args.no_update, args.shallow_clone,
                        ref)
    cleanup(build_folder, args.incremental)
    dirs = Directories(build_folder, install_folder, root_folder)
    do_multistage_build(args, dirs, env_vars)


if __name__ == '__main__':
    main()
