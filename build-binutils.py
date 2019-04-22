#!/usr/bin/env python3
# Description: Builds a standalone copy of binutils

import argparse
import multiprocessing
import os
import pathlib
import platform
import shutil
import subprocess
import utils


def host_arch_target():
    """
    Converts the host architecture to the first part of a target triple
    :return: Target host
    """
    host_mapping = {
        "armv7l": "arm",
        "ppc64le": "powerpc64le",
        "ppc": "powerpc"
    }
    machine = platform.machine()
    if machine in host_mapping.keys():
        return host_mapping[machine]
    else:
        return machine


def target_arch(target):
    """
    Returns the architecture from a target triple
    :param target: Triple to deduce architecture from
    :return: Architecture associated with given triple
    """
    return target.split("-")[0]


def host_is_target(target):
    """
    Checks in the current target triple the same as the host?
    :param target: Triple to match host architecture against
    :return: True if host and target are same, False otherwise
    """
    return host_arch_target() == target_arch(target)


def parse_parameters(root):
    """
    Parses parameters passed to the script into options
    :param root: The directory where the script is being invoked from
    :return: A 'Namespace' object with all the options parsed from supplied parameters
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-I",
                        "--install-folder",
                        help="""
                        By default, the script will create a "build" folder in the same folder as this script
                        and install binutils there. If you'd like to have it installed somewhere else, pass
                        it to this parameter. This can either be an absolute or relative path.
                        """,
                        type=str,
                        default=os.path.join(root.as_posix(), "build"))
    parser.add_argument("-t",
                        "--targets",
                        help="""
                        The script can build binutils targeting arm-linux-gnueabi, aarch64-linux-gnu,
                        powerpc-linux-gnu, powerpc64le-linux-gnu, and x86_64-linux-gnu.

                        You can either pass the full target or just the first part (arm, aarch64, x86_64, etc)
                        or all if you want to build all targets (which is the default). It will only add the
                        target prefix if it is not for the host architecture.
                        """,
                        nargs="+")
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
        "powerpc64le": "powerpc64le-linux-gnu",
        "powerpc": "powerpc-linux-gnu",
        "x86_64": "x86_64-linux-gnu"
    }

    targets_set = set()
    for target in targets:
        if target == "all":
            return list(targets_dict.values())
        elif target == "host":
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
        shutil.rmtree(build_folder.as_posix())
    build_folder.mkdir(parents=True, exist_ok=True)


def invoke_configure(build_folder, install_folder, root, target):
    """
    Invokes the configure script to generate a Makefile
    :param build_folder: Build directory
    :param install_folder: Directory to install binutils to
    :param root: Working directory
    :param target: Target to compile for
    """
    configure = [
        root.joinpath(utils.current_binutils(), "configure").as_posix(),
        '--prefix=%s' % install_folder.as_posix(),
        '--enable-deterministic-archives', '--enable-gold',
        '--enable-ld=default', '--enable-plugins', '--quiet',
        'CFLAGS=-O2 -march=native -mtune=native',
        'CXXFLAGS=-O2 -march=native -mtune=native'
    ]
    configure_arch_flags = {
        "arm-linux-gnueabi": [
            '--disable-multilib', '--disable-nls', '--with-gnu-as',
            '--with-gnu-ld',
            '--with-sysroot=%s' % install_folder.joinpath(target).as_posix()
        ],
        "powerpc-linux-gnu": [
            '--enable-lto', '--enable-relro', '--enable-shared',
            '--enable-threads', '--disable-gdb', '--disable-sim',
            '--disable-werror', '--with-pic', '--with-system-zlib'
        ],
        "x86_64-linux-gnu": [
            '--enable-lto', '--enable-relro', '--enable-shared',
            '--enable-targets=x86_64-pep', '--enable-threads', '--disable-gdb',
            '--disable-werror', '--with-pic', '--with-system-zlib'
        ]
    }
    configure_arch_flags['aarch64-linux-gnu'] = configure_arch_flags[
        'arm-linux-gnueabi']
    configure_arch_flags['powerpc64le-linux-gnu'] = configure_arch_flags[
        'powerpc-linux-gnu']

    configure += configure_arch_flags.get(target, [])

    # If the current machine is not the target, add the prefix to indicate
    # that it is a cross compiler
    if not host_is_target(target):
        configure += ['--program-prefix=%s-' % target, '--target=%s' % target]

    utils.print_header("Building %s binutils" % target)
    subprocess.run(configure, check=True, cwd=build_folder.as_posix())


def invoke_make(build_folder, install_folder, target):
    """
    Invoke make to compile binutils
    :param build_folder: Build directory
    :param install_folder: Directory to install binutils to
    :param target: Target to compile for
    """
    if host_is_target(target):
        subprocess.run(['make', '-s', 'configure-host', 'V=0'],
                       check=True,
                       cwd=build_folder.as_posix())
    subprocess.run(
        ['make', '-s', '-j' + str(multiprocessing.cpu_count()), 'V=0'],
        check=True,
        cwd=build_folder.as_posix())
    subprocess.run([
        'make', '-s', 'prefix=' + install_folder.as_posix(), 'install', 'V=0'
    ],
                   check=True,
                   cwd=build_folder.as_posix())
    with install_folder.joinpath(".gitignore").open("w") as gitignore:
        gitignore.write("*")


def build_targets(build, install_folder, root, targets):
    """
    Builds binutils for all specified targets
    :param build: Build directory
    :param install_folder: Directory to install binutils to
    :param root: Working directory
    :param targets: Targets to compile binutils for
    :return:
    """
    for target in targets:
        build_folder = build.joinpath(target)
        cleanup(build_folder)
        invoke_configure(build_folder, install_folder, root, target)
        invoke_make(build_folder, install_folder, target)


def main():
    root = pathlib.Path(__file__).resolve().parent

    args = parse_parameters(root)

    install_folder = pathlib.Path(args.install_folder)
    if not install_folder.is_absolute():
        install_folder = root.joinpath(install_folder)

    targets = ["all"]
    if args.targets is not None:
        targets = args.targets

    utils.download_binutils(root)

    build_targets(root.joinpath("build", "binutils"), install_folder, root,
                  create_targets(targets))


if __name__ == '__main__':
    main()
