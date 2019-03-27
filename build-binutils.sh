#!/usr/bin/env bash
# shellcheck disable=SC2191
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2019 The ClangBuiltLinux Authors
# Description: Builds a standalone copy of binutils


# Properly set target if on x86_64 machine
function x86_64_target() {
    if [[ $(uname -m) = "x86_64" ]]; then
        echo "host"
    else
        echo "x86_64-linux-gnu"
    fi
}


# Initialize helper functions and export current working directory for absolute paths later
function script_setup {
    # Move into the folder that this script is called from (no message because this will never happen)
    cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" || exit
    source common.sh
    ROOT=${PWD}
}


# Parse the script parameters
function parse_parameters() {
    TARGETS=()
    while (( ${#} )); do
        case ${1} in
            "-t"|"--target")
                shift
                [[ ${#} -lt 1 ]] && die "-t flag requires a value!"
                case ${1} in
                    "all") TARGETS=( aarch64-linux-gnu arm-linux-gnueabi
                                     powerpc-linux-gnu powerpc64le-linux-gnu
                                     "$(x86_64_target)" ) ;;
                    arm*) TARGETS+=( arm-linux-gnueabi ) ;;
                    aarch64*) TARGETS+=( aarch64-linux-gnu ) ;;
                    powerpc64le*) TARGETS+=( powerpc64-linux-gnu ) ;;
                    powerpc*) TARGETS+=( powerpc-linux-gnu ) ;;
                    x86*|"host") TARGETS+=( "$(x86_64_target)" ) ;;
                esac ;;
            "-h"|"--help") cat build-binutils-usage.txt; builtin exit 0 ;;
            *) die "Invalid parameter '${1}' specified! Run './build-binutils.sh -h' to see all options." ;;
        esac
        shift
    done
    [[ -z ${TARGETS[*]} ]] && die "No targets specified! Please run './build-binutils.sh' to see all the options."
}


# Setup the build folder
function setup_build_folder() {
    BUILD_FOLDER=${ROOT}/build/binutils/${TARGET}
    rm -rf "${BUILD_FOLDER}"
    mkdir -p "${BUILD_FOLDER}"
    cd "${BUILD_FOLDER}" || die "Couldn't create build folder??"
}


# Configure binutils
function configure_binutils() {
    COMMON_FLAGS=( --prefix="${INSTALL_FOLDER:="${ROOT}"/usr}"
                   --enable-deterministic-archives
                   --enable-gold
                   --enable-ld=default
                   --enable-plugins
                   --quiet
                   CFLAGS="-O2 -march=native -mtune=native"
                   CXXFLAGS="-O2 -march=native -mtune=native" )
    CONFIGURE=${ROOT}/${BINUTILS}/configure

    case ${TARGET} in
        arm-*|aarch64-*)
            "${CONFIGURE}" \
                --disable-multilib \
                --disable-nls \
                --program-prefix="${TARGET}"- \
                --target="${TARGET}" \
                --with-gnu-as \
                --with-gnu-ld \
                --with-sysroot="${INSTALL_FOLDER}/${TUPLE}" \
                "${COMMON_FLAGS[@]}" ;;
        powerpc*)
            "${CONFIGURE}" \
                --enable-lto \
                --enable-relro \
                --enable-shared \
                --enable-threads \
                --disable-gdb \
                --disable-sim \
                --disable-werror \
                --program-prefix="${TARGET}"- \
                --target="${TARGET}" \
                --with-pic \
                --with-system-zlib \
                "${COMMON_FLAGS[@]}" ;;
        x86*|host)
            "${CONFIGURE}" \
                --enable-lto \
                --enable-relro \
                --enable-shared \
                --enable-targets=x86_64-pep \
                --enable-threads \
                --disable-gdb \
                --disable-werror \
                "$([[ $(uname -m) != x86_64 ]] && echo "--program-prefix=${TARGET}- --target=${TARGET}")" \
                --with-pic \
                --with-system-zlib \
                "${COMMON_FLAGS[@]}"
            [[ ${TARGET} = "host" ]] && make -s configure-host V=0 ;;
    esac
}


# Build binutils
function build_binutils() {
    time make -s -j"$(nproc)" V=0 || die "Error building ${TARGET} binutils"
}


# Install binutils
function install_binutils {
    make -s prefix="${INSTALL_FOLDER}" install V=0 || die "Error installing ${TARGET} binutils"
}


# Main for loop
function for_all_targets() {
    for TARGET in "${TARGETS[@]}"; do
        setup_build_folder
        configure_binutils
        build_binutils
        install_binutils
    done
}


script_setup
parse_parameters "${@}"
dwnld_binutils
for_all_targets
