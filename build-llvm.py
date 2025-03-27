#!/usr/bin/env python3
# pylint: disable=invalid-name

from argparse import ArgumentParser, RawTextHelpFormatter
from pathlib import Path
import platform
import textwrap
import time

import tc_build.utils

from tc_build.llvm import LLVMBootstrapBuilder, LLVMBuilder, LLVMInstrumentedBuilder, LLVMSlimBuilder, LLVMSlimInstrumentedBuilder, LLVMSourceManager
from tc_build.kernel import KernelBuilder, LinuxSourceManager, LLVMKernelBuilder
from tc_build.tools import HostTools, StageTools

# This is a known good revision of LLVM for building the kernel
GOOD_REVISION = 'ed022d93b2fbfe52b7bdee786aa5cc49fa2323c4'

# The version of the Linux kernel that the script downloads if necessary
DEFAULT_KERNEL_FOR_PGO = (6, 14, 0)

parser = ArgumentParser(formatter_class=RawTextHelpFormatter)
clone_options = parser.add_mutually_exclusive_group()
opt_options = parser.add_mutually_exclusive_group()

parser.add_argument('--assertions',
                    help=textwrap.dedent('''\
                    In a release configuration, assertions are not enabled. Assertions can help catch
                    issues when compiling but it will increase compile times by 15-20%%.

                    '''),
                    action='store_true')
parser.add_argument('-b',
                    '--build-folder',
                    help=textwrap.dedent('''\
                    By default, the script will create a "build/llvm" folder in the same folder as this
                    script and build each requested stage within that containing folder. To change the
                    location of the containing build folder, pass it to this parameter. This can be either
                    an absolute or relative path.

                    '''),
                    type=str)
parser.add_argument('--build-targets',
                    default=['all'],
                    help=textwrap.dedent('''\
                    By default, the 'all' target is used as the build target for the final stage. With
                    this option, targets such as 'distribution' could be used to generate a slimmer
                    toolchain or targets such as 'clang' or 'llvm-ar' could be used to just test building
                    individual tools for a bisect.

                    NOTE: This only applies to the final stage build to avoid complicating tc-build internals.
                    '''),
                    nargs='+')
parser.add_argument('--bolt',
                    help=textwrap.dedent('''\
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

                    '''),
                    action='store_true')
opt_options.add_argument('--build-stage1-only',
                         help=textwrap.dedent('''\
                    By default, the script does a multi-stage build: it builds a more lightweight version of
                    LLVM first (stage 1) then uses that build to build the full toolchain (stage 2). This
                    is also known as bootstrapping.

                    This option avoids that, building the first stage as if it were the final stage. Note,
                    this option is more intended for quick testing and verification of issues and not regular
                    use. However, if your system is slow or can't handle 2+ stage builds, you may need this flag.

                         '''),
                         action='store_true')
# yapf: disable
parser.add_argument('--build-type',
                    metavar='BUILD_TYPE',
                    help=textwrap.dedent('''\
                    By default, the script does a Release build; Debug may be useful for tracking down
                    particularly nasty bugs.

                    See https://llvm.org/docs/GettingStarted.html#compiling-the-llvm-suite-source-code for
                    more information.

                    '''),
                    type=str,
                    choices=['Release', 'Debug', 'RelWithDebInfo', 'MinSizeRel'])
# yapf: enable
parser.add_argument('--check-targets',
                    help=textwrap.dedent('''\
                    By default, no testing is run on the toolchain. If you would like to run unit/regression
                    tests, use this parameter to specify a list of check targets to run with ninja. Common
                    ones include check-llvm, check-clang, and check-lld.

                    The values passed to this parameter will be automatically concatenated with 'check-'.

                    Example: '--check-targets clang llvm' will make ninja invokve 'check-clang' and 'check-llvm'.

                    '''),
                    nargs='+')
parser.add_argument('-D',
                    '--defines',
                    help=textwrap.dedent('''\
                    Specify additional cmake values. These will be applied to all cmake invocations.

                    Example: -D LLVM_PARALLEL_COMPILE_JOBS=2 LLVM_PARALLEL_LINK_JOBS=2

                    See https://llvm.org/docs/CMake.html for various cmake values. Note that some of
                    the options to this script correspond to cmake values.

                    '''),
                    nargs='+')
parser.add_argument('-f',
                    '--full-toolchain',
                    help=textwrap.dedent('''\
                    By default, the script tunes LLVM for building the Linux kernel by disabling several
                    projects, targets, and configuration options, which speeds up build times but limits
                    how the toolchain could be used.

                    With this option, all projects and targets are enabled and the script tries to avoid
                    unnecessarily turning off configuration options. The '--projects' and '--targets' options
                    to the script can still be used to change the list of projects and targets. This is
                    useful when using the script to do upstream LLVM development or trying to use LLVM as a
                    system-wide toolchain.

                    '''),
                    action='store_true')
parser.add_argument('-i',
                    '--install-folder',
                    help=textwrap.dedent('''\
                    By default, the script will leave the toolchain in its build folder. To install it
                    outside the build folder for persistent use, pass the installation location that you
                    desire to this parameter. This can be either an absolute or relative path.

                    '''),
                    type=str)
parser.add_argument('--install-targets',
                    help=textwrap.dedent('''\
                    By default, the script will just run the 'install' target to install the toolchain to
                    the desired prefix. To produce a slimmer toolchain, specify the desired targets to
                    install using this options.

                    The values passed to this parameter will be automatically prepended with 'install-'.

                    Example: '--install-targets clang lld' will make ninja invoke 'install-clang' and
                             'install-lld'.

                    '''),
                    nargs='+')
parser.add_argument('-l',
                    '--llvm-folder',
                    help=textwrap.dedent('''\
                    By default, the script will clone the llvm-project into the tc-build repo. If you have
                    another LLVM checkout that you would like to work out of, pass it to this parameter.
                    This can either be an absolute or relative path. Implies '--no-update'. When this
                    option is supplied, '--ref' and '--use-good-revison' do nothing, as the script does
                    not manipulate a repository it does not own.

                    '''),
                    type=str)
parser.add_argument('-L',
                    '--linux-folder',
                    help=textwrap.dedent('''\
                    If building with PGO, use this kernel source for building profiles instead of downloading
                    a tarball from kernel.org. This should be the full or relative path to a complete kernel
                    source directory, not a tarball or zip file.

                    '''),
                    type=str)
parser.add_argument('--lto',
                    metavar='LTO_TYPE',
                    help=textwrap.dedent('''\
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

                    '''),
                    type=str,
                    choices=['thin', 'full'])
parser.add_argument('-n',
                    '--no-update',
                    help=textwrap.dedent('''\
                    By default, the script always updates the LLVM repo before building. This prevents
                    that, which can be helpful during something like bisecting or manually managing the
                    repo to pin it to a particular revision.

                    '''),
                    action='store_true')
parser.add_argument('--no-ccache',
                    help=textwrap.dedent('''\
                    By default, the script adds LLVM_CCACHE_BUILD to the cmake options so that ccache is
                    used for the stage one build. This helps speed up compiles but it is only useful for
                    stage one, which is built using the host compiler, which usually does not change,
                    resulting in more cache hits. Subsequent stages will be always completely clean builds
                    since ccache will have no hits due to using a new compiler and it will unnecessarily
                    fill up the cache with files that will never be called again due to changing compilers
                    on the next build. This option prevents ccache from being used even at stage one, which
                    could be useful for benchmarking clean builds.

                    '''),
                    action='store_true')
parser.add_argument('-p',
                    '--projects',
                    help=textwrap.dedent('''\
                    Currently, the script only enables the clang, compiler-rt, lld, and polly folders in LLVM.
                    If you would like to override this, you can use this parameter and supply a list that is
                    supported by LLVM_ENABLE_PROJECTS.

                    See step #5 here: https://llvm.org/docs/GettingStarted.html#getting-started-quickly-a-summary

                    Example: -p clang lld polly

                    '''),
                    nargs='+')
opt_options.add_argument('--pgo',
                         metavar='PGO_BENCHMARK',
                         help=textwrap.dedent('''\
                    Build the final compiler with Profile Guided Optimization, which can often improve compile
                    time performance by 15-20%% on average. The script will:

                    1. Build a small bootstrap compiler like usual (stage 1).

                    2. Build an instrumented compiler with that compiler (stage 2).

                    3. Run the specified benchmark(s).

                       kernel-defconfig, kernel-allmodconfig, kernel-allyesconfig:

                       Download and extract kernel source from kernel.org (unless '--linux-folder' is
                       specified) and build some kernels based on the requested config with the instrumented
                       compiler (based on the '--targets' option). If there is a build error with one of the
                       kernels, build-llvm.py will fail as well.

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

                         '''),
                         nargs='+',
                         choices=[
                             'kernel-defconfig',
                             'kernel-allmodconfig',
                             'kernel-allyesconfig',
                             'kernel-defconfig-slim',
                             'kernel-allmodconfig-slim',
                             'kernel-allyesconfig-slim',
                             'llvm',
                         ])
parser.add_argument('--quiet-cmake',
                    help=textwrap.dedent('''\
                    By default, the script shows all output from cmake. When this option is enabled, the
                    invocations of cmake will only show warnings and errors.

                    '''),
                    action='store_true')
parser.add_argument('-r',
                    '--ref',
                    help=textwrap.dedent('''\
                    By default, the script builds the main branch (tip of tree) of LLVM. If you would
                    like to build an older branch, use this parameter. This may be helpful in tracking
                    down an older bug to properly bisect. This value is just passed along to 'git checkout'
                    so it can be a branch name, tag name, or hash (unless '--shallow-clone' is used, which
                    means a hash cannot be used because GitHub does not allow it). This will have no effect
                    if '--llvm-folder' is provided, as the script does not manipulate a repository that it
                    does not own.

                    '''),
                    default='main',
                    type=str)
clone_options.add_argument('-s',
                           '--shallow-clone',
                           help=textwrap.dedent('''\
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

                           '''),
                           action='store_true')
parser.add_argument('--show-build-commands',
                    help=textwrap.dedent('''\
                    By default, the script only shows the output of the comands it is running. When this option
                    is enabled, the invocations of cmake, ninja, and make will be shown to help with
                    reproducing issues outside of the script.

                    '''),
                    action='store_true')
parser.add_argument('-t',
                    '--targets',
                    help=textwrap.dedent('''\
                    LLVM is multitargeted by default. Currently, this script only enables the arm32, aarch64,
                    bpf, mips, powerpc, riscv, s390, and x86 backends because that's what the Linux kernel is
                    currently concerned with. If you would like to override this, you can use this parameter
                    and supply a list of targets supported by LLVM_TARGETS_TO_BUILD:

                    https://llvm.org/docs/CMake.html#llvm-specific-variables

                    Example: -t AArch64 ARM X86

                    '''),
                    nargs='+')
clone_options.add_argument('--use-good-revision',
                           help=textwrap.dedent('''\
                    By default, the script updates LLVM to the latest tip of tree revision, which may at times be
                    broken or not work right. With this option, it will checkout a known good revision of LLVM
                    that builds and works properly. If you use this option often, please remember to update the
                    script as the known good revision will change.

                    NOTE: This option cannot be used with '--shallow-clone'.

                           '''),
                           action='store_const',
                           const=GOOD_REVISION,
                           dest='ref')
parser.add_argument('--vendor-string',
                    help=textwrap.dedent('''\
                    Add this value to the clang and ld.lld version string (like "Apple clang version..."
                    or "Android clang version..."). Useful when reverting or applying patches on top
                    of upstream clang to differentiate a toolchain built with this script from
                    upstream clang or to distinguish a toolchain built with this script from the
                    system's clang. Defaults to ClangBuiltLinux, can be set to an empty string to
                    override this and have no vendor in the version string.

                    '''),
                    type=str,
                    default='ClangBuiltLinux')
args = parser.parse_args()

# Start tracking time that the script takes
script_start = time.time()

# Folder validation
tc_build_folder = Path(__file__).resolve().parent
src_folder = Path(tc_build_folder, 'src')

if args.build_folder:
    build_folder = Path(args.build_folder).resolve()
else:
    build_folder = Path(tc_build_folder, 'build/llvm')

# Validate and prepare Linux source if doing BOLT or PGO with kernel benchmarks
# Check for issues early, as these technologies are time consuming, so a user
# might step away from the build once it looks like it has started
if args.bolt or (args.pgo and [x for x in args.pgo if 'kernel' in x]):
    lsm = LinuxSourceManager()
    if args.linux_folder:
        if not (linux_folder := Path(args.linux_folder).resolve()).exists():
            raise RuntimeError(f"Provided Linux folder ('{args.linux_folder}') does not exist?")
        if not Path(linux_folder, 'Makefile').exists():
            raise RuntimeError(
                f"Provided Linux folder ('{args.linux_folder}') does not appear to be a Linux kernel tree?"
            )

        lsm.location = linux_folder

        # The kernel builder used by PGO below is written with a minimum
        # version in mind. If the user supplied their own Linux source, make
        # sure it is recent enough that the kernel builder will work.
        if (linux_version := lsm.get_version()) < KernelBuilder.MINIMUM_SUPPORTED_VERSION:
            found_version = '.'.join(map(str, linux_version))
            minimum_version = '.'.join(map(str, KernelBuilder.MINIMUM_SUPPORTED_VERSION))
            raise RuntimeError(
                f"Supplied kernel source version ('{found_version}') is older than the minimum required version ('{minimum_version}'), provide a newer version!"
            )
    else:
        # Turns (6, 2, 0) into 6.2 and (6, 2, 1) into 6.2.1 to follow tarball names
        ver_str = '.'.join(str(x) for x in DEFAULT_KERNEL_FOR_PGO if x)
        lsm.location = Path(src_folder, f"linux-{ver_str}")
        lsm.patches = list(src_folder.glob('*.patch'))

        lsm.tarball.base_download_url = 'https://cdn.kernel.org/pub/linux/kernel/v6.x'
        lsm.tarball.local_location = lsm.location.with_name(f"{lsm.location.name}.tar.xz")
        lsm.tarball.remote_checksum_name = 'sha256sums.asc'

        tc_build.utils.print_header('Preparing Linux source for profiling runs')
        lsm.prepare()

# Validate and configure LLVM source
if args.llvm_folder:
    if not (llvm_folder := Path(args.llvm_folder).resolve()).exists():
        raise RuntimeError(f"Provided LLVM folder ('{args.llvm_folder}') does not exist?")
else:
    llvm_folder = Path(src_folder, 'llvm-project')
llvm_source = LLVMSourceManager(llvm_folder)
llvm_source.download(args.ref, args.shallow_clone)
if not (args.llvm_folder or args.no_update):
    llvm_source.update(args.ref)

# Get host tools
tc_build.utils.print_header('Checking CC and LD')

host_tools = HostTools()
host_tools.show_compiler_linker()

# '--full-toolchain' affects all stages aside from the bootstrap stage so cache
# the class for all future initializations.
def_llvm_builder_cls = LLVMBuilder if args.full_toolchain else LLVMSlimBuilder

# Instantiate final builder to validate user supplied targets ahead of time, so
# that the user can correct the issue sooner rather than later.
final = def_llvm_builder_cls()
final.folders.source = llvm_folder
if args.targets:
    final.targets = args.targets
    final.validate_targets()
else:
    final.targets = ['all'] if args.full_toolchain else llvm_source.default_targets()

# Configure projects
if args.projects:
    final.projects = args.projects
elif args.full_toolchain:
    final.projects = ['all']
else:
    final.projects = llvm_source.default_projects()

# Warn the user of certain issues with BOLT and instrumentation
if args.bolt and not final.can_use_perf():
    warned = False
    has_4f158995b9cddae = Path(llvm_folder, 'bolt/lib/Passes/ValidateMemRefs.cpp').exists()
    if args.pgo and not args.assertions and not has_4f158995b9cddae:
        tc_build.utils.print_warning(
            'Using BOLT in instrumentation mode with PGO and no assertions might result in a binary that crashes:'
        )
        tc_build.utils.print_warning('https://github.com/llvm/llvm-project/issues/55004')
        tc_build.utils.print_warning(
            "Consider adding '--assertions' if there are any failures during the BOLT stage.")
        warned = True
    if platform.machine() != 'x86_64':
        tc_build.utils.print_warning(
            'Using BOLT in instrumentation mode may not work on non-x86_64 machines:')
        tc_build.utils.print_warning('https://github.com/llvm/llvm-project/issues/55005')
        tc_build.utils.print_warning(
            "Consider dropping '--bolt' if there are any failures during the BOLT stage.")
        warned = True
    if warned:
        tc_build.utils.print_warning('Continuing in 5 seconds, hit Ctrl-C to cancel...')
        time.sleep(5)

# Figure out unconditional cmake defines from input
common_cmake_defines = {}
if args.assertions:
    common_cmake_defines['LLVM_ENABLE_ASSERTIONS'] = 'ON'
if args.vendor_string:
    common_cmake_defines['CLANG_VENDOR'] = args.vendor_string
    common_cmake_defines['LLD_VENDOR'] = args.vendor_string
if args.defines:
    defines = dict(define.split('=', 1) for define in args.defines)
    common_cmake_defines.update(defines)

# Build bootstrap compiler if user did not request a single stage build
if (use_bootstrap := not args.build_stage1_only):
    tc_build.utils.print_header('Building LLVM (bootstrap)')

    bootstrap = LLVMBootstrapBuilder()
    bootstrap.build_targets = ['distribution']
    bootstrap.ccache = not args.no_ccache
    bootstrap.cmake_defines.update(common_cmake_defines)
    bootstrap.folders.build = Path(build_folder, 'bootstrap')
    bootstrap.folders.source = llvm_folder
    bootstrap.quiet_cmake = args.quiet_cmake
    bootstrap.show_commands = args.show_build_commands
    bootstrap.tools = host_tools
    if args.bolt:
        bootstrap.projects.append('bolt')
    if args.pgo:
        bootstrap.projects.append('compiler-rt')

    bootstrap.check_dependencies()
    bootstrap.configure()
    bootstrap.build()

# If the user did not specify CMAKE_C_FLAGS or CMAKE_CXX_FLAGS, add them as empty
# to paste stage 2 to ensure there are no environment issues (since CFLAGS and CXXFLAGS
# are taken into account by cmake)
c_flag_defines = ['CMAKE_C_FLAGS', 'CMAKE_CXX_FLAGS']
for define in c_flag_defines:
    if define not in common_cmake_defines:
        common_cmake_defines[define] = ''
# The user's build type should be taken into account past the bootstrap compiler
if args.build_type:
    common_cmake_defines['CMAKE_BUILD_TYPE'] = args.build_type

if args.pgo:
    if args.full_toolchain:
        instrumented = LLVMInstrumentedBuilder()
    else:
        instrumented = LLVMSlimInstrumentedBuilder()
    instrumented.build_targets = ['all' if args.full_toolchain else 'distribution']
    instrumented.cmake_defines.update(common_cmake_defines)
    # We run the tests on the instrumented stage if the LLVM benchmark was enabled
    instrumented.check_targets = args.check_targets if 'llvm' in args.pgo else None
    instrumented.folders.build = Path(build_folder, 'instrumented')
    instrumented.folders.source = llvm_folder
    instrumented.projects = final.projects
    instrumented.quiet_cmake = args.quiet_cmake
    instrumented.show_commands = args.show_build_commands
    instrumented.targets = final.targets
    instrumented.tools = StageTools(Path(bootstrap.folders.build, 'bin'))

    tc_build.utils.print_header('Building LLVM (instrumented)')
    instrumented.configure()
    instrumented.build()

    tc_build.utils.print_header('Generating PGO profiles')
    pgo_builders = []
    if 'llvm' in args.pgo:
        llvm_builder = def_llvm_builder_cls()
        llvm_builder.cmake_defines.update(common_cmake_defines)
        llvm_builder.folders.build = Path(build_folder, 'profiling')
        llvm_builder.folders.source = llvm_folder
        llvm_builder.projects = final.projects
        llvm_builder.quiet_cmake = args.quiet_cmake
        llvm_builder.show_commands = args.show_build_commands
        llvm_builder.targets = final.targets
        llvm_builder.tools = StageTools(Path(instrumented.folders.build, 'bin'))
        # clang-tblgen and llvm-tblgen may not be available from the
        # instrumented folder if the user did not pass '--full-toolchain', as
        # only the tools included in the distribution will be available. In
        # that case, use the bootstrap versions, which should not matter much
        # for profiling sake.
        if not args.full_toolchain:
            llvm_builder.tools.clang_tblgen = Path(bootstrap.folders.build, 'bin/clang-tblgen')
            llvm_builder.tools.llvm_tblgen = Path(bootstrap.folders.build, 'bin/llvm-tblgen')
        pgo_builders.append(llvm_builder)

    # If the user specified both a full and slim build of the same type, remove
    # the full build and warn them.
    pgo_targets = [s.replace('kernel-', '') for s in args.pgo if 'kernel-' in s]
    for pgo_target in pgo_targets:
        if 'slim' not in pgo_target:
            continue
        config_target = pgo_target.split('-')[0]
        if config_target in pgo_targets:
            tc_build.utils.print_warning(
                f"Both full and slim were specified for {config_target}, ignoring full...")
            pgo_targets.remove(config_target)

    if pgo_targets:
        kernel_builder = LLVMKernelBuilder()
        kernel_builder.folders.build = Path(build_folder, 'linux')
        kernel_builder.folders.source = lsm.location
        kernel_builder.toolchain_prefix = instrumented.folders.build
        for item in pgo_targets:
            pgo_target = item.split('-')

            config_target = pgo_target[0]
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
            if len(pgo_target) == 2:  # slim
                if instrumented.host_target_is_enabled():
                    llvm_targets = [instrumented.host_target()]
                else:
                    llvm_targets = final.targets[0:1]
            # full
            elif 'all' in final.targets:
                llvm_targets = llvm_source.default_targets()
            else:
                llvm_targets = final.targets

            kernel_builder.matrix[config_target] = llvm_targets

        pgo_builders.append(kernel_builder)

    for pgo_builder in pgo_builders:
        if hasattr(pgo_builder, 'configure') and callable(pgo_builder.configure):
            tc_build.utils.print_info('Building LLVM for profiling...')
            pgo_builder.configure()
        pgo_builder.build()

    instrumented.generate_profdata()

# Final build
final.build_targets = args.build_targets
final.check_targets = args.check_targets
final.cmake_defines.update(common_cmake_defines)
final.folders.build = Path(build_folder, 'final')
final.folders.install = Path(args.install_folder).resolve() if args.install_folder else None
final.install_targets = args.install_targets
final.quiet_cmake = args.quiet_cmake
final.show_commands = args.show_build_commands

if args.lto:
    final.cmake_defines['LLVM_ENABLE_LTO'] = args.lto.capitalize()
if args.pgo:
    final.cmake_defines['LLVM_PROFDATA_FILE'] = Path(instrumented.folders.build, 'profdata.prof')

if use_bootstrap:
    final.tools = StageTools(Path(bootstrap.folders.build, 'bin'))
else:
    # If we skipped bootstrapping, we need to check the dependencies now
    # and pass along certain user options
    final.check_dependencies()
    final.ccache = not args.no_ccache
    final.tools = host_tools

    # If the user requested BOLT but did not specify it in their projects nor
    # bootstrapped, we need to enable it to get the tools we need.
    if args.bolt:
        if not ('all' in final.projects or 'bolt' in final.projects):
            final.projects.append('bolt')
        final.tools.llvm_bolt = Path(final.folders.build, 'bin/llvm-bolt')
        final.tools.merge_fdata = Path(final.folders.build, 'bin/merge-fdata')
        final.tools.perf2bolt = Path(final.folders.build, 'bin/perf2bolt')

if args.bolt:
    final.bolt = True
    final.bolt_builder = LLVMKernelBuilder()
    final.bolt_builder.folders.build = Path(build_folder, 'linux')
    final.bolt_builder.folders.source = lsm.location
    if final.host_target_is_enabled():
        llvm_targets = [final.host_target()]
    else:
        llvm_targets = final.targets[0:1]
    final.bolt_builder.matrix['defconfig'] = llvm_targets

tc_build.utils.print_header('Building LLVM (final)')
final.configure()
final.build()
final.show_install_info()

print(f"Script duration: {tc_build.utils.get_duration(script_start)}")
