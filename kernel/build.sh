#!/usr/bin/env bash

# Get the tc-build folder's absolute path, which is the directory above this one
TC_BLD=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)
[[ -z ${TC_BLD} ]] && exit 1

# Parse parameters
while (( ${#} )); do
    case ${1} in
        "--allyesconfig")
            CONFIG_TARGET=allyesconfig ;;
        "-b"|"--build-folder")
            shift
            BUILD_FOLDER=${1} ;;
        "-p"|"--path-override")
            shift
            PATH_OVERRIDE=${1} ;;
        "-s"|"--src-folder")
            shift
            SRC_FOLDER=${1} ;;
        "-t"|"--targets")
            shift
            IFS=";" read -ra LLVM_TARGETS <<< "${1}"
            # Convert LLVM targets into GNU triples
            for LLVM_TARGET in "${LLVM_TARGETS[@]}"; do
                case ${LLVM_TARGET} in
                    "AArch64") TARGETS=( "${TARGETS[@]}" "aarch64-linux-gnu" ) ;;
                    "ARM") TARGETS=( "${TARGETS[@]}" "arm-linux-gnueabi" ) ;;
                    "PowerPC") TARGETS=( "${TARGETS[@]}" "powerpc-linux-gnu" "powerpc64-linux-gnu" "powerpc64le-linux-gnu" ) ;;
                    # SystemZ is consumed until Linux 5.6
                    "SystemZ") ;;
                    "X86") TARGETS=( "${TARGETS[@]}" "x86_64-linux-gnu" ) ;;
                esac
            done
    esac
    shift
done
[[ -z ${TARGETS[*]} ]] && TARGETS=( "arm-linux-gnueabi" "aarch64-linux-gnu" "powerpc-linux-gnu" "powerpc64-linux-gnu" "powerpc64le-linux-gnu" "x86_64-linux-gnu" )
[[ -z ${CONFIG_TARGET} ]] && CONFIG_TARGET=defconfig

# Add the default install bin folder to PATH for binutils
# Add the stage 2 bin folder to PATH for the instrumented clang
for BIN_FOLDER in ${TC_BLD}/install/bin ${BUILD_FOLDER:=${TC_BLD}/build/llvm}/stage2/bin; do
    export PATH=${BIN_FOLDER}:${PATH}
done

# If the user wants to add another folder to PATH, they can do it with the PATH_OVERRIDE variable
[[ -n ${PATH_OVERRIDE} ]] && export PATH=${PATH_OVERRIDE}:${PATH}

# A kernel folder can be supplied via '-f' for testing the script
if [[ -n ${SRC_FOLDER} ]]; then
    cd "${SRC_FOLDER}" || exit 1
else
    LINUX=linux-5.5
    LINUX_TARBALL=${TC_BLD}/kernel/${LINUX}.tar.xz
    LINUX_PATCH=${TC_BLD}/kernel/${LINUX}-${CONFIG_TARGET}.patch

    # If we don't have the source tarball, download and verify it
    if [[ ! -f ${LINUX_TARBALL} ]]; then
        curl -LSso "${LINUX_TARBALL}" https://cdn.kernel.org/pub/linux/kernel/v5.x/"${LINUX_TARBALL##*/}"

        ( cd "${LINUX_TARBALL%/*}" || exit 1; sha256sum -c "${LINUX_TARBALL}".sha256 --quiet ) || {
            echo "Linux tarball verification failed! Please remove '${LINUX_TARBALL}' and try again."
            exit 1
        }
    fi

    # If there is a patch to apply, remove the folder so that we can patch it accurately (we cannot assume it has already been patched)
    [[ -f ${LINUX_PATCH} ]] && rm -rf ${LINUX}
    [[ -d ${LINUX} ]] || { tar -xf "${LINUX_TARBALL}" || exit ${?}; }
    cd ${LINUX} || exit 1
    [[ -f ${LINUX_PATCH} ]] && { patch -p1 < "${LINUX_PATCH}" || exit ${?}; }
fi

# Check for all binutils and build them if necessary
BINUTILS_TARGETS=()
for PREFIX in "${TARGETS[@]}"; do
    # We assume an x86_64 host, should probably make this more generic in the future
    if [[ ${PREFIX} = "x86_64-linux-gnu" ]]; then
        COMMAND=as
    else
        COMMAND="${PREFIX}"-as
    fi
    command -v "${COMMAND}" &>/dev/null || BINUTILS_TARGETS=( "${BINUTILS_TARGETS[@]}" "${PREFIX}" )
done
[[ -n "${BINUTILS_TARGETS[*]}" ]] && { "${TC_BLD}"/build-binutils.py -t "${BINUTILS_TARGETS[@]}" || exit ${?}; }

# SC2191: The = here is literal. To assign by index, use ( [index]=value ) with no spaces. To keep as literal, quote it.
# shellcheck disable=SC2191
MAKE=( make -j"$(nproc)" -s CC=clang O=out )

set -x

for TARGET in "${TARGETS[@]}"; do
    case ${TARGET} in
        "arm-linux-gnueabi") time "${MAKE[@]}" ARCH=arm CROSS_COMPILE="${TARGET}-" KCONFIG_ALLCONFIG=${TC_BLD}/kernel/le.config LD=ld.lld distclean "${CONFIG_TARGET}" zImage modules || exit ${?} ;;
        "aarch64-linux-gnu") time "${MAKE[@]}" ARCH=arm64 CROSS_COMPILE="${TARGET}-" KCONFIG_ALLCONFIG=${TC_BLD}/kernel/le.config LD=ld.lld distclean "${CONFIG_TARGET}" Image.gz modules || exit ${?} ;;
        "powerpc-linux-gnu") time "${MAKE[@]}" ARCH=powerpc CROSS_COMPILE="${TARGET}-" distclean ppc44x_defconfig zImage modules || exit ${?} ;;
        "powerpc64-linux-gnu") time "${MAKE[@]}" ARCH=powerpc CROSS_COMPILE="${TARGET}-" distclean pseries_defconfig vmlinux modules || exit ${?} ;;
        "powerpc64le-linux-gnu") time "${MAKE[@]}" ARCH=powerpc CROSS_COMPILE="${TARGET}-" distclean powernv_defconfig zImage.epapr modules || exit ${?} ;;
        "x86_64-linux-gnu") time "${MAKE[@]}" LD=ld.lld O=out distclean "${CONFIG_TARGET}" bzImage modules || exit ${?} ;;
    esac
done
