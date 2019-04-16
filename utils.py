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
    p = pathlib.Path(root + "/" + binutils)
    if not p.is_dir():
        os.chdir(root)
        for entity in glob.glob(os.path.join(root, 'binutils*')):
            if os.path.isdir(entity):
                shutil.rmtree(entity)
            else:
                os.remove(entity)
        subprocess.run(["curl", "-LSsO", "https://ftp.gnu.org/gnu/binutils/" + binutils + ".tar.gz"], check=True)
        file_hash = hashlib.sha256()
        with open(str(p) + ".tar.gz", "rb") as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                file_hash.update(data)
        if file_hash.hexdigest() != "9b0d97b3d30df184d302bced12f976aa1e5fbf4b0be696cdebc6cca30411a46e":
            raise RuntimeError("binutils sha256sum does not match known good one!")
        subprocess.run(["tar", "-xzf", binutils + ".tar.gz"], check=True)
        os.remove(binutils + ".tar.gz")


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
