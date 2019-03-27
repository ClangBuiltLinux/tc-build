# Toolchain build scripts

There are times where a tip of tree LLVM build will have some issue fixed and it isn't available to you, maybe because it isn't in a release or it isn't available through your distribution's package management system. At that point, to get that fix, LLVM needs to be compiled, which sounds scary but is [rather simple](https://llvm.org/docs/GettingStarted.html). The `build-llvm.sh` script takes it a step farther by trying to optimize both LLVM's build time by:

* Trimming down a lot of things that kernel developers don't care about:
  * Documentation
  * LLVM tests
  * Ocaml bindings
  * libfuzzer
* Building with the faster tools available (in order of fastest to slowest):
  * clang + lld
  * clang/gcc + ld.gold
  * clang/gcc + ld.bfd

It also aims to make kernel compilation go a little bit faster by generating a toolchain that is optimized for the host CPU (using `-O2 -march=native -mtune=native`).

## Getting started

These scripts have been tested in a Docker image of the following distributions, with the following packages installed:

* ### Debian/Ubuntu

  ```
  apt install ca-certificates ccache clang cmake curl file gcc g++ git make ninja-build python texinfo zlib1g-dev
  ```

  On Debian Buster or Ubuntu Bionic/Cosmic/Disco, `apt-get install lld` should be added as well for faster compiles.

* ### Fedora

  ```
  dnf install ccache clang cmake gcc gcc-c++ git lld ninja-build python zlib-devel
  ```

* ### Arch Linux

  ```
  pacman -S base-devel ccache clang cmake git lld ninja python
  ```

These scripts should be distribution agnostic. Please feel free to add different distribution install commands here through a pull request.

## build-llvm.sh

By default, `./build-llvm.sh` will clone LLVM, grab the latest binutils tarball (for the LLVMgold.so plugin), and build LLVM, clang, and lld. Run `./build-llvm.sh -h` for more options.

## build-binutils.sh

This script builds a standalone copy of binutils. This might be needed because certain distributions like Arch Linux (whose options the script uses) might symlink `/usr/lib/LLVMgold.so` to `/usr/lib/bfd-plugins` ([source](https://bugs.archlinux.org/task/28479)), which can cause issues when using the system's linker for LTO (even with `LD_LIBRARY_PATH`):

```
bfd plugin: LLVM gold plugin has failed to create LTO module: Unknown attribute kind (60) (Producer: 'LLVM9.0.0svn' Reader: 'LLVM 7.0.1')
```

Having a standalone copy of binutils (ideally in the same folder at the LLVM toolchain so that one `PATH` modification is needed) works around this without any adverse side effects. Another workaround is bind mounting the new `LLVMgold.so` to `/usr/lib/LLVMgold.so`.

## Getting help

Please open an issue on this repo and include your distribution, shell, the command you ran, and the error output.
