#!/usr/bin/env bash

# Get the tc-build folder's absolute path, which is the directory above this one
TC_BLD=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)
[[ -z ${TC_BLD} ]] && exit 1

function header() {
    BORDER="====$(for _ in $(seq ${#1}); do printf '='; done)===="
    printf '\033[1m\n%s\n%s\n%s\n\n\033[0m' "${BORDER}" "== ${1} ==" "${BORDER}"
}

# Parse parameters
while ((${#})); do
    case ${1} in
        "--allyesconfig")
            CONFIG_TARGET=allyesconfig
            ;;
        "-b" | "--build-folder")
            shift
            BUILD_FOLDER=${1}
            ;;
        "-p" | "--path-override")
            shift
            PATH_OVERRIDE=${1}
            ;;
        "--pgo")
            shift
            PGO=${1}
            ;;
        "-s" | "--src-folder")
            shift
            SRC_FOLDER=${1}
            ;;
        "-t" | "--targets")
            shift
            IFS=";" read -ra LLVM_TARGETS <<<"${1}"
            # Convert LLVM targets into GNU triples
            for LLVM_TARGET in "${LLVM_TARGETS[@]}"; do
                case ${LLVM_TARGET} in
                    "AArch64") TARGETS=("${TARGETS[@]}" "aarch64-linux-gnu") ;;
                    "ARM") TARGETS=("${TARGETS[@]}" "arm-linux-gnueabi") ;;
                    "Mips") TARGETS=("${TARGETS[@]}" "mipsel-linux-gnu") ;;
                    "PowerPC") TARGETS=("${TARGETS[@]}" "powerpc-linux-gnu" "powerpc64-linux-gnu" "powerpc64le-linux-gnu") ;;
                    "RISCV") TARGETS=("${TARGETS[@]}" "riscv64-linux-gnu") ;;
                    "SystemZ") TARGETS=("${TARGETS[@]}" "s390x-linux-gnu") ;;
                    "X86") TARGETS=("${TARGETS[@]}" "x86_64-linux-gnu") ;;
                esac
            done
            ;;
    esac
    shift
done
[[ -z ${TARGETS[*]} ]] && TARGETS=(
    "arm-linux-gnueabi"
    "aarch64-linux-gnu"
    "mipsel-linux-gnu"
    "powerpc-linux-gnu"
    "powerpc64-linux-gnu"
    "powerpc64le-linux-gnu"
    "riscv64-linux-gnu"
    "s390x-linux-gnu"
    "x86_64-linux-gnu"
)
[[ -z ${CONFIG_TARGET} ]] && CONFIG_TARGET=defconfig

# Add the default install bin folder to PATH for binutils
export PATH=${TC_BLD}/install/bin:${PATH}
# Add the stage 2 bin folder to PATH for the instrumented clang if we are doing PGO
${PGO:=false} && export PATH=${BUILD_FOLDER:=${TC_BLD}/build/llvm}/stage2/bin:${PATH}
# If the user wants to add another folder to PATH, they can do it with the PATH_OVERRIDE variable
[[ -n ${PATH_OVERRIDE} ]] && export PATH=${PATH_OVERRIDE}:${PATH}

# A kernel folder can be supplied via '-f' for testing the script
if [[ -n ${SRC_FOLDER} ]]; then
    cd "${SRC_FOLDER}" || exit 1
else
    LINUX=linux-5.10.14
    LINUX_TARBALL=${TC_BLD}/kernel/${LINUX}.tar.xz
    LINUX_PATCH=${TC_BLD}/kernel/${LINUX}-${CONFIG_TARGET}.patch

    # If we don't have the source tarball, download and verify it
    if [[ ! -f ${LINUX_TARBALL} ]]; then
        curl -LSso "${LINUX_TARBALL}" https://cdn.kernel.org/pub/linux/kernel/v5.x/"${LINUX_TARBALL##*/}"

        (
            cd "${LINUX_TARBALL%/*}" || exit 1
            sha256sum -c "${LINUX_TARBALL}".sha256 --quiet
        ) || {
            echo "Linux tarball verification failed! Please remove '${LINUX_TARBALL}' and try again."
            exit 1
        }
    fi

    # If there is a patch to apply, remove the folder so that we can patch it accurately (we cannot assume it has already been patched)
    [[ -f ${LINUX_PATCH} ]] && rm -rf ${LINUX}
    [[ -d ${LINUX} ]] || { tar -xf "${LINUX_TARBALL}" || exit ${?}; }
    cd ${LINUX} || exit 1
    [[ -f ${LINUX_PATCH} ]] && { patch -p1 <"${LINUX_PATCH}" || exit ${?}; }
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
    command -v "${COMMAND}" &>/dev/null || BINUTILS_TARGETS=("${BINUTILS_TARGETS[@]}" "${PREFIX}")
done
[[ -n "${BINUTILS_TARGETS[*]}" ]] && { "${TC_BLD}"/build-binutils.py -t "${BINUTILS_TARGETS[@]}" || exit ${?}; }

# Print final toolchain information
header "Toolchain information"
clang --version
for PREFIX in "${TARGETS[@]}"; do
    echo
    case ${PREFIX} in
        x86_64-linux-gnu) as --version ;;
        *) "${PREFIX}"-as --version ;;
    esac
done

# SC2191: The = here is literal. To assign by index, use ( [index]=value ) with no spaces. To keep as literal, quote it.
# shellcheck disable=SC2191
MAKE=(make -skj"$(nproc)" LLVM=1 O=out)
case "$(uname -m)" in
    arm*) [[ ${TARGETS[*]} =~ arm ]] || NEED_GCC=true ;;
    aarch64) [[ ${TARGETS[*]} =~ aarch64 ]] || NEED_GCC=true ;;
    mips*) [[ ${TARGETS[*]} =~ mips ]] || NEED_GCC=true ;;
    ppc*) [[ ${TARGETS[*]} =~ powerpc ]] || NEED_GCC=true ;;
    s390*) [[ ${TARGETS[*]} =~ s390 ]] || NEED_GCC=true ;;
    riscv*) [[ ${TARGETS[*]} =~ riscv ]] || NEED_GCC=true ;;
    i*86 | x86*) [[ ${TARGETS[*]} =~ x86_64 ]] || NEED_GCC=true ;;
esac
${NEED_GCC:=false} && MAKE+=(HOSTCC=gcc HOSTCXX=g++)

header "Building kernels"

set -x

for TARGET in "${TARGETS[@]}"; do
    case ${TARGET} in
        "arm-linux-gnueabi")
            time \
                "${MAKE[@]}" \
                ARCH=arm \
                CROSS_COMPILE="${TARGET}-" \
                KCONFIG_ALLCONFIG=<(echo CONFIG_CPU_BIG_ENDIAN=n) \
                distclean "${CONFIG_TARGET}" zImage modules || exit ${?}
            ;;
        "aarch64-linux-gnu")
            time \
                "${MAKE[@]}" \
                ARCH=arm64 \
                CROSS_COMPILE="${TARGET}-" \
                KCONFIG_ALLCONFIG=<(echo CONFIG_CPU_BIG_ENDIAN=n) \
                distclean "${CONFIG_TARGET}" Image.gz modules || exit ${?}
            ;;
        "mipsel-linux-gnu")
            time \
                "${MAKE[@]}" \
                ARCH=mips \
                CROSS_COMPILE="${TARGET}-" \
                distclean malta_kvm_guest_defconfig vmlinux modules || exit ${?}
            ;;
        "powerpc-linux-gnu")
            time \
                "${MAKE[@]}" \
                ARCH=powerpc \
                CROSS_COMPILE="${TARGET}-" \
                distclean ppc44x_defconfig zImage modules || exit ${?}
            ;;
        "powerpc64-linux-gnu")
            time \
                "${MAKE[@]}" \
                ARCH=powerpc \
                LD="${TARGET}-ld" \
                CROSS_COMPILE="${TARGET}-" \
                distclean pseries_defconfig vmlinux modules || exit ${?}
            ;;
        "powerpc64le-linux-gnu")
            time \
                "${MAKE[@]}" \
                ARCH=powerpc \
                CROSS_COMPILE="${TARGET}-" \
                distclean powernv_defconfig zImage.epapr modules || exit ${?}
            ;;
        "riscv64-linux-gnu")
            RISCV_MAKE=(
                "${MAKE[@]}"
                ARCH=riscv
                CROSS_COMPILE="${TARGET}-"
                LD="${TARGET}-ld"
                LLVM_IAS=1
            )
            time "${RISCV_MAKE[@]}" distclean defconfig || exit ${?}
            # https://github.com/ClangBuiltLinux/linux/issues/1143
            grep -q "config EFI" arch/riscv/Kconfig && scripts/config --file out/.config -d EFI
            time "${RISCV_MAKE[@]}" Image.gz modules || exit ${?}
            ;;
        "s390x-linux-gnu")
            time \
                "${MAKE[@]}" \
                ARCH=s390 \
                CROSS_COMPILE="${TARGET}-" \
                LD="${TARGET}-ld" \
                OBJCOPY="${TARGET}-objcopy" \
                OBJDUMP="${TARGET}-objdump" \
                distclean defconfig bzImage modules || exit ${?}
            ;;
        "x86_64-linux-gnu")
            time \
                "${MAKE[@]}" \
                distclean "${CONFIG_TARGET}" bzImage modules || exit ${?}
            ;;
    esac
done
