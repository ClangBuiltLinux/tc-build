# Toolchain build scripts

There are times where a tip of tree LLVM build will have some issue fixed and it isn't available to you, maybe because it isn't in a release or it isn't available through your distribution's package management system. At that point, to get that fix, LLVM needs to be compiled, which sounds scary but is [rather simple](https://llvm.org/docs/GettingStarted.html). The `build-llvm.py` script takes it a step farther by trying to optimize both LLVM's build time by:

* Trimming down a lot of things that kernel developers don't care about:
  * Documentation
  * LLVM tests
  * Ocaml bindings
  * libfuzzer
* Building with the faster tools available (in order of fastest to slowest):
  * clang + lld
  * clang/gcc + ld.gold
  * clang/gcc + ld.bfd

## Getting started

These scripts have been tested in a Docker image of the following distributions, with the following packages installed:

* ### Debian/Ubuntu

  ```
  apt install bc \
              binutils-dev \
              bison \
              ca-certificates \
              ccache \
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
              make \
              ninja-build \
              python3 \
              texinfo \
              u-boot-tools \
              xz-utils \
              zlib1g-dev
  ```

  On Debian Buster or Ubuntu Bionic/Cosmic/Disco, `apt install lld` should be added as well for faster compiles.

* ### Fedora

  ```
  dnf install bc \
              binutils-devel \
              bison \
              ccache \
              clang \
              cmake \
              elfutils-libelf-devel \
              flex \
              gcc \
              gcc-c++ \
              git \
              lld \
              make \
              ninja-build \
              openssl-devel \
              python3 \
              texinfo-tex \
              uboot-tools \
              xz \
              zlib-devel
  ```

* ### Arch Linux

  ```
  pacman -S base-devel \
            bison \
            ccache \
            clang \
            cmake \
            flex \
            git \
            libelf \
            lld \
            ninja \
            openssl \
            python3 \
            uboot-tools
  ```

Python 3.5.3+ is recommended, as that is what the script has been tested against. These scripts should be distribution agnostic. Please feel free to add different distribution install commands here through a pull request.

## build-llvm.py

By default, `./build-llvm.py` will clone LLVM, grab the latest binutils tarball (for the LLVMgold.so plugin), and build LLVM, clang, and lld, and install them into `install`.

The script automatically clones and manages the [`llvm-project`](https://github.com/llvm/llvm-project). If you would like to do this management yourself, such as downloading a release tarball from [releases.llvm.org](https://releases.llvm.org/), doing a more aggressive shallow clone (versus what is done in the script via `--shallow-clone`), or doing a bisection of LLVM, you just need to make sure that your source is in an `llvm-project` folder within the root of this repository and pass `--no-update` into the script. See [this comment](https://github.com/ClangBuiltLinux/tc-build/issues/75#issuecomment-604374071) for an example.

Run `./build-llvm.py -h` for more options and information.

## build-binutils.py

This script builds a standalone copy of binutils. By default, `./build-binutils.py` will download the [latest stable version](https://www.gnu.org/software/binutils/) of binutils, build for all architectures we currently care about (see the help text or script for the full list), and install them into `install`. Run `./build-binutils.py -h` for more options.

Building a standalone copy of binutils might be needed because certain distributions like Arch Linux (whose options the script uses) might symlink `/usr/lib/LLVMgold.so` to `/usr/lib/bfd-plugins` ([source](https://bugs.archlinux.org/task/28479)), which can cause issues when using the system's linker for LTO (even with `LD_LIBRARY_PATH`):

```
bfd plugin: LLVM gold plugin has failed to create LTO module: Unknown attribute kind (60) (Producer: 'LLVM9.0.0svn' Reader: 'LLVM 7.0.1')
```

Having a standalone copy of binutils (ideally in the same folder at the LLVM toolchain so that only one `PATH` modification is needed) works around this without any adverse side effects. Another workaround is bind mounting the new `LLVMgold.so` to `/usr/lib/LLVMgold.so`.

## Contributing

This repository openly welcomes pull requests! There are a few presubmit checks that run to make sure the code stays consistently formatted and free of bugs.

1. All Python files must be passed through [`yapf`](https://github.com/google/yapf). See the installation section for how to get it (it may also be available through your package manager).

2. All shell files must be passed through [`shfmt`](https://github.com/mvdan/sh) (specifically `shfmt -ci -i 4 -w`) and emit no [`shellcheck`](https://github.com/koalaman/shellcheck) warnings.

The presubmit checks will do these things for you and fail if the code is not formatted properly or has a shellcheck warning. Running these tools on the command line before submitting will make it easier to get your code merged.

Additionally, please write a detailed commit message about why you are submitting your change.

## Getting help

Please open an issue on this repo and include your distribution, shell, the command you ran, and the error output.
