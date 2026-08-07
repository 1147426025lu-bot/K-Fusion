#!/bin/bash
# Rebuild libktc.a / libktcrasp.a for native aarch64 (Pi 5 / PREEMPT_RT).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=timedc_common__TimedC公共.sh
source "$SCRIPT_DIR/timedc_common__TimedC公共.sh"

timedc_ensure_root
ROOT="$(timedc_root)"
LIBDIR="$ROOT/lib"
SRCDIR="$ROOT/timedc-lib/src"
BUILDDIR="$ROOT/build/aarch64-runtime"
GCC="$(timedc_gcc)"

mkdir -p "$BUILDDIR"
COMMON_FLAGS=(-O2 -fPIC -Wall -Wextra -Wno-unused-parameter -I"$ROOT/include" -I"$SRCDIR")

build_variant() {
    local out="$LIBDIR/libktc.a"

    echo "=== build $out (aarch64 POSIX runtime) ==="
    "$GCC" "${COMMON_FLAGS[@]}" -DKTC_RUNTIME_LIB -c "$SRCDIR/fprofile.c" -o "$BUILDDIR/fprofile.o"
    "$GCC" "${COMMON_FLAGS[@]}" -c "$SRCDIR/cilktc_lib.c" -o "$BUILDDIR/cilktc_lib.o"
    rm -f "$out"
    ar rcs "$out" "$BUILDDIR/fprofile.o" "$BUILDDIR/cilktc_lib.o"
    ranlib "$out"
    file "$out" "$BUILDDIR/fprofile.o"
}

build_variant

echo "Runtime library installed: $LIBDIR/libktc.a"
