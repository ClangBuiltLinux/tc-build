FROM gitpod/workspace-full-vnc

# Install custom tools, runtimes, etc.
# For example "bastet", a command-line tetris clone:
# RUN brew install bastet
#
# More information: https://www.gitpod.io/docs/config-docker/

FROM gitpod/workspace-full

# Install custom tools, runtime, etc.
RUN brew install fzf
RUN brew install meson ninja clang 
RUN apt install bc \
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
            zlib1g-dev lld
            