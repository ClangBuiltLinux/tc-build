#!/usr/bin/env python3
# pylint: disable=invalid-name
# Description: Builds a standalone copy of binutils

import argparse
import hashlib
import multiprocessing
import pathlib
import platform
import re
import shutil
import subprocess
import time
import utils


def current_binutils():
    """
    Simple getter for current stable binutils release
    :return: The current stable release of binutils
    """
    return "binutils-2.40"


def download_binutils(folder):
    """
    Downloads the latest stable version of binutils
    :param folder: Directory to download binutils to
    """
    binutils = current_binutils()
    binutils_folder = folder.joinpath(binutils)
    if not binutils_folder.is_dir():
        # Remove any previous copies of binutils
        for entity in folder.glob('binutils-*'):
            if entity.is_dir():
                shutil.rmtree(entity)
            else:
                entity.unlink()

        # Download the tarball
        binutils_tarball = folder.joinpath(binutils + ".tar.xz")
        curl_cmd = [
            "curl", "-LSs", "-o", binutils_tarball,
            f"https://sourceware.org/pub/binutils/releases/{binutils_tarball.name}"
        ]
        subprocess.run(curl_cmd, check=True)
        verify_binutils_checksum(binutils_tarball)
        # Extract the tarball then remove it
        subprocess.run(["tar", "-xJf", binutils_tarball.name],
                       check=True,
                       cwd=folder)
        utils.create_gitignore(binutils_folder)
        binutils_tarball.unlink()


def verify_binutils_checksum(file_to_check):
    # Check the SHA512 checksum of the downloaded file with a known good one
    file_hash = hashlib.sha512()
    with file_to_check.open("rb") as file:
        while True:
            data = file.read(131072)
            if not data:
                break
            file_hash.update(data)
    # Get good hash from file
    curl_cmd = [
        'curl', '-fLSs',
        'https://sourceware.org/pub/binutils/releases/sha512.sum'
    ]
    sha512_sums = subprocess.run(curl_cmd,
                                 capture_output=True,
                                 check=True,
                                 text=True).stdout
    line_match = fr"([0-9a-f]+)\s+{file_to_check.name}$"
    if not (match := re.search(line_match, sha512_sums, flags=re.M)):
        raise RuntimeError(
            "Could not find binutils hash in sha512.sum output?")
    if file_hash.hexdigest() != match.groups()[0]:
        raise RuntimeError(
            "binutils: SHA512 checksum does not match known good one!")


def host_arch_target():
    """
    Converts the host architecture to the first part of a target triple
    :return: Target host
    """
    host_mapping = {
        "armv7l": "arm",
        "ppc64": "powerpc64",
        "ppc64le": "powerpc64le",
        "ppc": "powerpc"
    }
    machine = platform.machine()
    return host_mapping.get(machine, machine)


def target_arch(target):
    """
    Returns the architecture from a target triple
    :param target: Triple to deduce architecture from
    :return: Architecture associated with given triple
    """
    return target.split("-")[0]


def host_is_target(target):
    """
    Checks if the current target triple the same as the host.
    :param target: Triple to match host architecture against
    :return: True if host and target are same, False otherwise
    """
    return host_arch_target() == target_arch(target)


def parse_parameters(root_folder):
    """
    Parses parameters passed to the script into options
    :param root_folder: The directory where the script is being invoked from
    :return: A 'Namespace' object with all the options parsed from supplied parameters
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-b",
                        "--binutils-folder",
                        help="""
                        By default, the script will download a copy of the binutils source in the same folder as
                        this script. If you have your own copy of the binutils source that you would like to build
                        from, pass it to this parameter. This can either be an absolute or relative path.
                        """,
                        type=str)
    parser.add_argument("-B",
                        "--build-folder",
                        help="""
                        By default, the script will create a "build" folder in the same folder as this script,
                        then a "binutils" folder within that one and build the files there. If you would like
                        that done somewhere else, pass it to this parameter. This can either be an absolute
                        or relative path.
                        """,
                        type=str,
                        default=root_folder.joinpath("build", "binutils"))
    parser.add_argument("-I",
                        "--install-folder",
                        help="""
                        By default, the script will create an "install" folder in the same folder as this script
                        and install binutils there. If you'd like to have it installed somewhere else, pass
                        it to this parameter. This can either be an absolute or relative path.
                        """,
                        type=str,
                        default=root_folder.joinpath("install"))
    parser.add_argument("-s",
                        "--skip-install",
                        help="""
                        Skip installing binutils into INSTALL_FOLDER
                        """,
                        action="store_true")
    parser.add_argument("-t",
                        "--targets",
                        help="""
                        The script can build binutils targeting arm-linux-gnueabi, aarch64-linux-gnu,
                        mipsel-linux-gnu, powerpc-linux-gnu, powerpc64-linux-gnu, powerpc64le-linux-gnu,
                        riscv64-linux-gnu, s390x-linux-gnu, and x86_64-linux-gnu.

                        You can either pass the full target or just the first part (arm, aarch64, x86_64, etc)
                        or all if you want to build all targets (which is the default). It will only add the
                        target prefix if it is not for the host architecture.
                        """,
                        nargs="+")
    parser.add_argument("-m",
                        "--march",
                        metavar="ARCH",
                        help="""
                        Add -march=ARCH and -mtune=ARCH to CFLAGS to optimize the toolchain for the target
                        host processor.
                        """,
                        type=str)
    return parser.parse_args()


def create_targets(targets):
    """
    Generate a list of targets that can be passed to the binutils compile function
    :param targets: A list of targets to convert to binutils target triples
    :return: A list of target triples
    """
    targets_dict = {
        "arm": "arm-linux-gnueabi",
        "aarch64": "aarch64-linux-gnu",
        "mips": "mips-linux-gnu",
        "mipsel": "mipsel-linux-gnu",
        "powerpc64": "powerpc64-linux-gnu",
        "powerpc64le": "powerpc64le-linux-gnu",
        "powerpc": "powerpc-linux-gnu",
        "riscv64": "riscv64-linux-gnu",
        "s390x": "s390x-linux-gnu",
        "x86_64": "x86_64-linux-gnu"
    }

    targets_set = set()
    for target in targets:
        if target == "all":
            return list(targets_dict.values())
        if target == "host":
            key = host_arch_target()
        else:
            key = target_arch(target)
        targets_set.add(targets_dict[key])

    return list(targets_set)


def cleanup(build_folder):
    """
    Cleanup the build directory
    :param build_folder: Build directory
    """
    if build_folder.is_dir():
        shutil.rmtree(build_folder)
    build_folder.mkdir(parents=True, exist_ok=True)


def invoke_configure(binutils_folder, build_folder, install_folder, target,
                     host_arch):
    """
    Invokes the configure script to generate a Makefile
    :param binutils_folder: Binutils source folder
    :param build_folder: Build directory
    :param install_folder: Directory to install binutils to
    :param target: Target to compile for
    :param host_arch: Host architecture to optimize for
    """
    configure = [
        binutils_folder.joinpath("configure"),
        'CC=gcc',
        'CXX=g++',
        '--disable-compressed-debug-sections',
        '--disable-gdb',
        '--disable-nls',
        '--disable-werror',
        '--enable-deterministic-archives',
        '--enable-new-dtags',
        '--enable-plugins',
        '--enable-threads',
        '--quiet',
        '--with-system-zlib',
    ]  # yapf: disable
    if install_folder:
        configure += [f'--prefix={install_folder}']
    if host_arch:
        configure += [
            f'CFLAGS=-O2 -march={host_arch} -mtune={host_arch}',
            f'CXXFLAGS=-O2 -march={host_arch} -mtune={host_arch}'
        ]
    else:
        configure += ['CFLAGS=-O2', 'CXXFLAGS=-O2']
    # gprofng uses glibc APIs that might not be available on musl
    if utils.libc_is_musl():
        configure += ['--disable-gprofng']

    configure_arch_flags = {
        "arm-linux-gnueabi": [
            '--disable-multilib',
            '--with-gnu-as',
            '--with-gnu-ld',
        ],
        "powerpc-linux-gnu": [
            '--disable-sim',
            '--enable-lto',
            '--enable-relro',
            '--with-pic',
        ],
    }  # yapf: disable
    configure_arch_flags['aarch64-linux-gnu'] = [
        *configure_arch_flags['arm-linux-gnueabi'],
        '--enable-gold',
        '--enable-ld=default',
    ]
    configure_arch_flags['powerpc64-linux-gnu'] = configure_arch_flags[
        'powerpc-linux-gnu']
    configure_arch_flags['powerpc64le-linux-gnu'] = configure_arch_flags[
        'powerpc-linux-gnu']
    configure_arch_flags['riscv64-linux-gnu'] = configure_arch_flags[
        'powerpc-linux-gnu']
    configure_arch_flags['s390x-linux-gnu'] = [
        *configure_arch_flags['powerpc-linux-gnu'],
        '--enable-targets=s390-linux-gnu',
    ]
    configure_arch_flags['x86_64-linux-gnu'] = [
        *configure_arch_flags['powerpc-linux-gnu'],
        '--enable-targets=x86_64-pep',
    ]

    for endian in ["", "el"]:
        configure_arch_flags[f'mips{endian}-linux-gnu'] = [
            f'--enable-targets=mips64{endian}-linux-gnuabi64,mips64{endian}-linux-gnuabin32'
        ]

    configure += configure_arch_flags.get(target, [])

    # If the current machine is not the target, add the prefix to indicate
    # that it is a cross compiler
    if not host_is_target(target):
        configure += [f'--program-prefix={target}-', f'--target={target}']

    utils.print_header(f"Building {target} binutils")
    subprocess.run(configure, check=True, cwd=build_folder)


def invoke_make(build_folder, install_folder, target):
    """
    Invoke make to compile binutils
    :param build_folder: Build directory
    :param install_folder: Directory to install binutils to
    :param target: Target to compile for
    """
    make = ['make', '-s', '-j' + str(multiprocessing.cpu_count()), 'V=0']
    if host_is_target(target):
        subprocess.run(make + ['configure-host'], check=True, cwd=build_folder)
    subprocess.run(make, check=True, cwd=build_folder)
    if install_folder:
        subprocess.run(make + [f'prefix={install_folder}', 'install'],
                       check=True,
                       cwd=build_folder)
        with install_folder.joinpath(".gitignore").open("w") as gitignore:
            gitignore.write("*")


def build_targets(binutils_folder, build, install_folder, targets, host_arch):
    """
    Builds binutils for all specified targets
    :param binutils_folder: Binutils source folder
    :param build: Build directory
    :param install_folder: Directory to install binutils to
    :param targets: Targets to compile binutils for
    :param host_arch: Host architecture to optimize for
    :return:
    """
    for target in targets:
        build_folder = build.joinpath(target)
        cleanup(build_folder)
        invoke_configure(binutils_folder, build_folder, install_folder, target,
                         host_arch)
        invoke_make(build_folder, install_folder, target)


def main():
    script_start = time.time()

    root_folder = pathlib.Path(__file__).resolve().parent

    args = parse_parameters(root_folder)

    if args.binutils_folder:
        binutils_folder = pathlib.Path(args.binutils_folder).resolve()
    else:
        binutils_folder = root_folder.joinpath(current_binutils())
        download_binutils(root_folder)

    build_folder = pathlib.Path(args.build_folder).resolve()

    if args.skip_install:
        install_folder = None
    else:
        install_folder = pathlib.Path(args.install_folder).resolve()

    targets = ["all"]
    if args.targets is not None:
        targets = args.targets

    build_targets(binutils_folder, build_folder, install_folder,
                  create_targets(targets), args.march)

    print(f"\nTotal script duration: {utils.get_duration(script_start)}")


if __name__ == '__main__':
    main()
