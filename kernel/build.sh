#!/usr/bin/env bash

KRNL=$(dirname "$(readlink -f "${0}")")
TC_BLD=${KRNL%/*}

function header() {
    BORDER="====$(for _ in $(seq ${#1}); do printf '='; done)===="
    printf '\033[1m\n%s\n%s\n%s\n\n\033[0m' "${BORDER}" "== ${1} ==" "${BORDER}"
}

# Parse parameters
function parse_parameters() {
    TARGETS=()
    while ((${#})); do
        case ${1} in
            "--allmodconfig")
                CONFIG_TARGET=allmodconfig
                ;;
            "--allyesconfig")
                CONFIG_TARGET=allyesconfig
                ;;
            "-b" | "--build-folder")
                shift
                BUILD_FOLDER=${1}
                ;;
            "-k" | "--kernel-src")
                shift
                KERNEL_SRC=${1}
                ;;
            "-p" | "--path-override")
                shift
                PATH_OVERRIDE=${1}
                ;;
            "--pgo")
                PGO=true
                ;;
            "-t" | "--targets")
                shift
                IFS=";" read -ra LLVM_TARGETS <<<"${1}"
                # Convert LLVM targets into GNU triples
                for LLVM_TARGET in "${LLVM_TARGETS[@]}"; do
                    case ${LLVM_TARGET} in
                        "AArch64") TARGETS+=("aarch64-linux-gnu") ;;
                        "ARM") TARGETS+=("arm-linux-gnueabi") ;;
                        "Hexagon") TARGETS+=("hexagon-linux-gnu") ;;
                        "Mips") TARGETS+=("mipsel-linux-gnu") ;;
                        "PowerPC") TARGETS+=("powerpc-linux-gnu" "powerpc64-linux-gnu" "powerpc64le-linux-gnu") ;;
                        "RISCV") TARGETS+=("riscv64-linux-gnu") ;;
                        "SystemZ") TARGETS+=("s390x-linux-gnu") ;;
                        "X86") TARGETS+=("x86_64-linux-gnu") ;;
                    esac
                done
                ;;
        esac
        shift
    done
}

function set_default_values() {
    [[ -z ${TARGETS[*]} || ${TARGETS[*]} = "all" ]] && TARGETS=(
        "arm-linux-gnueabi"
        "aarch64-linux-gnu"
        "hexagon-linux-gnu"
        "mipsel-linux-gnu"
        "powerpc-linux-gnu"
        "powerpc64-linux-gnu"
        "powerpc64le-linux-gnu"
        "riscv64-linux-gnu"
        "s390x-linux-gnu"
        "x86_64-linux-gnu"
    )
    [[ -z ${CONFIG_TARGET} ]] && CONFIG_TARGET=defconfig
}

function setup_up_path() {
    # Add the default install bin folder to PATH for binutils
    export PATH=${TC_BLD}/install/bin:${PATH}
    # Add the stage 2 bin folder to PATH for the instrumented clang if we are doing PGO
    ${PGO:=false} && export PATH=${BUILD_FOLDER:=${TC_BLD}/build/llvm}/stage2/bin:${PATH}
    # If the user wants to add another folder to PATH, they can do it with the PATH_OVERRIDE variable
    [[ -n ${PATH_OVERRIDE} ]] && export PATH=${PATH_OVERRIDE}:${PATH}
}

# Turns 'patch -N' from a fatal error to an informational message
function apply_patch {
    PATCH_FILE=${1:?}
    if ! PATCH_OUT=$(patch -Np1 <"${PATCH_FILE}"); then
        PATCH_OUT_OK=$(echo "${PATCH_OUT}" | grep "Reversed (or previously applied) patch detected")
        if [[ -n ${PATCH_OUT_OK} ]]; then
            echo "${PATCH_FILE##*/}: ${PATCH_OUT_OK}"
        else
            echo "${PATCH_OUT}"
            exit 2
        fi
    fi
}

function setup_krnl_src() {
    # A kernel folder can be supplied via '-k' for testing the script
    if [[ -n ${KERNEL_SRC} ]]; then
        cd "${KERNEL_SRC}" || exit 1
    else
        LINUX=linux-5.17
        LINUX_TARBALL=${KRNL}/${LINUX}.tar.xz

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
        PATCH_FILES=()
        for SRC_FILE in "${KRNL}"/*; do
            [[ ${SRC_FILE##*/} = *.patch ]] && PATCH_FILES+=("${SRC_FILE}")
        done
        [[ -n "${PATCH_FILES[*]}" ]] && rm -rf ${LINUX}
        [[ -d ${LINUX} ]] || { tar -xf "${LINUX_TARBALL}" || exit ${?}; }
        cd ${LINUX} || exit 1
        for PATCH_FILE in "${PATCH_FILES[@]}"; do
            apply_patch "${PATCH_FILE}"
        done
    fi
}

# Can the requested architecture use LLVM_IAS=1? This assumes that if the user
# is passing in their own kernel source via '-k', it is either the same or a
# newer version as the one that the script downloads to avoid having a two
# variable matrix.
function can_use_llvm_ias() {
    local llvm_version
    llvm_version=$("$TC_BLD"/clang-version.sh clang)

    case $1 in
        # https://github.com/ClangBuiltLinux/linux/issues?q=is%3Aissue+label%3A%22%5BARCH%5D+arm32%22+label%3A%22%5BTOOL%5D+integrated-as%22+
        arm*)
            if [[ $llvm_version -ge 130000 ]]; then
                return 0
            else
                return 1
            fi
            ;;

        # https://github.com/ClangBuiltLinux/linux/issues?q=is%3Aissue+label%3A%22%5BARCH%5D+arm64%22+label%3A%22%5BTOOL%5D+integrated-as%22+
        # https://github.com/ClangBuiltLinux/linux/issues?q=is%3Aissue+label%3A%22%5BARCH%5D+x86_64%22+label%3A%22%5BTOOL%5D+integrated-as%22+
        aarch64* | x86_64*)
            if [[ $llvm_version -ge 110000 ]]; then
                return 0
            else
                return 1
            fi
            ;;

        hexagon* | mips* | riscv*)
            # All supported versions of LLVM for building the kernel
            return 0
            ;;

        powerpc* | s390*)
            # No supported versions of LLVM for building the kernel
            return 1
            ;;
    esac
}

# Get as command based on prefix and host architecture. See host_arch_target()
# in build-binutils.py.
function get_as() {
    local host_target target_arch

    case "$(uname -m)" in
        armv7l) host_target=arm ;;
        ppc64) host_target=powerpc64 ;;
        ppc64le) host_target=powerpc64le ;;
        ppc) host_target=powerpc ;;
        *) host_target=$(uname -m) ;;
    esac

    # Turn triple (<arch>-<os>-<abi>) into <arch>
    target_arch=${1%%-*}

    if [[ "$target_arch" = "$host_target" ]]; then
        echo "as"
    else
        echo "$1-as"
    fi
}

function check_binutils() {
    # Check for all binutils and build them if necessary
    BINUTILS_TARGETS=()

    for PREFIX in "${TARGETS[@]}"; do
        # We do not need to check for binutils if we can use the integrated assembler
        can_use_llvm_ias "$PREFIX" && continue

        command -v "$(get_as "$PREFIX")" &>/dev/null || BINUTILS_TARGETS+=("${PREFIX}")
    done

    [[ -n "${BINUTILS_TARGETS[*]}" ]] && { "${TC_BLD}"/build-binutils.py -t "${BINUTILS_TARGETS[@]}" || exit ${?}; }
}

function print_tc_info() {
    # Print final toolchain information
    header "Toolchain information"
    clang --version
    for PREFIX in "${TARGETS[@]}"; do
        can_use_llvm_ias "$PREFIX" && continue

        echo
        "$(get_as "$PREFIX")" --version
    done
}

# Checks if clang can be used as a host toolchain. This command will error with
# "No available targets are compatible with triple ..." if clang has been built
# without support for the host target. This is better than keeping a map of
# 'uname -m' against the target's name.
function clang_supports_host_target() {
    echo | clang -x c -c -o /dev/null - &>/dev/null
}

function build_kernels() {
    MAKE_BASE=(make -skj"$(nproc)" KCFLAGS=-Wno-error LLVM=1 O=out)

    clang_supports_host_target || MAKE_BASE+=(HOSTCC=gcc HOSTCXX=g++)

    header "Building kernels"

    # If the user has any CFLAGS in their environment, they can cause issues when building tools/
    # Ideally, the kernel would always clobber user flags via ':=' but that is not always the case
    unset CFLAGS

    set -x

    for TARGET in "${TARGETS[@]}"; do
        MAKE=("${MAKE_BASE[@]}")
        can_use_llvm_ias "$TARGET" || MAKE+=(CROSS_COMPILE="${TARGET}-" LLVM_IAS=0)

        case ${TARGET} in
            "arm-linux-gnueabi")
                case ${CONFIG_TARGET} in
                    defconfig)
                        CONFIGS=(multi_v5_defconfig aspeed_g5_defconfig multi_v7_defconfig)
                        ;;
                    *)
                        CONFIGS=("${CONFIG_TARGET}")
                        ;;
                esac
                for CONFIG in "${CONFIGS[@]}"; do
                    time "${MAKE[@]}" \
                        ARCH=arm \
                        distclean "${CONFIG}" all || exit ${?}
                done
                ;;
            "aarch64-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=arm64 \
                    distclean "${CONFIG_TARGET}" all || exit ${?}
                ;;
            "hexagon-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=hexagon \
                    distclean defconfig all || exit ${?}
                ;;
            "mipsel-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=mips \
                    distclean malta_defconfig all || exit ${?}
                ;;
            "powerpc-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=powerpc \
                    distclean ppc44x_defconfig all || exit ${?}
                ;;
            "powerpc64-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=powerpc \
                    LD="${TARGET}-ld" \
                    distclean pseries_defconfig disable-werror.config all || exit ${?}
                ;;
            "powerpc64le-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=powerpc \
                    distclean powernv_defconfig all || exit ${?}
                ;;
            "riscv64-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=riscv \
                    distclean defconfig all || exit ${?}
                ;;
            "s390x-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=s390 \
                    LD="${TARGET}-ld" \
                    OBJCOPY="${TARGET}-objcopy" \
                    OBJDUMP="${TARGET}-objdump" \
                    distclean defconfig all || exit ${?}
                ;;
            "x86_64-linux-gnu")
                time "${MAKE[@]}" \
                    ARCH=x86_64 \
                    distclean "${CONFIG_TARGET}" all || exit ${?}
                ;;
        esac
    done
}

parse_parameters "${@}"
set_default_values
setup_up_path
setup_krnl_src
check_binutils
print_tc_info
build_kernels
