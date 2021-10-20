#!/usr/bin/env bash

BASE=$(dirname "$(readlink -f "${0}")")

set -eu

function parse_parameters() {
    while ((${#})); do
        case ${1} in
            all | binutils | deps | kernel | llvm) ACTION=${1} ;;
            *) exit 33 ;;
        esac
        shift
    done
}

function do_all() {
    do_deps
    do_llvm
    do_binutils
    do_kernel
}

function do_binutils() {
    "${BASE}"/build-binutils.py -t x86_64
}

function do_deps() {
    # We only run this when running on GitHub Actions
    [[ -z ${GITHUB_ACTIONS:-} ]] && return 0
    sudo apt-get install -y --no-install-recommends \
        bc \
        bison \
        ca-certificates \
        clang \
        cmake \
        curl \
        file \
        flex \
        gcc \
        g++ \
        git \
        libelf-dev \
        libssl-dev \
        lld \
        make \
        ninja-build \
        python3 \
        texinfo \
        xz-utils \
        zlib1g-dev
}

function do_kernel() {
    cd "${BASE}"/kernel
    ./build.sh -t X86
}

function do_llvm() {
    EXTRA_ARGS=()
    [[ -n ${GITHUB_ACTIONS:-} ]] && EXTRA_ARGS+=(--no-ccache)
    "${BASE}"/build-llvm.py \
        --assertions \
        --branch "release/13.x" \
        --build-stage1-only \
        --check-targets clang lld llvm \
        --install-stage1-only \
        --projects "clang;lld" \
        --shallow-clone \
        --targets X86 \
        "${EXTRA_ARGS[@]}"
}

parse_parameters "${@}"
do_"${ACTION:=all}"
