#!/usr/bin/env python3
# pylint: disable=invalid-name

from argparse import ArgumentParser
from pathlib import Path
import time

import tc_build.binutils
import tc_build.utils

LATEST_BINUTILS_RELEASE = (2, 42, 0)

parser = ArgumentParser()
parser.add_argument('-B',
                    '--binutils-folder',
                    help='''
                    By default, the script will download a copy of the binutils source in the src folder within
                    the same folder as this script. If you have your own copy of the binutils source that you
                    would like to build from, pass it to this parameter. It can be either an absolute or
                    relative path.
                    ''',
                    type=str)
parser.add_argument('-b',
                    '--build-folder',
                    help='''
                    By default, the script will create a "build/binutils" folder in the same folder as this
                    script then build each target in its own folder within that containing folder. If you
                    would like the containing build folder to be somewhere else, pass it to this parameter.
                    that done somewhere else, pass it to this parameter. It can be either an absolute or
                    relative path.
                    ''',
                    type=str)
parser.add_argument('-i',
                    '--install-folder',
                    help='''
                    By default, the script will build binutils but stop before installing it. To install
                    them into a prefix, pass it to this parameter. This can be either an absolute or
                    relative path.
                    ''',
                    type=str)
parser.add_argument('-m',
                    '--march',
                    metavar='ARCH',
                    help='''
                    Add -march=ARCH to CFLAGS to optimize the toolchain for the processor that it will be
                    running on.
                    ''',
                    type=str)
parser.add_argument('--show-build-commands',
                    help='''
                    By default, the script only shows the output of the comands it is running. When this option
                    is enabled, the invocations of configure and make will be shown to help with reproducing
                    issues outside of the script.
                    ''',
                    action='store_true')
parser.add_argument('-t',
                    '--targets',
                    help='''
                    The script can build binutils targeting arm-linux-gnueabi, aarch64-linux-gnu,
                    mips-linux-gnu, mipsel-linux-gnu, powerpc-linux-gnu, powerpc64-linux-gnu,
                    powerpc64le-linux-gnu, riscv64-linux-gnu, s390x-linux-gnu, and x86_64-linux-gnu.

                    By default, it builds all supported targets ("all"). If you would like to build
                    specific targets only, pass them to this script. It can be either the full target
                    or just the first part (arm, aarch64, x86_64, etc).
                    ''',
                    nargs='+')
args = parser.parse_args()

script_start = time.time()

tc_build_folder = Path(__file__).resolve().parent

bsm = tc_build.binutils.BinutilsSourceManager()
if args.binutils_folder:
    bsm.location = Path(args.binutils_folder).resolve()
    if not bsm.location.exists():
        raise RuntimeError(f"Provided binutils source ('{bsm.location}') does not exist?")
else:
    # Turns (2, 40, 0) into 2.40 and (2, 40, 1) into 2.40.1 to follow tarball names
    folder_name = 'binutils-' + '.'.join(str(x) for x in LATEST_BINUTILS_RELEASE if x)

    bsm.location = Path(tc_build_folder, 'src', folder_name)
    bsm.tarball.base_download_url = 'https://sourceware.org/pub/binutils/releases'
    bsm.tarball.local_location = bsm.location.with_name(f"{folder_name}.tar.xz")
    bsm.tarball_remote_checksum_name = 'sha512.sum'
    bsm.prepare()

if args.build_folder:
    build_folder = Path(args.build_folder).resolve()
else:
    build_folder = Path(tc_build_folder, 'build/binutils')

default_targets = bsm.default_targets()
if args.targets:
    targets = default_targets if 'all' in args.targets else set(args.targets)
else:
    targets = default_targets

targets_to_builder = {
    'arm': tc_build.binutils.ArmBinutilsBuilder,
    'aarch64': tc_build.binutils.AArch64BinutilsBuilder,
    'mips': tc_build.binutils.MipsBinutilsBuilder,
    'mipsel': tc_build.binutils.MipselBinutilsBuilder,
    'powerpc': tc_build.binutils.PowerPCBinutilsBuilder,
    'powerpc64': tc_build.binutils.PowerPC64BinutilsBuilder,
    'powerpc64le': tc_build.binutils.PowerPC64LEBinutilsBuilder,
    'riscv64': tc_build.binutils.RISCV64BinutilsBuilder,
    's390x': tc_build.binutils.S390XBinutilsBuilder,
    'x86_64': tc_build.binutils.X8664BinutilsBuilder,
}
if 'loongarch64' in default_targets:
    targets_to_builder['loongarch64'] = tc_build.binutils.LoongArchBinutilsBuilder
for item in targets:
    target = item.split('-', maxsplit=1)[0]
    if target in targets_to_builder:
        builder = targets_to_builder[target]()
        builder.folders.build = Path(build_folder, target)
        if args.install_folder:
            builder.folders.install = Path(args.install_folder).resolve()
        builder.folders.source = bsm.location
        if args.march:
            builder.cflags.append(f"-march={args.march}")
            # -march implies -mtune except for x86-64-v{2,3,4}, which are
            # documented to imply -mtune=generic. If the user has requested one
            # of these values, it is a safe assumption they only care about
            # running on their machine, so add -mtune=native to further
            # optimize the toolchain for their machine.
            if 'x86-64-v' in args.march:
                builder.cflags.append('-mtune=native')
        builder.show_commands = args.show_build_commands
        builder.build()
    else:
        tc_build.utils.print_warning(f"Unsupported target ('{target}'), ignoring...")

print(f"\nTotal script duration: {tc_build.utils.get_duration(script_start)}")
