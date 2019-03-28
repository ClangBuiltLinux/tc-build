#!/usr/bin/env bash
# shellcheck disable=SC1117
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2019 The ClangBuiltLinux Authors
# Description: Common helper functions


# Prints an error in bold red and exits the script
function die() {
    echo
    echo "\033[01;31m${*}\033[0m"
    echo
    builtin exit 1
}


# Downloads and extracts the latest binutils
function dwnld_binutils() {
    BINUTILS=binutils-2.32
    if [[ ! -d ${BINUTILS} ]]; then
        # Remove any previous copies of binutils
        rm -rf binutils*
        curl -LSsO https://ftp.gnu.org/gnu/binutils/${BINUTILS}.tar.gz || die "Error downloading binutils!"
        # Check the sha256sum of the downloaded package with a known good one
        # To regenerate the sha256sum, download the .tar.gz and .tar.gz.sig files
        # $ gpg --verify *.tar.gz.sig *.tar.gz
        # $ sha256sum *.tar.gz
        SHA256SUM=9b0d97b3d30df184d302bced12f976aa1e5fbf4b0be696cdebc6cca30411a46e
        [[ $(sha256sum ${BINUTILS}.tar.gz | awk '{print $1}') != "${SHA256SUM}" ]] && die "binutils sha256sum does not match known good one!"
        tar -xzf ${BINUTILS}.tar.gz || die "Extracting binutils failed!"
        rm -rf ${BINUTILS}.tar.gz
    fi
}


# Wrapper for echo to always print escape codes properly
function echo() {
    command echo -e "${@}"
}


# Prints a formatted header to the user
function header() {
    echo "\033[01;31m"
    echo "====$(for i in $(seq ${#1}); do echo "=\c"; done)===="
    echo "==  ${1}  =="
    # SC2034: i appears unused. Verify it or export it.
    # shellcheck disable=SC2034
    echo "====$(for i in $(seq ${#1}); do echo "=\c"; done)===="
    echo "\033[0m"
}


# Prints a warning in bold yellow
function warn() {
    echo
    echo "\033[01;33m${*}\033[0m"
    echo
}
