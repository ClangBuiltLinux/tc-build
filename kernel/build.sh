#!/usr/bin/env bash

# Get the tc-build folder's absolute path, which is the directory above this one
TC_BLD=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)
[[ -z ${TC_BLD} ]] && exit 1

# Add the default install bin folder to PATH for binutils
# Add the stage 2 bin folder to PATH for the instrumented clang
for FOLDER in ${TC_BLD}/install/bin ${TC_BLD}/build/llvm/stage2/bin; do
    [[ -d ${FOLDER} ]] && export PATH=${FOLDER}:${PATH}
done

# If the user wants to add another folder to PATH, they can do it with the PATH_OVERRIDE variable
[[ -n ${PATH_OVERRIDE} ]] && export PATH=${PATH_OVERRIDE}:${PATH}

# A kernel folder can be supplied for testing the script
if [[ ${#} -gt 0 ]]; then
    cd "${1}" || exit 1
else
    LINUX=linux-5.1
    LINUX_TARBALL=${TC_BLD}/kernel/${LINUX}.tar.gz
    LINUX_PATCH=${TC_BLD}/kernel/${LINUX}.patch

    # If we don't have the source tarball, download it
    [[ -f ${LINUX_TARBALL} ]] || curl -LSso "${LINUX_TARBALL}" https://git.kernel.org/torvalds/t/${LINUX}.tar.gz

    # If there is a patch to apply, remove the folder so that we can patch it accurately (we cannot assume it has already been patched)
    [[ -f ${LINUX_PATCH} ]] && rm -rf ${LINUX}
    [[ -d ${LINUX} ]] || { tar -xzf "${LINUX_TARBALL}" || exit ${?}; }
    cd ${LINUX} || exit 1
    [[ -f ${LINUX_PATCH} ]] && patch -p1 < "${LINUX_PATCH}"
fi

# Check for all binutils and build them if necessary
TARGETS=()
for PREFIX in arm-linux-gnueabi aarch64-linux-gnu powerpc-linux-gnu powerpc64le-linux-gnu; do
    command -v "${PREFIX}"-as &>/dev/null || TARGETS=( "${TARGETS[@]}" "${PREFIX}" )
done
command -v as &>/dev/null || TARGETS=( "${TARGETS[@]}" host )
[[ -n "${TARGETS[*]}" ]] && { "${TC_BLD}"/build-binutils.py -t "${TARGETS[@]}" || exit ${?}; }

# SC2191: The = here is literal. To assign by index, use ( [index]=value ) with no spaces. To keep as literal, quote it.
# shellcheck disable=SC2191
MAKE=( make -j"$(nproc)" CC=clang HOSTCC=clang HOSTLD=ld.lld O=out )

time "${MAKE[@]}" ARCH=arm CROSS_COMPILE=arm-linux-gnueabi- LD=ld.lld distclean defconfig zImage modules || exit ${?}

time "${MAKE[@]}" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LD=ld.lld distclean defconfig Image.gz modules || exit ${?}

time "${MAKE[@]}" ARCH=powerpc CROSS_COMPILE=powerpc-linux-gnu- distclean ppc44x_defconfig zImage modules || exit ${?}

time "${MAKE[@]}" ARCH=powerpc CROSS_COMPILE=powerpc64le-linux-gnu- distclean powernv_defconfig zImage.epapr modules || exit ${?}

time "${MAKE[@]}" LD=ld.lld O=out distclean defconfig bzImage modules || exit ${?}
