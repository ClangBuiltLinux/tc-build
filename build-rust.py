#!/usr/bin/env python3
# pylint: disable=invalid-name

from argparse import ArgumentParser, RawTextHelpFormatter
from pathlib import Path
import textwrap
import time

import tc_build.utils

from tc_build.rust import RustBuilder, RustSourceManager

# This is a known good revision of Rust for building the kernel
GOOD_REVISION = '69b3959afec9b5468d5de15133b199553f6e55d2'

parser = ArgumentParser(formatter_class=RawTextHelpFormatter)
clone_options = parser.add_mutually_exclusive_group()

parser.add_argument('--debug',
                    help=textwrap.dedent('''\
                    Build a debug compiler and standard library. This enables debug assertions,
                    debug logging, overflow checks and debug info. The debug assertions and overflow
                    checks can help catch issues when compiling.

                    '''),
                    action='store_true')
parser.add_argument('-b',
                    '--build-folder',
                    help=textwrap.dedent('''\
                    By default, the script will create a "build/rust" folder in the same folder as this
                    script and build each requested stage within that containing folder. To change the
                    location of the containing build folder, pass it to this parameter. This can be either
                    an absolute or relative path. If it is provided, then a custom LLVM install folder
                    needs to be provided as well to prevent mistakes.

                    '''),
                    type=str)
parser.add_argument('-i',
                    '--install-folder',
                    help=textwrap.dedent('''\
                    By default, the script will leave the toolchain in its build folder. To install it
                    outside the build folder for persistent use, pass the installation location that you
                    desire to this parameter. This can be either an absolute or relative path.

                    '''),
                    type=str)
parser.add_argument('-l',
                    '--llvm-install-folder',
                    help=textwrap.dedent('''\
                    By default, the script will try to use a built LLVM by './build-llvm.py'. To use
                    another LLVM installation (perhaps from './build-llvm.py --install-folder'), pass
                    it to this parameter.

                    '''),
                    type=str)
parser.add_argument('-R',
                    '--rust-folder',
                    help=textwrap.dedent('''\
                    By default, the script will clone the Rust project into the tc-build repo. If you have
                    another Rust checkout that you would like to work out of, pass it to this parameter.
                    This can either be an absolute or relative path. Implies '--no-update'. When this
                    option is supplied, '--ref' and '--use-good-revision' do nothing, as the script does
                    not manipulate a repository it does not own.

                    '''),
                    type=str)
parser.add_argument('-n',
                    '--no-update',
                    help=textwrap.dedent('''\
                    By default, the script always updates the Rust repo before building. This prevents
                    that, which can be helpful during something like bisecting or manually managing the
                    repo to pin it to a particular revision.

                    '''),
                    action='store_true')
parser.add_argument('-r',
                    '--ref',
                    help=textwrap.dedent('''\
                    By default, the script builds the main branch (tip of tree) of Rust. If you would
                    like to build an older branch, use this parameter. This may be helpful in tracking
                    down an older bug to properly bisect. This value is just passed along to 'git checkout'
                    so it can be a branch name, tag name, or hash. This will have no effect if
                    '--rust-folder' is provided, as the script does not manipulate a repository that it
                    does not own.

                    '''),
                    default='main',
                    type=str)
parser.add_argument('--show-build-commands',
                    help=textwrap.dedent('''\
                    By default, the script only shows the output of the comands it is running. When this option
                    is enabled, the invocations of the build tools will be shown to help with reproducing
                    issues outside of the script.

                    '''),
                    action='store_true')
clone_options.add_argument('--use-good-revision',
                           help=textwrap.dedent('''\
                    By default, the script updates Rust to the latest tip of tree revision, which may at times be
                    broken or not work right. With this option, it will checkout a known good revision of Rust
                    that builds and works properly. If you use this option often, please remember to update the
                    script as the known good revision will change. This option may work best with a matching good
                    revision used to build LLVM by './build-llvm.py'.

                           '''),
                           action='store_const',
                           const=GOOD_REVISION,
                           dest='ref')
parser.add_argument('--vendor-string',
                    help=textwrap.dedent('''\
                    Add this value to the Rust version string (like "rustc ... (ClangBuiltLinux)"). Useful when
                    reverting or applying patches on top of upstream Rust to differentiate a toolchain built
                    with this script from upstream Rust or to distinguish a toolchain built with this script
                    from the system's Rust. Defaults to ClangBuiltLinux, can be set to an empty string to
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

    if not args.llvm_install_folder:
        raise RuntimeError(
            'Build folder customized, but no custom LLVM install folder provided -- this is likely a mistake. Provide both if you want to build in a custom folder?'
        )
else:
    build_folder = Path(tc_build_folder, 'build/rust')

if args.llvm_install_folder:
    llvm_install_folder = Path(args.llvm_install_folder).resolve()
else:
    llvm_install_folder = Path(tc_build_folder, 'build/llvm/final')

# Validate and configure Rust source
if args.rust_folder:
    if not (rust_folder := Path(args.rust_folder).resolve()).exists():
        raise RuntimeError(f"Provided Rust folder ('{args.rust_folder}') does not exist?")
else:
    rust_folder = Path(src_folder, 'rust')
rust_source = RustSourceManager(rust_folder)
rust_source.download(args.ref)
if not (args.rust_folder or args.no_update):
    rust_source.update(args.ref)

# Build Rust
tc_build.utils.print_header('Building Rust')

# Final build
final = RustBuilder()
final.folders.source = rust_folder
final.folders.build = Path(build_folder, 'final')
final.folders.install = Path(args.install_folder).resolve() if args.install_folder else None
final.llvm_install_folder = llvm_install_folder
final.debug = args.debug
final.vendor_string = args.vendor_string
final.show_commands = args.show_build_commands

final.configure()
final.build()
final.show_install_info()

print(f"Script duration: {tc_build.utils.get_duration(script_start)}")
