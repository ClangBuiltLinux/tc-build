#!/usr/bin/env python

import argparse
import os
import pathlib
import platform


def x86_64_target():
    if platform.machine() == "x86_64":
        return "host"
    else:
        return "x86_64-linux-gnu"


def parse_parameters(root):
    parser = argparse.ArgumentParser()
    parser.add_argument("-I", "--install-folder",
                        help="""
                        By default, the script will create a "usr" folder in the same folder as this script
                        and install binutils there. If you'd like to have it installed somewhere else, pass
                        it to this parameter. This can either be an absolute or relative path.

                        Example: ~/binutils
                        """, type=str, default=os.path.join(root.as_posix(), "usr"))
    parser.add_argument("-t", "--targets",
                        help="""
                        The script can build binutils targeting arm-linux-gnueabi, aarch64-linux-gnu,
                        powerpc-linux-gnu, powerpc64le-linux-gnu, and x86_64-linux-gnu (host if on x86_64).

                        You can either pass the full target or just the first part (arm, aarch64, etc) or all
                        if you want to build all targets (which is the default).

                        Example: all, aarch64, arm-linux-gnueabi
                        """, default="all", nargs="+")
    return parser.parse_args()


def create_tuples(targets):
    tuples_dict = {
        "arm": "arm-linux-gnueabi",
        "aarch64": "aarch64-linux-gnu",
        "powerpc64le": "powerpc64-linux-gnu",
        "powerpc": "powerpc-linux-gnu",
        "x86": x86_64_target()
    }
    tuples = []

    if ''.join(targets) == "all":
        for key in tuples_dict:
            tuples.append(tuples_dict[key])
    else:
        for target in targets:
            tuples.append(tuples_dict[target.split("-")[0]])

    return tuples

def main():
    root = pathlib.Path(__file__).resolve().parent
    build = root.joinpath("build", "binutils")

    args = parse_parameters(root)

    install_folder = pathlib.Path(args.install_folder)
    if not install_folder.is_absolute():
        install_folder = root.joinpath(install_folder)

    tuples = create_tuples(args.targets)


if __name__ == '__main__':
    main()
