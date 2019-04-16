#!/usr/bin/env python

import argparse
import os


def parse_parameters():
    parser = argparse.ArgumentParser()
    parser.add_argument("-I", "--install-folder",
                        help="""
                        By default, the script will create a "usr" folder in the same folder as this script
                        and install binutils there. If you'd like to have it installed somewhere else, pass
                        it to this parameter. This can either be an absolute or relative path.

                        Example: ~/binutils
                        """, type=str, default=os.getcwd() + "/usr")
    parser.add_argument("-t", "--targets",
                        help="""
                        The script can build binutils targeting arm-linux-gnueabi, aarch64-linux-gnu,
                        powerpc-linux-gnu, powerpc64le-linux-gnu, and x86_64-linux-gnu (host if on x86_64).

                        You can either pass the full target or just the first part (arm, aarch64, etc) or all
                        if you want to build all targets (which is the default).

                        Example: all, aarch64, arm-linux-gnueabi
                        """, default="all", nargs="+")
    return parser.parse_args()


def main():
    root = os.path.dirname(os.path.realpath(__file__))
    os.chdir(root)
    args = parse_parameters()


if __name__ == '__main__':
    main()
