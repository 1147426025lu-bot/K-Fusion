#!/bin/bash
# Compile a Timed C (.c) source to native aarch64 binary on Pi.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=timedc_common__TimedC公共.sh
source "$SCRIPT_DIR/timedc_common__TimedC公共.sh"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <source.c> [output_name]" >&2
    exit 1
fi

SRC="$(readlink -f "$1")"
OUT_NAME="${2:-$(basename "$SRC" .c)}"
timedc_ensure_root
ROOT="$(timedc_root)"
BINDIR="$ROOT/bin"
OUT_DIR="${TIMEDC_BUILD_DIR:-$ROOT/build/aarch64-apps}"
GCC="$(timedc_gcc)"

if [ ! -x "$BINDIR/ktcexe" ]; then
    echo "KTC not built. Run: bash scripts/timedc/install_ktc_rpi5__安装KTC.sh" >&2
    exit 1
fi

if [ ! -f "$ROOT/lib/libktc.a" ]; then
    bash "$SCRIPT_DIR/build_runtime_aarch64__重编运行时库.sh"
fi

mkdir -p "$OUT_DIR"
WORK="$OUT_DIR/$OUT_NAME"
rm -rf "$WORK"
mkdir -p "$WORK"
cp "$SRC" "$WORK/source.c"
DECIM_MAX="${TIMEDC_DECIM_MAX:-72000}"
if grep -q 'MAX_DECIM_SAMPLES' "$WORK/source.c"; then
    sed -i "s/#define MAX_DECIM_SAMPLES [0-9]*U/#define MAX_DECIM_SAMPLES ${DECIM_MAX}U/" "$WORK/source.c"
fi
cd "$WORK"

timedc_eval_opam
TIMEDC_GCC_WRAPPER="${TIMEDC_GCC_WRAPPER:-$SCRIPT_DIR/ktc_gcc_wrapper.sh}"
"$ROOT/bin/ktc" --enable-ext0 --link --save-temps --gcc="$TIMEDC_GCC_WRAPPER" source.c -w -lpthread -lrt -lm

BIN="$OUT_DIR/${OUT_NAME}.out"
mv -f a.out "$BIN"
rm -f ./*.dot ./*.i 2>/dev/null || true

echo "Built: $BIN"
file "$BIN"
