#!/usr/bin/env python3
# Description: Common helper functions

import hashlib
import pathlib
import shutil
import subprocess


def create_gitignore(folder):
    """
    Create a gitignore that ignores all files in a folder. Some folders are not
    known until the script is run so they can't be added to the root .gitignore
    :param folder: Folder to create the gitignore in
    """
    with folder.joinpath(".gitignore").open("w") as gitignore:
        gitignore.write("*")


def current_binutils():
    """
    Simple getter for current stable binutils release
    :return: The current stable release of binutils
    """
    return "binutils-2.35.1"


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
                shutil.rmtree(entity.as_posix())
            else:
                entity.unlink()

        # Download the tarball
        binutils_tarball = folder.joinpath(binutils + ".tar.xz")
        subprocess.run([
            "curl", "-LSs", "-o",
            binutils_tarball.as_posix(),
            "https://ftp.gnu.org/gnu/binutils/" + binutils_tarball.name
        ],
                       check=True)
        verify_binutils_checksum(binutils_tarball)
        # Extract the tarball then remove it
        subprocess.run(["tar", "-xJf", binutils_tarball.name],
                       check=True,
                       cwd=folder.as_posix())
        create_gitignore(binutils_folder)
        binutils_tarball.unlink()


def verify_binutils_checksum(file):
    # Check the SHA512 checksum of the downloaded file with a known good one
    # Use officially provided sha512.sum from <sourceware.org> for .tar.xz file
    # Link: https://sourceware.org/pub/binutils/releases/sha512.sum
    file_hash = hashlib.sha512()
    with file.open("rb") as f:
        while True:
            data = f.read(131072)
            if not data:
                break
            file_hash.update(data)
    good_hash = "94ff72708403413b70b247f3af4099ebaa882b6659249869f1ed9941a0f1912e313f08357d470f9fd2359e7f5e5b0eb86285e5eaf883fa8187789d6b1bd304eb"
    if file_hash.hexdigest() != good_hash:
        raise RuntimeError("binutils: SHA512 checksum does not match known good one!")


def print_header(string):
    """
    Prints a fancy header
    :param string: String to print inside the header
    """
    # Use bold cyan for the header so that the headers
    # are not intepreted as success (green) or failed (red)
    print("\033[01;36m")
    for x in range(0, len(string) + 6):
        print("=", end="")
    print("\n== %s ==" % string)
    for x in range(0, len(string) + 6):
        print("=", end="")
    # \033[0m resets the color back to the user's default
    print("\n\033[0m")


def print_error(string):
    """
    Prints a error in bold red
    :param string: String to print
    """
    # Use bold red for error
    print("\033[01;31m%s\n\033[0m" % string)
