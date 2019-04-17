#!/usr/bin/env python3
# Copyright (C) 2019 The ClangBuiltLinux Authors
# Description: Common helper functions

import hashlib
import pathlib
import shutil
import subprocess


def create_gitignore(folder):
    """
    Create a gitignore that ignores all files in a folder
    :param folder: Folder to create the gitignore in
    """
    with folder.joinpath(".gitignore").open("w") as gitignore:
        gitignore.write("*")


def current_binutils():
    """
    Simple getter for current stable binutils release
    :return: The current stable release of binutils
    """
    return "binutils-2.32"


def download_binutils(root):
    """
    Downloads the latest stable version of binutils
    :param root: Directory to download binutils to
    """
    binutils = current_binutils()
    p = pathlib.Path.joinpath(root, binutils)
    if not p.is_dir():
        # Remove any previous copies of binutils
        for entity in root.glob('binutils*'):
            if entity.is_dir():
                shutil.rmtree(entity.as_posix())
            else:
                entity.unlink()

        # Download the tarball
        binutils_tarball = pathlib.Path.joinpath(root, binutils + ".tar.gz")
        subprocess.run([
            "curl", "-LSs", "-o",
            binutils_tarball.as_posix(),
            "https://ftp.gnu.org/gnu/binutils/" + binutils_tarball.name
        ],
                       check=True)

        # Check the sha256sum of the downloaded package with a known good one
        # To regenerate the sha256sum, download the .tar.gz and .tar.gz.sig files
        # $ gpg --verify *.tar.gz.sig *.tar.gz
        # $ sha256sum *.tar.gz
        file_hash = hashlib.sha256()
        with binutils_tarball.open("rb") as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                file_hash.update(data)
        good_hash = "9b0d97b3d30df184d302bced12f976aa1e5fbf4b0be696cdebc6cca30411a46e"
        if file_hash.hexdigest() != good_hash:
            raise RuntimeError(
                "binutils sha256sum does not match known good one!")

        # Extract the tarball then remove it
        subprocess.run(["tar", "-xzf", binutils_tarball.name], check=True)
        create_gitignore(p)
        binutils_tarball.unlink()


def print_header(string):
    """
    Prints a fancy header
    :param string: String to print inside the header
    """
    print('\033[01;31m')
    for x in range(0, len(string) + 6):
        print("=", end="")
    print("\n== " + string + " ==")
    for x in range(0, len(string) + 6):
        print("=", end="")
    print('\n\033[0m')
