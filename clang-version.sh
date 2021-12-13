#!/bin/sh
# shellcheck disable=SC2046,SC2086
# SPDX-License-Identifier: GPL-2.0
#
# Print clang's version in a 5 or 6-digit form.

set -e

# Print the compiler name and some version components.
get_compiler_info() {
    cat <<-EOF | "$@" -E -P -x c - 2>/dev/null
	__clang_major__  __clang_minor__  __clang_patchlevel__
	EOF
}

# Convert the version string x.y.z to a canonical 5 or 6-digit form.
get_canonical_version() {
    IFS=.
    set -- $1
    echo $((10000 * $1 + 100 * $2 + $3))
}

# $@ instead of $1 because multiple words might be given, e.g. CC="ccache gcc".
set -- $(get_compiler_info "$@")

get_canonical_version $1.$2.$3
