#!/usr/bin/env bash
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
        curl -LSs https://ftp.gnu.org/gnu/binutils/${BINUTILS}.tar.gz | tar -xzf - || die "Error downloading binutils!"
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
