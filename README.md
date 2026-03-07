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

These scripts have been tested in a Docker image of the following distributions with the following packages installed. LLVM has [minimum host tool version requirements](https://llvm.org/docs/GettingStarted.html#software) so the latest stable version of the chosen distribution should be used whenever possible to ensure recent versions of the tools are used. Build errors from within LLVM are expected if the tool version is not recent enough, in which case it will need to be built from source or installed through other means. The scripts have been validated against Python 3.9 and newer. These scripts should be distribution agnostic. Please feel free to add different distribution install commands here through a pull request.

* ### Debian/Ubuntu

  ```
  apt install bc \
              binutils-dev \
              bison \
              build-essential \
              ca-certificates \
              ccache \
              clang \
              cmake \
              curl \
              file \
              flex \
              git \
              libelf-dev \
              libssl-dev \
              libstdc++-$(apt list libstdc++6 2>/dev/null | grep -Eos '[0-9]+\.[0-9]+\.[0-9]+' | head -1 | cut -d . -f 1)-dev \
              lld \
              make \
              ninja-build \
              pkg-config \
              python3-dev \
              texinfo \
              u-boot-tools \
              xz-utils \
              zlib1g-dev
  ```

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

* ### Arch Linux / Manjaro

  ```
  pacman -S base-devel \
            bc \
            bison \
            ccache \
            clang \
            cpio \
            cmake \
            flex \
            git \
            libelf \
            lld \
            llvm \
            ninja \
            openssl \
            python3 \
            uboot-tools
  ```
* ### Gentoo

  A convenient way to manage all the required packages is to use [package sets](https://wiki.gentoo.org/wiki//etc/portage/sets). Create a set for tc-build:

  ```
  $ sudo mkdir /etc/portage/sets && sudo $EDITOR /etc/portage/sets/tc-build
  ```

  With the file `tc-build` containing:

  ```
  llvm-core/clang
  llvm-core/lld
  sys-libs/binutils-libs
  dev-build/cmake
  dev-vcs/git
  dev-util/ccache
  dev-libs/elfutils
  app-arch/cpio
  dev-embedded/u-boot-tools
  ```

  Afterwards emerge the set:

  ```
  $ sudo emerge --ask --verbose @tc-build
  ```

  To avoid building from source Gentoo also provides [binary packages](https://wiki.gentoo.org/wiki/Binary_package_guide).

  ```
  $ sudo emerge --ask --verbose --getbinpkg @tc-build
  ```

  Or for short:

  ```
  $ sudo emerge -avg @tc-build
  ```

## build-llvm.py

By default, `./build-llvm.py` will clone LLVM, build LLVM, clang, and lld, and install them into `install`.

The script automatically clones and manages the [`llvm-project`](https://github.com/llvm/llvm-project). If you would like to do this management yourself, such as downloading a release tarball from [releases.llvm.org](https://releases.llvm.org/), doing a more aggressive shallow clone (versus what is done in the script via `--shallow-clone`), or doing a bisection of LLVM, provide the source via `--llvm-folder`.

Run `./build-llvm.py -h` for more options and information.

## build-binutils.py

This script builds a standalone copy of binutils. By default, `./build-binutils.py` will download the [latest stable version](https://www.gnu.org/software/binutils/) of binutils, build for all architectures we currently care about (see the help text or script for the full list), and install them into `install`. Run `./build-binutils.py -h` for more options.

## build-rust.py

By default, `./build-rust.py` will clone Rust and build it using an LLVM previously built by `./build-llvm.py`, e.g.:

```sh
./build-llvm.py && ./build-rust.py
```

This script does not apply any Rust-specific patches to LLVM.

Run `./build-rust.py -h` for more options and information.

## Contributing

This repository openly welcomes pull requests! There are a few presubmit checks that run to make sure the code stays consistently formatted and free of bugs.

1. All Python files must be passed through [`ruff`](https://github.com/astral-sh/ruff) for linting and [`yapf`](https://github.com/google/yapf) for style.

2. All shell files must be passed through [`shellcheck`](https://github.com/koalaman/shellcheck) for linting and [`shfmt`](https://github.com/mvdan/sh) (specifically `shfmt -ci -i 4 -w`) for style.

These presubmit checks run via GitHub Actions on push or pull request, failing if the code is not formatted properly or has lint warnings. Running these tools on the command line before submitting will make it easier to get your code merged.

Additionally, please write a detailed commit message about why you are submitting your change.

## Getting help

Please open an issue on this repo and include your distribution, shell, the command you ran, and the error output.
