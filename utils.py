#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2019 The ClangBuiltLinux Authors
# Description: Common helper functions

import hashlib
import pathlib
import shutil
import subprocess


# The latest stable version of binutils
def current_binutils():
    return "binutils-2.32"


# Download the latest stable version of binutils
def download_binutils(root):
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
        if file_hash.hexdigest(
        ) != "9b0d97b3d30df184d302bced12f976aa1e5fbf4b0be696cdebc6cca30411a46e":
            raise RuntimeError(
                "binutils sha256sum does not match known good one!")

        # Extract the tarball then remove it
        subprocess.run(["tar", "-xzf", binutils_tarball.name], check=True)
        binutils_tarball.unlink()


# Print a fancy header
def header(string):
    print('\033[01;31m')
    for x in range(0, len(string) + 6):
        print("=", end="")
    print("\n== " + string + " ==")
    for x in range(0, len(string) + 6):
        print("=", end="")
    print('\n\033[0m')
