#!/usr/bin/env bash

krnl=$(dirname "$(readlink -f "$0")")
tc_bld=${krnl%/*}

function header() {
    border="===$(for _ in $(seq ${#1}); do printf '='; done)==="
    printf '\033[1m\n%s\n%s\n%s\n\n\033[0m' "$border" "== $1 ==" "$border"
}

# Parse parameters
function parse_parameters() {
    targets=()
    while (($#)); do
        case $1 in
            --allmodconfig)
                config_target=allmodconfig
                ;;

            --allyesconfig)
                config_target=allyesconfig
                ;;

            -b | --build-folder)
                shift
                build_folder=$1
                ;;

            --bolt-*)
                bolt=$(echo "$1" | cut -d - -f 4)
                ;;

            -i | --install-folder)
                shift
                install_folder=$1
                ;;

            -k | --kernel-src)
                shift
                kernel_src=$1
                ;;

            -p | --path-override)
                shift
                path_override=$1
                ;;

            --pgo)
                pgo=true
                ;;

            -t | --targets)
                shift
                IFS=";" read -ra llvm_targets <<<"$1"
                # Convert LLVM targets into GNU triples
                for llvm_target in "${llvm_targets[@]}"; do
                    case $llvm_target in
                        AArch64) targets+=(aarch64-linux-gnu) ;;
                        ARM) targets+=(arm-linux-gnueabi) ;;
                        Hexagon) targets+=(hexagon-linux-gnu) ;;
                        Mips) targets+=(mipsel-linux-gnu) ;;
                        PowerPC) targets+=(powerpc-linux-gnu powerpc64-linux-gnu powerpc64le-linux-gnu) ;;
                        RISCV) targets+=(riscv64-linux-gnu) ;;
                        SystemZ) targets+=(s390x-linux-gnu) ;;
                        X86) targets+=(x86_64-linux-gnu) ;;
                    esac
                done
                ;;
        esac
        shift
    done
}

function set_default_values() {
    [[ -z ${targets[*]} || ${targets[*]} = "all" ]] && targets=(
        arm-linux-gnueabi
        aarch64-linux-gnu
        hexagon-linux-gnu
        mipsel-linux-gnu
        powerpc-linux-gnu
        powerpc64-linux-gnu
        powerpc64le-linux-gnu
        riscv64-linux-gnu
        s390x-linux-gnu
        x86_64-linux-gnu
    )
    [[ -z $config_target ]] && config_target=defconfig
}

function setup_up_path() {
    # Add the default install bin folder to PATH for binutils
    export PATH=$tc_bld/install/bin:$PATH

    # Add the stage 2 bin folder to PATH for the instrumented clang if we are doing PGO
    ${pgo:=false} && export PATH=${build_folder:=$tc_bld/build/llvm}/stage2/bin:$PATH

    # Add the user's install folder if it is not in the PATH already if we are doing bolt
    if [[ -n $bolt ]] && [[ -n $install_folder ]]; then
        install_bin=$install_folder/bin
        echo "$PATH" | grep -q "$install_bin:" || export PATH=$install_bin:$PATH
    fi

    # If the user wants to add another folder to PATH, they can do it with the path_override variable
    [[ -n $path_override ]] && export PATH=$path_override:$PATH
}

# Turns 'patch -N' from a fatal error to an informational message
function apply_patch {
    patch_file=${1:?}
    if ! patch_out=$(patch -Np1 <"$patch_file"); then
        patch_out_ok=$(echo "$patch_out" | grep "Reversed (or previously applied) patch detected")
        if [[ -n $patch_out_ok ]]; then
            echo "${patch_file##*/}: $patch_out_ok"
        else
            echo "$patch_out"
            exit 2
        fi
    fi
}

function setup_krnl_src() {
    # A kernel folder can be supplied via '-k' for testing the script
    if [[ -n $kernel_src ]]; then
        cd "$kernel_src" || exit
    else
        linux="linux-6.1.7"
        linux_tarball=$krnl/$linux.tar.xz

        # If we don't have the source tarball, download and verify it
        if [[ ! -f $linux_tarball ]]; then
            curl -LSso "$linux_tarball" https://cdn.kernel.org/pub/linux/kernel/v6.x/"${linux_tarball##*/}"

            (
                cd "${linux_tarball%/*}" || exit
                sha256sum -c "$linux_tarball".sha256 --quiet
            ) || {
                echo "Linux tarball verification failed! Please remove '$linux_tarball' and try again."
                exit 1
            }
        fi

        # If there is a patch to apply, remove the folder so that we can patch it accurately (we cannot assume it has already been patched)
        patch_files=()
        for src_file in "$krnl"/*; do
            [[ ${src_file##*/} = *.patch ]] && patch_files+=("$src_file")
        done
        [[ -n "${patch_files[*]}" ]] && rm -rf $linux
        [[ -d $linux ]] || { tar -xf "$linux_tarball" || exit; }
        cd $linux || exit
        for patch_file in "${patch_files[@]}"; do
            apply_patch "$patch_file"
        done
    fi
}

function set_llvm_version() {
    llvm_version=$("$tc_bld"/clang-version.sh clang)
}

# Can the requested architecture use LLVM_IAS=1? This assumes that if the user
# is passing in their own kernel source via '-k', it is either the same or a
# newer version as the one that the script downloads to avoid having a two
# variable matrix.
function can_use_llvm_ias() {
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

        hexagon* | mips* | riscv* | s390*)
            # All supported versions of LLVM for building the kernel
            return 0
            ;;

        powerpc64le-linux-gnu)
            if [[ $llvm_version -ge 140000 ]]; then
                return 0
            else
                return 1
            fi
            ;;

        powerpc*)
            # No supported versions of LLVM for building the kernel
            return 1
            ;;
    esac
}

# Does the requested architecture need binutils? Normally, this is answered by
# can_use_llvm_ias() but powerpc64le stills needs binutils for now due to the
# boot wrapper, despite being able to use the integrated assembler and LLVM
# binutils for the rest of the kernel build.
function needs_binutils() {
    case $1 in
        # powerpc64le needs binutils for the boot wrapper:
        #   - https://github.com/ClangBuiltLinux/linux/issues/1601
        # s390x needs binutils for ld, objcopy, and objdump:
        #   - https://github.com/ClangBuiltLinux/linux/issues/1524
        #   - https://github.com/ClangBuiltLinux/linux/issues/1530
        #   - https://github.com/ClangBuiltLinux/linux/issues/859
        powerpc64le-linux-gnu | s390x-linux-gnu)
            return 0
            ;;
        *)
            ! can_use_llvm_ias "$1"
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
    binutils_targets=()

    for prefix in "${targets[@]}"; do
        needs_binutils "$prefix" || continue

        command -v "$(get_as "$prefix")" &>/dev/null || binutils_targets+=("$prefix")
    done

    [[ -n "${binutils_targets[*]}" ]] && { "$tc_bld"/build-binutils.py -t "${binutils_targets[@]}" || exit; }
}

function print_tc_info() {
    # Print final toolchain information
    header "Toolchain information"
    clang --version
    for prefix in "${targets[@]}"; do
        needs_binutils "$prefix" || continue

        echo
        "$(get_as "$prefix")" --version
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
    make_base=(make -skj"$(nproc)" KCFLAGS=-Wno-error LLVM=1 O=out)
    [[ $bolt = "instrumentation" ]] && make_base+=(CC=clang.inst)

    if clang_supports_host_target; then
        [[ $bolt = "instrumentation" ]] && make_base+=(HOSTCC=clang.inst)
    else
        make_base+=(HOSTCC=gcc HOSTCXX=g++)
    fi

    header "Building kernels ($(make -s kernelversion))"

    # If the user has any CFLAGS in their environment, they can cause issues when building tools/
    # Ideally, the kernel would always clobber user flags via ':=' but that is not always the case
    unset CFLAGS

    set -x

    for target in "${targets[@]}"; do
        make=("${make_base[@]}")
        needs_binutils "$target" && make+=(CROSS_COMPILE="$target-")
        can_use_llvm_ias "$target" || make+=(LLVM_IAS=0)

        case $target in
            arm-linux-gnueabi)
                case $config_target in
                    defconfig)
                        configs=(multi_v5_defconfig aspeed_g5_defconfig multi_v7_defconfig)
                        ;;
                    *)
                        configs=("$config_target")
                        ;;
                esac
                for config in "${configs[@]}"; do
                    time "${make[@]}" \
                        ARCH=arm \
                        distclean "$config" all || exit
                done
                ;;

            aarch64-linux-gnu)
                time "${make[@]}" \
                    ARCH=arm64 \
                    distclean "$config_target" all || exit
                ;;

            hexagon-linux-gnu)
                time "${make[@]}" \
                    ARCH=hexagon \
                    distclean "$config_target" all || exit
                ;;

            mipsel-linux-gnu)
                time "${make[@]}" \
                    ARCH=mips \
                    distclean malta_defconfig all || exit
                ;;

            powerpc-linux-gnu)
                time "${make[@]}" \
                    ARCH=powerpc \
                    distclean pmac32_defconfig all || exit
                ;;

            powerpc64-linux-gnu)
                time "${make[@]}" \
                    ARCH=powerpc \
                    LD="$target-ld" \
                    distclean pseries_defconfig disable-werror.config all || exit
                ;;

            powerpc64le-linux-gnu)
                time "${make[@]}" \
                    ARCH=powerpc \
                    distclean powernv_defconfig all || exit
                ;;

            riscv64-linux-gnu)
                time "${make[@]}" \
                    ARCH=riscv \
                    distclean "$config_target" all || exit
                ;;

            s390x-linux-gnu)
                # https://git.kernel.org/linus/8218827b73c6e41029438a2d3cc573286beee914
                [[ $llvm_version -lt 140000 ]] && continue

                time "${make[@]}" \
                    ARCH=s390 \
                    LD="$target-ld" \
                    OBJCOPY="$target-objcopy" \
                    OBJDUMP="$target-objdump" \
                    distclean "$config_target" all || exit
                ;;

            x86_64-linux-gnu)
                time "${make[@]}" \
                    ARCH=x86_64 \
                    distclean "$config_target" all || exit
                ;;
        esac
    done
}

parse_parameters "$@"
set_default_values
setup_up_path
setup_krnl_src
set_llvm_version
check_binutils
print_tc_info
build_kernels
