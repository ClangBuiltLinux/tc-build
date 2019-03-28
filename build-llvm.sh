#!/usr/bin/env bash
# shellcheck disable=SC1117,SC2028,SC2191
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2019 The ClangBuiltLinux Authors
# Description: Builds an LLVM toolchain suitable for kernel development


######################
#  HELPER FUNCTIONS  #
######################


# Checks if we are using clang to build LLVM
function cc_is_clang() {
    [[ ${CC} =~ clang ]]
}


# Get clang version in a digit
function cc_clang_version() {
    if cc_is_clang; then
        printf "%d%02d%02d\\n" "$(echo __clang_major__ | ${CC} -E -x c - | tail -n 1)" \
                               "$(echo __clang_minor__ | ${CC} -E -x c - | tail -n 1)" \
                               "$(echo __clang_patchlevel__ | ${CC} -E -x c - | tail -n 1)"
    fi
}


####################
#  MAIN FUNCTIONS  #
####################


# Initialize helper functions and export current working directory for absolute paths later
function script_setup {
    # Move into the folder that this script is called from (no message because this will never happen)
    cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" || exit
    source common.sh
    ROOT=${PWD}
}


# Parse the script parameters
function parse_parameters() {
    while (( ${#} )); do
        case ${1} in
            CC=*|CXX=*|LD=*) export "${1?}" ;;
            "-b"|"--branch") shift; [[ ${#} -lt 1 ]] && die "-b flag requires a value!"; BRANCH=${1} ;;
            "-d"|"--debug") DEBUG=true ;;
            "-h"|"--help") cat build-llvm-usage.txt; builtin exit 0 ;;
            "-i"|"--incremental") INCREMENTAL=true ;;
            "-I"|"--install-folder") shift; [[ ${#} -lt 1 ]] && die "-I flag requires a value!"; INSTALL_FOLDER=${1} ;;
            "-n"|"--no-pull") NO_PULL=true ;;
            "-p"|"--projects") shift; [[ ${#} -lt 1 ]] && die "-p flag requires a value!"; PROJECTS=${1} ;;
            "-t"|"--targets") shift; [[ ${#} -lt 1 ]] && die "-t flag requires a value!"; TARGETS=${1} ;;
            *) die "Invalid parameter '${1}' specified! Run './build-llvm.sh -h' to see all options." ;;
        esac
        shift
    done
}


# Set the CC, CXX, and LD variables, which will be passed to cmake
# NOTE: This function is heavily commented because while the flow
#       is simple on the surface, there are a lot of edge cases
#       covered by this logic.
function check_cc_ld_variables() {
    header "Checking CC and LD"

    # If the user didn't specify the C compiler with the CC variable...
    if [[ -z ${CC} ]]; then
        # use clang if available because we can use LLD for linking
        # which will make the build go much quicker. Look for newer
        # versions of clang first (from either apt.llvm.org or a
        # separate newer installation.
        for CC in clang-9 clang-8 clang-7 clang gcc; do
            # Use 'command -v' to evaluate the full path to the binary, which can
            # prevent the occasional odd bug when compiling
            CC=$(command -v ${CC})
            [[ -n ${CC} ]] && break
        done
        [[ -z ${CC} ]] && die "Neither clang nor gcc could be found on your system!"
    # If the user did specify the C compiler with the CC variable...
    else
        # get its full path with 'command -v'
        CC=$(command -v "${CC}")
    fi
    # Evaluate if CC is a symlink. Certain packages of clang (like from
    # apt.llvm.org) symlink the clang++ binary to clang++-<version> in
    # /usr/bin, which then points to something like /usr/lib/llvm-<version/bin.
    # This won't be found by the dumb logic below and trying to parse and figure
    # out a heuristic for that is a lot more effort than just going into the
    # folder that clang is actually installed in and getting clang++ from there.
    CC=$(readlink -f "${CC}")
    CC_FOLDER=$(dirname "${CC}")

    # If the user didn't specify the C++ compiler with the CXX variable...
    if [[ -z ${CXX} ]]; then
        if cc_is_clang; then
            CXX=clang++
        else
            CXX=g++
        fi
        # Use the one that is located where CC is
        CXX=$(PATH=${CC_FOLDER}:${PATH} command -v ${CXX})
    # If the user did specify the C++ compiler with the CXX variable...
    else
        # get its full path with 'command -v'
        CXX=$(command -v "${CXX}")
    fi

    # If no linker was specified...
    if [[ -z ${LD} ]]; then
        # and we are using clang, use the fastest linker available
        if cc_is_clang; then
            for LD_NAME in lld-9 lld-8 lld-7 lld gold bfd; do
                # The PATH logic is so that we use lld wherever clang is located
                LD=$(PATH=${CC_FOLDER}:${PATH} command -v ld.${LD_NAME})
                [[ -n ${LD} ]] && break
            done
            # If clang is older than 3.9, it won't accept absolute paths so we
            # need to just pass it the name (and modify PATH so that it is found properly)
            # https://github.com/llvm/llvm-project/commit/e43b7413597d8102a4412f9de41102e55f4f2ec9
            if [[ $(cc_clang_version) -lt 30900 ]]; then
                export PATH=${CC_FOLDER}:${PATH}
                LD=${LD_NAME}
            fi
        # and we are using gcc, see if we can use '-fuse-ld=gold'
        else
            echo "int main() { return 0; }" | ${CC} -fuse-ld=gold -o /dev/null -x c - &>/dev/null && LD=gold
        fi
    # If a linker was specified...
    else
        # evaluate its full path with clang to avoid weird issues and check to
        # see if it will work with '-fuse-ld', which is what cmake will do. Doing
        # it now prevents a hard error later.
        if cc_is_clang && [[ $(cc_clang_version) -ge 30900 ]]; then
            LD=$(command -v "${LD}")
        fi
        if ! echo "int main() { return 0; }" | ${CC} -fuse-ld="${LD}" -o /dev/null -x c - &>/dev/null; then
            warn "The specified LD (${LD}) will not work with CC (${CC}), unsetting LD..."
            unset LD
        fi
    fi

    # Print what binaries we are using to compile/link with so the user can decide if that is proper or not
    echo "CC: ${CC}"
    echo "CXX: ${CXX}"
    [[ -n ${LD} ]] && echo "LD: $(if echo "${LD}" | grep "/" &>/dev/null; then echo "${LD}"; else command -v ld."${LD}" || command -v "${LD}"; fi)"
}


# Make sure that the base dependencies of cmake, curl, git, and ninja are installed
function check_dependencies() {
    header "Checking dependencies"
    for DEP in cmake curl git ninja; do
        command -v ${DEP} || die "${DEP} not found, please install it!"
    done
}


# Download llvm and binutils or update them if they exist
function fetch_llvm_binutils() {
    header "Updating LLVM"

    # We default to tip of tree
    [[ -z ${BRANCH} ]] && BRANCH=master
    if [[ ! -d llvm-project ]]; then
        git clone -b "${BRANCH}" git://github.com/llvm/llvm-project || die "Error cloning LLVM!"
    else
        if [[ -z ${NO_PULL} ]]; then (
            cd llvm-project || die "Can't move into llvm-project??"
            git checkout "${BRANCH}"
            git pull --rebase
        ); fi
    fi

    # One might wonder why we are downloading binutils in an LLVM build script :)
    # We need it for the LLVMgold plugin, which can be used for LTO with ld.gold,
    # which at the time of writing this, is how the Google Pixel 3 kernel is built
    # and linked.
    dwnld_binutils

    # Auto create .gitignore
    echo ".gitignore\n${BINUTILS}/\nbuild/*\nllvm-project/" > .gitignore
    # If INSTALL_FOLDER is within the repository, ignore it
    INSTALL_FOLDER=$(readlink -f "${INSTALL_FOLDER:=${ROOT}/usr}")
    [[ ${INSTALL_FOLDER} =~ ${ROOT} ]] && echo "${INSTALL_FOLDER/${ROOT//\//\\/}\//}/" >> "${ROOT}"/.gitignore
}


# Clean up the build folder unless told not to
function cleanup() {
    BUILD_FOLDER=${ROOT}/build/llvm
    [[ -z ${INCREMENTAL} ]] && rm -rf "${BUILD_FOLDER}"
    mkdir -p "${BUILD_FOLDER}"
    cd "${BUILD_FOLDER}" || die "Can't create build folder??"
}


# Invoke cmake to generate the build files
function invoke_cmake() {
    header "Configuring LLVM"

    # Base cmake defintions, which don't depend on any user supplied options
    CMAKE_DEFINES=( # Objective-C Automatic Reference Counting (we don't use Objective-C)
                    -DCLANG_ENABLE_ARCMT=OFF
                    # We don't (currently) use the static analyzer
                    -DCLANG_ENABLE_STATIC_ANALYZER=OFF
                    # We don't use the plugin system and this saves cycles according to Chromium OS
                    -DCLANG_PLUGIN_SUPPORT=OFF
                    # The C compiler to use
                    -DCMAKE_C_COMPILER="${CC:?}"
                    # The C++ compiler to use
                    -DCMAKE_CXX_COMPILER="${CXX:?}"
                    # Where the toolchain should be installed (default is set two functions up)
                    -DCMAKE_INSTALL_PREFIX="${INSTALL_FOLDER:?}"
                    # for LLVMgold.so, which is used for LTO with ld.gold
                    -DLLVM_BINUTILS_INCDIR="${ROOT}/${BINUTILS}/include"
                    # we include compiler-rt for the sanitizers, which are currently being developed/used on Android
                    -DLLVM_ENABLE_PROJECTS="${PROJECTS:=clang;lld;compiler-rt}"
                    # Don't build bindings; they are for other languages that the kernel does not use
                    -DLLVM_ENABLE_BINDINGS=OFF
                    # Don't build Ocaml documentation
                    -DLLVM_ENABLE_OCAMLDOC=OFF
                    # Removes system dependency on terminfo and almost every major clang provider turns this off
                    -DLLVM_ENABLE_TERMINFO=OFF
                    # Don't build clang-tools-extras to cut down on build targets (about 400 files or so)
                    -DLLVM_EXTERNAL_CLANG_TOOLS_EXTRA_SOURCE_DIR=""
                    # Don't include documentation build targets because it is available on the web
                    -DLLVM_INCLUDE_DOCS=OFF
                    # Don't include example build targets to save on cmake cycles
                    -DLLVM_INCLUDE_EXAMPLES=OFF
                    # The architectures to build backends for
                    -DLLVM_TARGETS_TO_BUILD="${TARGETS:=AArch64;ARM;PowerPC;X86}" )

    # If a debug build was requested
    if [[ -n ${DEBUG} ]]; then
        CMAKE_DEFINES+=( -DCMAKE_BUILD_TYPE=Debug
                         -DCMAKE_C_FLAGS="-march=native -mtune=native"
                         -DCMAKE_CXX_FLAGS="-march=native -mtune=native"
                         -DLLVM_BUILD_TESTS=ON )
    # Otherwise, build with a release configuration
    else
        CMAKE_DEFINES+=( -DCMAKE_BUILD_TYPE=Release
                         -DCMAKE_C_FLAGS="-O2 -march=native -mtune=native"
                         -DCMAKE_CXX_FLAGS="-O2 -march=native -mtune=native"
                         -DLLVM_INCLUDE_TESTS=OFF
                         -DLLVM_ENABLE_WARNINGS=OFF )
    fi

    # Don't build libfuzzer when compiler-rt is enabled, it invokes cmake again and we don't use it
    [[ ${PROJECTS} =~ compiler-rt ]] && CMAKE_DEFINES+=( -DCOMPILER_RT_BUILD_LIBFUZZER=OFF )

    # Use ccache if it is available for faster incremental builds
    command -v ccache &>/dev/null && CMAKE_DEFINES+=( -DLLVM_CCACHE_BUILD=ON )

    # If we found a linker, we should use it
    [[ -n ${LD} ]] && CMAKE_DEFINES+=( -DLLVM_USE_LINKER="${LD}" )

    cmake -G Ninja -Wno-dev "${CMAKE_DEFINES[@]}" "${ROOT}"/llvm-project/llvm
}


# Build the world
function invoke_ninja() {
    header "Building LLVM"

    if time ninja || die "Error building LLVM!"; then
        ninja install &>/dev/null || die "Error installing LLVM to ${INSTALL_FOLDER}!"
    fi
}


# Print final information/instruction
function print_install_info() {
    header "LLVM build successful"

    echo "Installation directory: ${INSTALL_FOLDER}"
    echo
    echo "To use, either run:"
    echo
    echo "    $ export \${PATH}=${INSTALL_FOLDER}:\${PATH}"
    echo
    echo "or add:"
    echo
    echo "    PATH=${INSTALL_FOLDER}:\${PATH}"
    echo
    echo "before the command you want to use this toolchain."
    echo
}


script_setup
parse_parameters "${@}"
check_cc_ld_variables
check_dependencies
fetch_llvm_binutils
cleanup
invoke_cmake
invoke_ninja
print_install_info
