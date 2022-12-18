#!/usr/bin/env python3
# pylint: disable=invalid-name
# Description: Builds an LLVM toolchain suitable for kernel development

import argparse
import datetime
import glob
import pathlib
import platform
import os
import subprocess
import shutil
import sys
import textwrap
import time
import re
from urllib import request
from urllib.error import URLError

import utils

# This is a known good revision of LLVM for building the kernel
GOOD_REVISION = '5351878ba1963a84600df3a9e907b458b0529851'


class Directories:

    def __init__(self, build_folder, install_folder, linux_folder, llvm_folder,
                 root_folder):
        self.build_folder = build_folder
        self.install_folder = install_folder
        self.linux_folder = linux_folder
        self.llvm_folder = llvm_folder
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
    command = [root_folder.joinpath("clang-version.sh"), cc]
    return int(subprocess.check_output(command).decode())


def parse_parameters(root_folder):
    """
    Parses parameters passed to the script into options
    :param root_folder: The directory where the script is being invoked from
    :return: A 'Namespace' object with all the options parsed from supplied parameters
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter)
    clone_options = parser.add_mutually_exclusive_group()
    opt_options = parser.add_mutually_exclusive_group()

    parser.add_argument("--assertions",
                        help=textwrap.dedent("""\
                        In a release configuration, assertions are not enabled. Assertions can help catch
                        issues when compiling but it will increase compile times by 15-20%%.

                        """),
                        action="store_true")
    parser.add_argument("-b",
                        "--branch",
                        help=textwrap.dedent("""\
                        By default, the script builds the main branch (tip of tree) of LLVM. If you would
                        like to build an older branch, use this parameter. This may be helpful in tracking
                        down an older bug to properly bisect. This value is just passed along to 'git checkout'
                        so it can be a branch name, tag name, or hash (unless '--shallow-clone' is used, which
                        means a hash cannot be used because GitHub does not allow it).

                        """),
                        type=str,
                        default="main")
    parser.add_argument("-B",
                        "--build-folder",
                        help=textwrap.dedent("""\
                        By default, the script will create a "build" folder in the same folder as this script,
                        then an "llvm" folder within that one and build the files there. If you would like
                        that done somewhere else, pass it to this parameter. This can either be an absolute
                        or relative path.

                        """),
                        type=str,
                        default=root_folder.joinpath("build", "llvm"))
    parser.add_argument("--bolt",
                        help=textwrap.dedent("""\
                        Optimize the final clang binary with BOLT (Binary Optimization and Layout Tool), which can
                        often improve compile time performance by 5-7%% on average.

                        This is similar to Profile Guided Optimization (PGO) but it happens against the final
                        binary that is built. The script will:

                        1. Figure out if perf can be used with branch sampling. You can test this ahead of time by
                           running:

                           $ perf record --branch-filter any,u --event cycles:u --output /dev/null -- sleep 1

                        2. If perf cannot be used, the clang binary will be instrumented by llvm-bolt, which will
                           result in a much slower clang binary.

                           NOTE #1: When this instrumentation is combined with a build of LLVM that has already
                                    been PGO'd (i.e., the '--pgo' flag) without LLVM's internal assertions (i.e.,
                                    no '--assertions' flag), there might be a crash when attempting to run the
                                    instrumented clang:

                                    https://github.com/llvm/llvm-project/issues/55004

                                    To avoid this, pass '--assertions' with '--bolt --pgo'.

                           NOTE #2: BOLT's instrumentation might not be compatible with architectures other than
                                    x86_64 and build-llvm.py's implementation has only been validated on x86_64
                                    machines:

                                    https://github.com/llvm/llvm-project/issues/55005

                                    BOLT itself only appears to support AArch64 and x86_64 as of LLVM commit
                                    a0b8ab1ba3165d468792cf0032fce274c7d624e1.

                        3. A kernel will be built and profiled. This will either be the host architecture's
                           defconfig or the first target's defconfig if '--targets' is specified without support
                           for the host architecture. The profiling data will be quite large, so it is imperative
                           that you have ample disk space and memory when attempting to do this. With instrumentation,
                           a profile will be generated for each invocation (PID) of clang, so this data could easily
                           be a couple hundred gigabytes large.

                        4. The clang binary will be optimized with BOLT using the profile generated above. This can
                           take some time.

                           NOTE #3: Versions of BOLT without commit 7d7771f34d14 ("[BOLT] Compact legacy profiles")
                                    will use significantly more memory during this stage if instrumentation is used
                                    because the merged profile is not as slim as it could be. Either upgrade to a
                                    version of LLVM that contains that change or pick it yourself, switch to perf if
                                    your machine supports it, upgrade the amount of memory you have (if possible),
                                    or run build-llvm.py without '--bolt'.

                        """),
                        action="store_true")
    opt_options.add_argument("--build-stage1-only",
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
                        system's clang. Defaults to ClangBuiltLinux, can be set to an empty string to
                        override this and have no vendor in the version string.

                        """),
                        type=str,
                        default="ClangBuiltLinux")
    parser.add_argument("-D",
                        "--defines",
                        help=textwrap.dedent("""\
                        Specify additional cmake values. These will be applied to all cmake invocations.

                        Example: -D LLVM_PARALLEL_COMPILE_JOBS=2 LLVM_PARALLEL_LINK_JOBS=2

                        See https://llvm.org/docs/CMake.html for various cmake values. Note that some of
                        the options to this script correspond to cmake values.

                        """),
                        nargs="+")
    parser.add_argument("-f",
                        "--full-toolchain",
                        help=textwrap.dedent("""\
                        By default, the script tunes LLVM for building the Linux kernel by disabling several
                        projects, targets, and configuration options, which speeds up build times but limits
                        how the toolchain could be used.

                        With this option, all projects and targets are enabled and the script tries to avoid
                        unnecessarily turning off configuration options. The '--projects' and '--targets' options
                        to the script can still be used to change the list of projects and targets. This is
                        useful when using the script to do upstream LLVM development or trying to use LLVM as a
                        system-wide toolchain.

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
                        By default, the script will create an "install" folder in the same folder as this script
                        and install the LLVM toolchain there. If you'd like to have it installed somewhere
                        else, pass it to this parameter. This can either be an absolute or relative path.

                        """),
                        type=str,
                        default=root_folder.joinpath("install"))
    parser.add_argument("--install-stage1-only",
                        help=textwrap.dedent("""\
                        When doing a stage 1 only build with '--build-stage1-only', install the toolchain to
                        the value of INSTALL_FOLDER.

                        """),
                        action="store_true")
    parser.add_argument("-l",
                        "--llvm-folder",
                        help=textwrap.dedent("""\
                        By default, the script will clone the llvm-project into the tc-build repo. If you have
                        another LLVM checkout that you would like to work out of, pass it to this parameter.
                        This can either be an absolute or relative path. Implies '--no-update'.

                        """),
                        type=str)
    parser.add_argument("-L",
                        "--linux-folder",
                        help=textwrap.dedent("""\
                        If building with PGO, use this kernel source for building profiles instead of downloading
                        a tarball from kernel.org. This should be the full or relative path to a complete kernel
                        source directory, not a tarball or zip file.

                        """),
                        type=str)
    parser.add_argument("--lto",
                        metavar="LTO_TYPE",
                        help=textwrap.dedent("""\
                        Build the final compiler with either ThinLTO (thin) or full LTO (full), which can
                        often improve compile time performance by 3-5%% on average.

                        Only use full LTO if you have more than 64 GB of memory. ThinLTO uses way less memory,
                        compiles faster because it is fully multithreaded, and it has almost identical
                        performance (within 1%% usually) to full LTO. The compile time impact of ThinLTO is about
                        5x the speed of a '--build-stage1-only' build and 3.5x the speed of a default build. LTO
                        is much worse and is not worth considering unless you have a server available to build on.

                        This option should not be used with '--build-stage1-only' unless you know that your
                        host compiler and linker support it. See the two links below for more information.

                        https://llvm.org/docs/LinkTimeOptimization.html
                        https://clang.llvm.org/docs/ThinLTO.html

                        """),
                        type=str,
                        choices=['thin', 'full'])
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
                        By default, the script adds LLVM_CCACHE_BUILD to the cmake options so that ccache is
                        used for the stage one build. This helps speed up compiles but it is only useful for
                        stage one, which is built using the host compiler, which usually does not change,
                        resulting in more cache hits. Subsequent stages will be always completely clean builds
                        since ccache will have no hits due to using a new compiler and it will unnecessarily
                        fill up the cache with files that will never be called again due to changing compilers
                        on the next build. This option prevents ccache from being used even at stage one, which
                        could be useful for benchmarking clean builds.

                        """),
                        action="store_true")
    parser.add_argument("-p",
                        "--projects",
                        help=textwrap.dedent("""\
                        Currently, the script only enables the clang, compiler-rt, lld, and polly folders in LLVM.
                        If you would like to override this, you can use this parameter and supply a list that is
                        supported by LLVM_ENABLE_PROJECTS.

                        See step #5 here: https://llvm.org/docs/GettingStarted.html#getting-started-quickly-a-summary

                        Example: -p \"clang;lld;libcxx\"

                        """),
                        type=str)
    opt_options.add_argument("--pgo",
                             metavar="PGO_BENCHMARK",
                             help=textwrap.dedent("""\
                        Build the final compiler with Profile Guided Optimization, which can often improve compile
                        time performance by 15-20%% on average. The script will:

                        1. Build a small bootstrap compiler like usual (stage 1).

                        2. Build an instrumented compiler with that compiler (stage 2).

                        3. Run the specified benchmark(s).

                           kernel-defconfig, kernel-allmodconfig, kernel-allyesconfig:

                           Download and extract kernel source from kernel.org (unless '--linux-folder' is
                           specified), build the necessary binutils if not found in PATH, and build some
                           kernels based on the requested config with the instrumented compiler (based on the
                           '--targets' option). If there is a build error with one of the kernels, build-llvm.py
                           will fail as well.

                           kernel-defconfig-slim, kernel-allmodconfig-slim, kernel-allyesconfig-slim:

                           Same as above but only one kernel will be built. If the host architecture is in the list
                           of targets, that architecture's requested config will be built; otherwise, the config of
                           the first architecture in '--targets' will be built. This will result in a less optimized
                           toolchain than the full variant above but it will result in less time spent profiling,
                           which means less build time overall. This might be worthwhile if you want to take advantage
                           of PGO on slower machines.

                           llvm:

                           The script will run the LLVM tests if they were requested via '--check-targets' then
                           build a full LLVM toolchain with the instrumented compiler.

                        4. Build a final compiler with the profile data generated from step 3 (stage 3).

                        Due to the nature of this process, '--build-stage1-only' cannot be used. There will be
                        three distinct LLVM build folders/compilers and several kernel builds done by default so
                        ensure that you have enough space on your disk to hold this (25GB should be enough) and the
                        time/patience to build three toolchains and kernels (will often take 5x the amount of time
                        as '--build-stage1-only' and 4x the amount of time as the default two-stage build that the
                        script does). When combined with '--lto', the compile time impact is about 9-10x of a one or
                        two stage builds.

                        See https://llvm.org/docs/HowToBuildWithPGO.html for more information.

                             """),
                             nargs="+",
                             choices=[
                                 'kernel-defconfig', 'kernel-allmodconfig',
                                 'kernel-allyesconfig',
                                 'kernel-defconfig-slim',
                                 'kernel-allmodconfig-slim',
                                 'kernel-allyesconfig-slim', 'llvm'
                             ])
    parser.add_argument("--quiet-cmake",
                        help=textwrap.dedent("""\
                        By default, the script shows all output from cmake. When this option is enabled, the
                        invocations of cmake will only show warnings and errors.

                        """),
                        action="store_true")
    clone_options.add_argument("-s",
                               "--shallow-clone",
                               help=textwrap.dedent("""\
                        Only fetch the required objects and omit history when cloning the LLVM repo. This
                        option is only used for the initial clone, not subsequent fetches. This can break
                        the script's ability to automatically update the repo to newer revisions or branches
                        so be careful using this. This option is really designed for continuous integration
                        runs, where a one off clone is necessary. A better option is usually managing the repo
                        yourself:

                        https://github.com/ClangBuiltLinux/tc-build#build-llvmpy

                        A couple of notes:

                        1. This cannot be used with '--use-good-revision'.

                        2. When no '--branch' is specified, only main is fetched. To work with other branches,
                           a branch other than main needs to be specified when the repo is first cloned.

                               """),
                               action="store_true")
    parser.add_argument("--show-build-commands",
                        help=textwrap.dedent("""\
                        By default, the script only shows the output of the comands it is running. When this option
                        is enabled, the invocations of cmake, ninja, and kernel/build.sh will be shown to help with
                        reproducing issues outside of the script.

                        """),
                        action="store_true")
    parser.add_argument("-t",
                        "--targets",
                        help=textwrap.dedent("""\
                        LLVM is multitargeted by default. Currently, this script only enables the arm32, aarch64,
                        bpf, mips, powerpc, riscv, s390, and x86 backends because that's what the Linux kernel is
                        currently concerned with. If you would like to override this, you can use this parameter
                        and supply a list that is supported by LLVM_TARGETS_TO_BUILD:

                        https://llvm.org/docs/CMake.html#llvm-specific-variables

                        Example: -t "AArch64;X86"

                        """),
                        type=str)
    clone_options.add_argument("--use-good-revision",
                               help=textwrap.dedent("""\
                        By default, the script updates LLVM to the latest tip of tree revision, which may at times be
                        broken or not work right. With this option, it will checkout a known good revision of LLVM
                        that builds and works properly. If you use this option often, please remember to update the
                        script as the known good revision will change.

                        NOTE: This option cannot be used with '--shallow-clone'.

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
    cc_cmd = [cc, f'-fuse-ld={ld}', '-o', '/dev/null', '-x', 'c', '-']

    try:
        subprocess.run(cc_cmd,
                       capture_output=True,
                       check=True,
                       input='int main() { return 0; }',
                       text=True)
    except subprocess.CalledProcessError:
        return False

    return True


def versioned_binaries(binary_name):
    """
    Returns a list of versioned binaries that may be used on Debian/Ubuntu
    :param binary_name: The name of the binary that we're checking for
    :return: List of versioned binaries
    """

    # There might be clang-7 to clang-16
    tot_llvm_ver = 16
    try:
        cmakelists_url = 'https://raw.githubusercontent.com/llvm/llvm-project/main/llvm/CMakeLists.txt'
        with request.urlopen(cmakelists_url) as response:
            data = response.readlines()

        for line in data:
            line = line.decode('utf-8').strip()
            if "set(LLVM_VERSION_MAJOR" in line:
                tot_llvm_ver = re.search(r'\d+', line).group(0)
                break
    except URLError:
        pass
    return [f'{binary_name}-{i}' for i in range(int(tot_llvm_ver), 6, -1)]


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
    cc = pathlib.Path(cc).resolve()
    cc_folder = cc.parent

    # If the user specified a C++ compiler, get its full path
    if 'CXX' in os.environ:
        cxx = shutil.which(os.environ['CXX'])
    # Otherwise, use the one where CC is
    else:
        if "clang" in cc.stem:
            cxx = "clang++"
        else:
            cxx = "g++"
        cxx = shutil.which(cxx, path=f"{cc_folder}:{os.environ['PATH']}")
    cxx = pathlib.Path(cxx.strip())

    # If the user specified a linker
    if 'LD' in os.environ:
        # evaluate its full path with clang to avoid weird issues and check to
        # see if it will work with '-fuse-ld', which is what cmake will do. Doing
        # it now prevents a hard error later.
        ld = os.environ['LD']
        if "clang" in cc.stem and clang_version(cc, root_folder) >= 30900:
            ld = shutil.which(ld)
        if linker_test(cc, ld):
            print(
                f"LD won't work with {cc}, saving you from yourself by ignoring LD value",
                flush=True)
            ld = None
    # If the user didn't specify a linker
    else:
        # and we're using clang, try to find the fastest one
        if "clang" in cc.stem:
            possible_linkers = ['lld', 'gold', 'bfd']
            for linker in possible_linkers:
                # We want to find lld wherever the clang we are using is located
                ld = shutil.which(f"ld.{linker}",
                                  path=f"{cc_folder}:{os.environ['PATH']}")
                if ld is not None:
                    break
            # If clang is older than 3.9, it won't accept absolute paths so we
            # need to just pass it the name (and modify PATH so that it is found properly)
            # https://github.com/llvm/llvm-project/commit/e43b7413597d8102a4412f9de41102e55f4f2ec9
            if clang_version(cc, root_folder) < 30900:
                os.environ['PATH'] = f"{cc_folder}:{os.environ['PATH']}"
                ld = linker
        # and we're using gcc, try to use gold
        else:
            ld = "gold"
            if linker_test(cc, ld):
                ld = None

    # Print what binaries we are using to compile/link with so the user can
    # decide if that is proper or not
    print(f"CC: {cc}")
    print(f"CXX: {cxx}")
    if ld is not None:
        ld = ld.strip()
        ld_to_print = shutil.which("ld.{ld}")
        if ld_to_print is None:
            ld_to_print = shutil.which(ld)
        print(f"LD: {ld_to_print}")
    utils.flush_std_err_out()

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
            raise RuntimeError(
                f"{command} could not be found, please install it!")
        print(output, flush=True)


def repo_is_shallow(repo):
    """
    Check if repo is a shallow clone already (looks for <repo>/.git/shallow)
    :param repo: The path to the repo to check
    :return: True if the repo is shallow, False if not
    """
    git_dir = subprocess.check_output(["git", "rev-parse", "--git-dir"],
                                      cwd=repo).decode().strip()
    return pathlib.Path(repo).resolve().joinpath(git_dir, "shallow").exists()


def ref_exists(repo, ref):
    """
    Check if ref exists using show-branch (works for branches, tags, and raw SHAs)
    :param repo: The path to the repo to check
    :param ref: The ref to check
    :return: True if ref exits, False if not
    """
    try:
        subprocess.run(["git", "show-branch", ref],
                       capture_output=True,
                       check=True,
                       cwd=repo)
    except subprocess.CalledProcessError:
        return False
    return True


def fetch_llvm(llvm_folder, update, shallow, ref):
    """
    Download llvm if it does not exist, update it if it does
    :param llvm_folder: llvm-project repo directory
    :param update: Boolean indicating whether sources need to be updated or not
    :param ref: The ref to checkout the monorepo to
    """
    if llvm_folder.is_dir():
        if update:
            utils.print_header("Updating LLVM")

            # Make sure repo is up to date before trying to see if checkout is possible
            subprocess.run(["git", "fetch", "origin"],
                           check=True,
                           cwd=llvm_folder)

            # Explain to the user how to avoid issues if their ref does not exist with
            # a shallow clone.
            if repo_is_shallow(llvm_folder) and not ref_exists(
                    llvm_folder, ref):
                utils.print_error(
                    f"\nSupplied ref ({ref}) does not exist, cannot checkout.")
                utils.print_error("To proceed, either:")
                utils.print_error(
                    "\t1. Manage the repo yourself and pass '--no-update' to the script."
                )
                utils.print_error(
                    f"\t2. Run 'git -C {llvm_folder} fetch --unshallow origin' to get a complete repository."
                )
                utils.print_error(
                    f"\t3. Delete '{llvm_folder}' and re-run the script with '-s' + '-b <ref>' to get a full set of refs."
                )
                sys.exit(1)

            # Do the update
            subprocess.run(["git", "checkout", ref],
                           check=True,
                           cwd=llvm_folder)
            local_ref = None
            try:
                local_ref = subprocess.check_output(
                    ["git", "symbolic-ref", "-q", "HEAD"],
                    cwd=llvm_folder).decode("utf-8")
            except subprocess.CalledProcessError:
                # This is thrown when we're on a revision that cannot be mapped to a symbolic reference, like a tag
                # or a git hash. Swallow and move on with the rest of our business.
                pass
            if local_ref and local_ref.startswith("refs/heads/"):
                # This is a branch, pull from remote
                git_pull_cmd = [
                    "git", "pull", "--rebase", "origin",
                    local_ref.strip().replace("refs/heads/", "")
                ]
                subprocess.run(git_pull_cmd, check=True, cwd=llvm_folder)
    else:
        utils.print_header("Downloading LLVM")

        extra_args = ()
        if shallow:
            extra_args = ("--depth", "1")
            if ref != "main":
                extra_args += ("--no-single-branch", )
        git_clone_cmd = [
            "git", "clone", *extra_args,
            "https://github.com/llvm/llvm-project", llvm_folder
        ]
        subprocess.run(git_clone_cmd, check=True)
        subprocess.run(["git", "checkout", ref], check=True, cwd=llvm_folder)


def cleanup(build_folder, incremental):
    """
    Clean up and create the build folder
    :param build_folder: The build directory
    :param incremental: Whether the build is incremental or not.
    :return:
    """
    if not incremental and build_folder.is_dir():
        shutil.rmtree(build_folder)
    build_folder.mkdir(parents=True, exist_ok=True)


def get_final_stage(args):
    """
    Gets the final stage number, which depends on PGO or a stage one only build
    :param args: The args variable generated by parse_parameters
    :return: The final stage number
    """
    if args.build_stage1_only:
        return 1
    if args.pgo:
        return 3
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


def pgo_stage(stage):
    """
    Returns true if LLVM is being built as a PGO benchmark
    :return: True if LLVM is being built as a PGO benchmark, false if not
    """
    return stage == "pgo"


def slim_cmake_defines():
    """
    Generate a set of cmake defines to slim down the LLVM toolchain
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
        # Don't build bindings; they are for other languages that the kernel does not use
        'LLVM_ENABLE_BINDINGS': 'OFF',
        # Don't build Ocaml documentation
        'LLVM_ENABLE_OCAMLDOC': 'OFF',
        # Don't build clang-tools-extras to cut down on build targets (about 400 files or so)
        'LLVM_EXTERNAL_CLANG_TOOLS_EXTRA_SOURCE_DIR': '',
        # Don't include documentation build targets because it is available on the web
        'LLVM_INCLUDE_DOCS': 'OFF',
        # Don't include example build targets to save on cmake cycles
        'LLVM_INCLUDE_EXAMPLES': 'OFF'
    }
    # yapf: enable

    return defines


def get_stage_binary(binary, dirs, stage):
    """
    Generate a path from the stage bin directory for the requested binary
    :param binary: Name of the binary
    :param dirs: An instance of the Directories class with the paths to use
    :param stage: The staged binary to use
    :return: A path suitable for a cmake define
    """
    return dirs.build_folder.joinpath(f"stage{stage}", "bin", binary)


def if_binary_exists(binary_name, cc):
    """
    Returns the path of the requested binary if it exists and clang is being used, None if not
    :param binary_name: Name of the binary
    :param cc: Path to CC binary
    :return: A path to binary if it exists and clang is being used, None if either condition is false
    """
    binary = None
    if "clang" in cc.stem:
        binary = shutil.which(binary_name,
                              path=f"{cc.parent}:{os.environ['PATH']}")
    return binary


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
        # Already figured out above
        cc = env_vars.cc
        cxx = env_vars.cxx
        ld = env_vars.ld
        # Optional to have
        ar = if_binary_exists("llvm-ar", cc)
        ranlib = if_binary_exists("llvm-ranlib", cc)
        # Cannot be used from host due to potential incompatibilities
        clang_tblgen = None
        llvm_tblgen = None
    else:
        if pgo_stage(stage):
            stage = 2
        else:
            stage = 1
        ar = get_stage_binary("llvm-ar", dirs, stage)
        cc = get_stage_binary("clang", dirs, stage)
        clang_tblgen = get_stage_binary("clang-tblgen", dirs, stage)
        cxx = get_stage_binary("clang++", dirs, stage)
        ld = get_stage_binary("ld.lld", dirs, stage)
        llvm_tblgen = get_stage_binary("llvm-tblgen", dirs, stage)
        ranlib = get_stage_binary("llvm-ranlib", dirs, stage)

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


def distro_cmake_defines():
    """
    Generate distribution specific cmake defines
    :return: A set of defines
    """
    defines = {}

    # Clear Linux needs a different target to find all of the C++ header files, otherwise
    # stage 2+ compiles will fail without this
    # We figure this out based on the existence of x86_64-generic-linux in the C++ headers path
    if glob.glob("/usr/include/c++/*/x86_64-generic-linux"):
        defines['LLVM_HOST_TRIPLE'] = "x86_64-generic-linux"

    # By default, the Linux triples are for glibc, which might not work on
    # musl-based systems. If clang is available, get the default target triple
    # from it so that clang without a '--target' flag always works.
    if shutil.which("clang"):
        clang_cmd = ["clang", "-print-target-triple"]
        clang_cmd_out = subprocess.run(clang_cmd,
                                       capture_output=True,
                                       check=True)
        default_target_triple = clang_cmd_out.stdout.decode('UTF-8').strip()
        defines['LLVM_DEFAULT_TARGET_TRIPLE'] = default_target_triple

    return defines


def project_cmake_defines(args, stage):
    """
    Generate lists of projects, depending on whether a full or
    kernel-focused LLVM build is being done and the stage
    :param args: The args variable generated by parse_parameters
    :param stage: What stage we are at
    :return: A set of defines
    """
    defines = {}

    if args.full_toolchain:
        if args.projects:
            projects = args.projects
        else:
            projects = "all"
    else:
        if bootstrap_stage(args, stage):
            projects = "clang;lld"
            if args.bolt:
                projects += ';bolt'
            if args.pgo:
                projects += ';compiler-rt'
        elif instrumented_stage(args, stage):
            projects = "clang;lld"
        elif args.projects:
            projects = args.projects
        else:
            projects = "clang;compiler-rt;lld;polly"

    # Add "bolt" in the list of projects if the user is doing a one stage build
    # and it is not already in the list
    if args.bolt and args.build_stage1_only and projects != "all" and "bolt" not in projects:
        projects += ";bolt"

    defines['LLVM_ENABLE_PROJECTS'] = projects

    if "compiler-rt" in projects:
        if not args.full_toolchain:
            # Don't build libfuzzer when compiler-rt is enabled, it invokes cmake again and we don't use it
            defines['COMPILER_RT_BUILD_LIBFUZZER'] = 'OFF'
            # We only use compiler-rt for the sanitizers, disable some extra stuff we don't need
            # Chromium OS also does this: https://crrev.com/c/1629950
            defines['COMPILER_RT_BUILD_CRT'] = 'OFF'
            defines['COMPILER_RT_BUILD_XRAY'] = 'OFF'
        # We don't need the sanitizers for the stage 1 bootstrap
        if bootstrap_stage(args, stage):
            defines['COMPILER_RT_BUILD_SANITIZERS'] = 'OFF'
        # execinfo.h might not exist (Alpine Linux) but the GWP ASAN library
        # depends on it. Disable the option to avoid breaking the build, the
        # kernel does not depend on it.
        if not pathlib.Path('/usr/include/execinfo.h').exists():
            defines['COMPILER_RT_BUILD_GWP_ASAN'] = 'OFF'

    return defines


def get_targets(args):
    """
    Gets the list of targets for cmake and kernel/build.sh
    :param args: The args variable generated by parse_parameters
    :return: A string of targets suitable for cmake or kernel/build.sh
    """
    if args.targets:
        targets = args.targets
    elif args.full_toolchain:
        targets = "all"
    else:
        targets = "AArch64;ARM;BPF;Hexagon;Mips;PowerPC;RISCV;SystemZ;X86"

    return targets


def target_cmake_defines(args, stage):
    """
    Generate target cmake define, which change depending on what
    stage we are at
    :param args: The args variable generated by parse_parameters
    :param stage: What stage we are at
    :return: A set of defines
    """
    defines = {}

    if bootstrap_stage(args, stage):
        targets = "host"
    else:
        targets = get_targets(args)

    defines['LLVM_TARGETS_TO_BUILD'] = targets

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
        defines.update(slim_cmake_defines())
        defines['CMAKE_BUILD_TYPE'] = 'Release'
        defines['LLVM_BUILD_UTILS'] = 'OFF'
        defines['LLVM_ENABLE_BACKTRACES'] = 'OFF'
        defines['LLVM_ENABLE_WARNINGS'] = 'OFF'
        defines['LLVM_INCLUDE_TESTS'] = 'OFF'
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
        defines['CMAKE_INSTALL_PREFIX'] = dirs.install_folder

        # Build with instrumentation if we are using PGO and on stage 2
        if instrumented_stage(args, stage):
            defines['LLVM_BUILD_INSTRUMENTED'] = 'IR'
            defines['LLVM_BUILD_RUNTIME'] = 'OFF'
            # The next two defines is needed to avoid thousands of warnings
            # along the lines of:
            # "Unable to track new values: Running out of static counters."
            defines['LLVM_LINK_LLVM_DYLIB'] = 'ON'
            defines['LLVM_VP_COUNTERS_PER_SITE'] = '6'

        # If we are at the final stage, use PGO/Thin LTO if requested
        if stage == get_final_stage(args):
            if args.pgo:
                defines['LLVM_PROFDATA_FILE'] = dirs.build_folder.joinpath(
                    "profdata.prof")
            if args.lto:
                defines['LLVM_ENABLE_LTO'] = args.lto.capitalize()
            # BOLT needs relocations for instrumentation
            if args.bolt:
                defines['CMAKE_EXE_LINKER_FLAGS'] = '-Wl,--emit-relocs'

        # If the user did not specify CMAKE_C_FLAGS or CMAKE_CXX_FLAGS, add them as empty
        # to paste stage 2 to ensure there are no environment issues (since CFLAGS and CXXFLAGS
        # are taken into account by cmake)
        keys = ['CMAKE_C_FLAGS', 'CMAKE_CXX_FLAGS']
        for key in keys:
            if key not in str(args.defines):
                defines[key] = ''

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
    defines = {}

    # Get slim defines if we are not building a full toolchain
    if not args.full_toolchain:
        defines.update(slim_cmake_defines())

    # Add compiler/linker defines, which change based on stage
    defines.update(cc_ld_cmake_defines(dirs, env_vars, stage))

    # Add distribution specific defines
    defines.update(distro_cmake_defines())

    # Add project and target defines, which change based on stage
    defines.update(project_cmake_defines(args, stage))
    defines.update(target_cmake_defines(args, stage))

    # Add other stage specific defines
    defines.update(stage_specific_cmake_defines(args, dirs, stage))

    # Add the vendor string if necessary
    if args.clang_vendor:
        defines['CLANG_VENDOR'] = args.clang_vendor

    # Removes system dependency on terminfo to keep the dynamic library dependencies slim
    defines['LLVM_ENABLE_TERMINFO'] = 'OFF'

    return defines


def show_command(args, command):
    """
    :param args: The args variable generated by parse_parameters
    :param command: The command being run
    """
    if args.show_build_commands:
        print(f"$ {' '.join([str(element) for element in command])}",
              flush=True)


def get_pgo_header_folder(stage):
    if pgo_stage(stage):
        header_string = "for PGO"
        sub_folder = "pgo"
    else:
        header_string = f"stage {stage}"
        sub_folder = f"stage{stage}"

    return (header_string, sub_folder)


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

    # Report only warnings and errors if running quietly.
    if args.quiet_cmake:
        cmake += ['--log-level=NOTICE']

    defines = build_cmake_defines(args, dirs, env_vars, stage)
    cmake += [f'-D{key}={val}' for key, val in defines.items()]
    if args.defines:
        cmake += [f'-D{d}' for d in args.defines]

    cmake += [dirs.llvm_folder.joinpath("llvm")]

    header_string, sub_folder = get_pgo_header_folder(stage)

    utils.print_header(f"Configuring LLVM {header_string}")

    show_command(args, cmake)
    subprocess.run(cmake,
                   check=True,
                   cwd=dirs.build_folder.joinpath(sub_folder))


def print_install_info(install_folder):
    """
    Prints out where the LLVM toolchain is installed, how to add to PATH, and version information
    :param install_folder: Where the LLVM toolchain is installed
    :return:
    """
    bin_folder = install_folder.joinpath("bin")
    print(f"\nLLVM toolchain installed to: {install_folder}")
    print("\nTo use, either run:\n")
    print(f"    $ export PATH={bin_folder}:$PATH\n")
    print("or add:\n")
    print(f"    PATH={bin_folder}:$PATH\n")
    print("to the command you want to use this toolchain.\n")

    clang = bin_folder.joinpath("clang")
    lld = bin_folder.joinpath("ld.lld")
    if clang.exists() or lld.exists():
        print("Version information:\n")
        for binary in [clang, lld]:
            if binary.exists():
                subprocess.run([binary, "--version"], check=True)
                print()
    utils.flush_std_err_out()


def ninja_check(args, build_folder):
    """
    Invoke ninja with check targets if they are present
    :param args: The args variable generated by parse_parameters
    :param build_folder: The build folder that ninja should be run in
    :return:
    """
    if args.check_targets:
        # yapf really messes the look of this up so split it into two assignments.
        ninja_check_cmd = ['ninja']
        ninja_check_cmd += [f'check-{s}' for s in args.check_targets]
        show_command(args, ninja_check_cmd)
        subprocess.run(ninja_check_cmd, check=True, cwd=build_folder)


def invoke_ninja(args, dirs, stage):
    """
    Invoke ninja to run the actual build
    :param args: The args variable generated by parse_parameters
    :param dirs: An instance of the Directories class with the paths to use
    :param stage: The current stage we're building
    :return:
    """
    header_string, sub_folder = get_pgo_header_folder(stage)

    utils.print_header(f"Building LLVM {header_string}")

    build_folder = dirs.build_folder.joinpath(sub_folder)

    install_folder = None
    if should_install_toolchain(args, stage):
        install_folder = dirs.install_folder
    elif stage == 1 and args.build_stage1_only and not args.install_stage1_only:
        install_folder = build_folder

    time_started = time.time()

    show_command(args, ["ninja"])
    subprocess.run('ninja', check=True, cwd=build_folder)

    if stage == get_final_stage(args):
        ninja_check(args, build_folder)

    print()
    time_string = str(
        datetime.timedelta(seconds=int(time.time() - time_started)))
    print(f"LLVM build duration: {time_string}")
    utils.flush_std_err_out()

    if should_install_toolchain(args, stage):
        subprocess.run(['ninja', 'install'],
                       check=True,
                       cwd=build_folder,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

        utils.create_gitignore(install_folder)

        if args.bolt:
            do_bolt(args, dirs)

    if install_folder is not None:
        print_install_info(install_folder)


def get_host_llvm_target():
    """
    Get the host's LLVM target based on 'uname -m'
    :return: Host's LLVM target
    """
    host_mapping = {
        "aarch64": "AArch64",
        "armv7l": "ARM",
        "i386": "X86",
        "mips": "Mips",
        "mips64": "Mips",
        "ppc": "PowerPC",
        "ppc64": "PowerPC",
        "ppc64le": "PowerPC",
        "riscv32": "RISCV",
        "riscv64": "RISCV",
        "s390x": "SystemZ",
        "x86_64": "X86"
    }
    return host_mapping.get(platform.machine())


def kernel_build_sh(args, config, dirs, profile_type):
    """
    Run kernel/build.sh to generate PGO or BOLT profiles
    :param args: The args variable generated by parse_parameters
    :param config: The config to build (defconfig, allmodconfig, allyesconfig)
    :param dirs: An instance of the Directories class with the paths to use
    :param profile_type: The type of profile we are building (bolt-instrumentation, bolt-sampling, pgo, or pgo-slim)
    :return:
    """

    # Run kernel/build.sh
    build_sh = [
        dirs.root_folder.joinpath("kernel", "build.sh"), f'--{profile_type}'
    ]

    targets = get_targets(args)
    if "bolt" in profile_type or "slim" in profile_type:
        # For BOLT or "slim" PGO, we limit the number of kernels we build for
        # each mode:
        #
        # When using perf, building too many kernels will generate a gigantic
        # perf profile. perf2bolt calls 'perf script', which will load the
        # entire profile into memory, which could cause OOM for most machines
        # and long processing times for the ones that can handle it for little
        # extra gain.
        #
        # With BOLT instrumentation, we generate one profile file for each
        # invocation of clang (PID) to avoid profiling just the driver, so
        # building multiple kernels will generate a few hundred gigabytes of
        # fdata files.
        #
        # Just do a native build if the host target is in the list of targets
        # or the first target if not.
        host_target = get_host_llvm_target()
        if targets == "all" or host_target in targets:
            targets = host_target
        else:
            targets = targets.split(";")[0]

    build_sh += ['-t', targets]

    if profile_type == "bolt-sampling":
        build_sh = [
            "perf", "record", "--branch-filter", "any,u", "--event",
            "cycles:u", "--output",
            dirs.build_folder.joinpath("perf.data"), "--"
        ] + build_sh

    if "bolt" in profile_type:
        build_sh += ['-i', dirs.install_folder]
    if "pgo" in profile_type:
        build_sh += ['-b', dirs.build_folder]

    if config != "defconfig":
        build_sh += [f'--{config}']

    if dirs.linux_folder:
        build_sh += ['-k', dirs.linux_folder]

    show_command(args, build_sh)
    subprocess.run(build_sh, check=True, cwd=dirs.build_folder)


def pgo_llvm_build(args, dirs):
    """
    Builds LLVM as a PGO benchmark
    :param args: The args variable generated by parse_parameters
    :param dirs: An instance of the Directories class with the paths to use
    :return:
    """
    # Run check targets if the user requested them for PGO coverage
    ninja_check(args, dirs.build_folder.joinpath("stage2"))
    # Then, build LLVM as if it were the full final toolchain
    stage = "pgo"
    dirs.build_folder.joinpath(stage).mkdir(parents=True, exist_ok=True)
    invoke_cmake(args, dirs, None, stage)
    invoke_ninja(args, dirs, stage)


def generate_pgo_profiles(args, dirs):
    """
    Build a set of kernels across a few architectures to generate PGO profiles
    :param args: The args variable generated by parse_parameters
    :param dirs: An instance of the Directories class with the paths to use
    :return:
    """

    utils.print_header("Building PGO profiles")

    # Run PGO benchmarks
    for pgo in args.pgo:
        pgo = pgo.split("-")
        pgo_target = pgo[0]
        if pgo_target == "kernel":
            pgo_config = pgo[1]
            # Handle "kernel-defconfig-slim"
            if len(pgo) == 3:
                pgo_type = "pgo-slim"
            else:
                pgo_type = "pgo"
            kernel_build_sh(args, pgo_config, dirs, pgo_type)
        if pgo_target == "llvm":
            pgo_llvm_build(args, dirs)

    # Combine profiles
    llvm_prof_data_cmd = [
        dirs.build_folder.joinpath("stage1", "bin", "llvm-profdata"), "merge",
        f'-output={dirs.build_folder.joinpath("profdata.prof")}'
    ] + list(
        dirs.build_folder.joinpath("stage2", "profiles").glob("*.profraw"))
    subprocess.run(llvm_prof_data_cmd, check=True)


def do_multistage_build(args, dirs, env_vars):
    stages = [1]

    if not args.build_stage1_only:
        stages += [2]
        if args.pgo:
            stages += [3]

    for stage in stages:
        dirs.build_folder.joinpath(f"stage{stage}").mkdir(parents=True,
                                                          exist_ok=True)
        invoke_cmake(args, dirs, env_vars, stage)
        invoke_ninja(args, dirs, stage)
        # Build profiles after stage 2 when using PGO
        if instrumented_stage(args, stage):
            generate_pgo_profiles(args, dirs)


def can_use_perf():
    """
    Checks if perf can be used for branch sampling with BOLT
    :return: True if perf can be used for branch sampling with BOLT, false if not
    """
    # Make sure perf is in the environment
    if shutil.which("perf"):
        try:
            perf_cmd = [
                "perf", "record", "--branch-filter", "any,u", "--event",
                "cycles:u", "--output", "/dev/null", "--", "sleep", "1"
            ]
            subprocess.run(perf_cmd,
                           stderr=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL,
                           check=True)
        except subprocess.CalledProcessError:
            pass
        else:
            return True

    return False


def do_bolt(args, dirs):
    # Default to instrumentation, as it should be universally available.
    mode = "instrumentation"

    # If we can use perf for branch sampling, we switch to that mode, as
    # it is much quicker and it can result in more performance gains
    if can_use_perf():
        mode = "sampling"

    utils.print_header(f"Performing BOLT with {mode}")

    # clang-#: original binary
    # clang.bolt: BOLT optimized binary
    # .bolt will become original binary after optimization
    clang = dirs.install_folder.joinpath("bin", "clang").resolve()
    clang_bolt = clang.with_name("clang.bolt")

    llvm_bolt = dirs.build_folder.joinpath("stage1", "bin", "llvm-bolt")
    bolt_profile = dirs.build_folder.joinpath("clang.fdata")

    if mode == "instrumentation":
        # clang.inst: instrumented binary, will be removed after generating profiles
        clang_inst = clang.with_name("clang.inst")

        # Remove profile if it exists (incremental builds). The missing_ok
        # parameter to unlink() was only introduced in Python 3.8 but we try to
        # support older versions when possible.
        try:
            bolt_profile.unlink()
        except FileNotFoundError:
            pass

        # Instrument clang
        clang_inst_cmd = [
            llvm_bolt, "--instrument",
            f"--instrumentation-file={bolt_profile}",
            "--instrumentation-file-append-pid", "-o", clang_inst, clang
        ]
        show_command(args, clang_inst_cmd)
        subprocess.run(clang_inst_cmd, check=True)

    # Generate profile data by using clang to build kernels
    kernel_build_sh(args, "defconfig", dirs, f"bolt-{mode}")

    # With instrumentation, we need to combine the profiles we generated, as
    # they are separated by PID
    if mode == "instrumentation":
        merge_fdata = dirs.build_folder.joinpath("stage1", "bin",
                                                 "merge-fdata")
        fdata_files = glob.glob(f"{bolt_profile}.*.fdata")

        # merge-fdata will print one line for each .fdata it merges. Redirect
        # the output to a log file in case it ever needs to be inspected
        merge_fdata_log = dirs.build_folder.joinpath("merge-fdata.log")

        with open(bolt_profile, "w", encoding='utf-8') as out_f, \
             open(merge_fdata_log, "w", encoding='utf-8') as err_f:
            # We don't use show_command() here because of how long the command
            # will be.
            print("Merging .fdata files, this might take a while...",
                  flush=True)
            subprocess.run([merge_fdata] + fdata_files,
                           stdout=out_f,
                           stderr=err_f,
                           check=True)

        for fdata_file in fdata_files:
            os.remove(fdata_file)

    # If we generated perf data, we need to convert it into something BOLT can
    # understand. This is already done with instrumentation.
    if mode == "sampling":
        perf2bolt = dirs.build_folder.joinpath("stage1", "bin", "perf2bolt")
        perf_data = dirs.build_folder.joinpath("perf.data")

        perf2bolt_cmd = [perf2bolt, "-p", perf_data, "-o", bolt_profile, clang]
        show_command(args, perf2bolt_cmd)
        subprocess.run(perf2bolt_cmd, check=True)

        try:
            perf_data.unlink()
        except FileNotFoundError:
            pass

    # Generate BOLT optimized clang
    # Flags are from https://github.com/llvm/llvm-project/blob/2696d82fa0c323d92d8794f0a34ea9619888fae9/bolt/docs/OptimizingClang.md
    clang_opt_cmd = [
        llvm_bolt, f"--data={bolt_profile}", "--reorder-blocks=cache+",
        "--reorder-functions=hfsort+", "--split-functions=3",
        "--split-all-cold", "--dyno-stats", "--icf=1", "--use-gnu-stack", "-o",
        clang_bolt, clang
    ]
    show_command(args, clang_opt_cmd)
    subprocess.run(clang_opt_cmd, check=True)

    # Move BOLT optimized clang into place and remove instrumented clang
    shutil.move(clang_bolt, clang)
    if mode == "instrumentation":
        clang_inst.unlink()


# https://github.com/llvm/llvm-project/commit/4f158995b9cddae392bfb5989af8c83101ae0789
def has_4f158995b9cddae(llvm_folder):
    return llvm_folder.joinpath("bolt", "lib", "Passes",
                                "ValidateMemRefs.cpp").exists()


def main():
    root_folder = pathlib.Path(__file__).resolve().parent

    args = parse_parameters(root_folder)

    build_folder = pathlib.Path(args.build_folder).resolve()
    install_folder = pathlib.Path(args.install_folder).resolve()

    linux_folder = None
    if args.linux_folder:
        linux_folder = pathlib.Path(args.linux_folder).resolve()
        if not linux_folder.exists():
            utils.print_error(
                f"\nSupplied kernel source ({linux_folder}) does not exist!")
            sys.exit(1)

    if args.llvm_folder:
        llvm_folder = pathlib.Path(args.llvm_folder).resolve()
        if not llvm_folder.exists():
            utils.print_error(
                f"\nSupplied LLVM source ({llvm_folder}) does not exist!")
            sys.exit(1)
    else:
        llvm_folder = root_folder.joinpath("llvm-project")

    # There are a couple of known issues with BOLT in instrumentation mode:
    # https://github.com/llvm/llvm-project/issues/55004
    # https://github.com/llvm/llvm-project/issues/55005
    # Warn the user about them
    if args.bolt and not can_use_perf():
        warn = False

        if args.pgo and not args.assertions and not has_4f158995b9cddae(
                llvm_folder):
            utils.print_warning(
                "\nUsing BOLT in instrumentation mode with PGO and no assertions might result in a binary that crashes:"
            )
            utils.print_warning(
                "https://github.com/llvm/llvm-project/issues/55004")
            utils.print_warning(
                "Consider adding '--assertions' if there are any failures during the BOLT stage."
            )
            warn = True

        if platform.machine() != "x86_64":
            utils.print_warning(
                "\nUsing BOLT in instrumentation mode may not work on non-x86_64 machines:"
            )
            utils.print_warning(
                "https://github.com/llvm/llvm-project/issues/55005")
            utils.print_warning(
                "Consider dropping '--bolt' if there are any failures during the BOLT stage."
            )
            warn = True

        if warn:
            utils.print_warning(
                "Continuing in 5 seconds, hit Ctrl-C to cancel...")
            time.sleep(5)

    env_vars = EnvVars(*check_cc_ld_variables(root_folder))
    check_dependencies()
    if args.use_good_revision:
        ref = GOOD_REVISION
    else:
        ref = args.branch

    if not args.llvm_folder:
        fetch_llvm(llvm_folder, not args.no_update, args.shallow_clone, ref)
    cleanup(build_folder, args.incremental)
    dirs = Directories(build_folder, install_folder, linux_folder, llvm_folder,
                       root_folder)
    do_multistage_build(args, dirs, env_vars)


if __name__ == '__main__':
    main()
