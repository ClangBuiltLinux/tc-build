#!/usr/bin/env python3

import colorama
import glob
import hashlib
import pathlib
import os
import shutil
import subprocess

def current_binutils():
    return "binutils-2.32"

def download_binutils(root):
    binutils = current_binutils()
    p = pathlib.Path.joinpath(root, binutils)
    if not p.is_dir():
        for entity in root.glob('binutils*'):
            if entity.is_dir():
                shutil.rmtree(entity.as_posix())
            else:
                entity.unlink()
        binutils_tarball = pathlib.Path.joinpath(root, binutils + ".tar.gz")
        subprocess.run(["curl", "-LSs", "-o", binutils_tarball.as_posix(), "https://ftp.gnu.org/gnu/binutils/" + binutils_tarball.name], check=True)
        file_hash = hashlib.sha256()
        with binutils_tarball.open("rb") as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                file_hash.update(data)
        if file_hash.hexdigest() != "9b0d97b3d30df184d302bced12f976aa1e5fbf4b0be696cdebc6cca30411a46e":
            raise RuntimeError("binutils sha256sum does not match known good one!")
        subprocess.run(["tar", "-xzf", binutils_tarball.name], check=True)
        binutils_tarball.unlink()


def header(string):
    print(colorama.Fore.RED + colorama.Style.BRIGHT)
    for x in range(0, len(string) + 6):
        print("=", end="")
    print()
    print("== " + string + " ==")
    for x in range(0, len(string) + 6):
        print("=", end="")
    print()
    print(colorama.Style.RESET_ALL)
