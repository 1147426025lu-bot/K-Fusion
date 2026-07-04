#!/bin/bash
# Plot dual PNGs (jitter 4-panel + latency timeline) from jitter.bin or cyclictest log.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRJ="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLOT="$PRJ/src/plot_frequency_polygon__抖动绘图.py"

label="${1:-}"
kind="${2:-soak}"
out_base="${3:-}"
jitter_bin="${4:-}"
input_log="${5:-}"

if [ -z "$label" ] || [ -z "$out_base" ]; then
    echo "Usage: $0 <label> <soak|stress> <out_base> [jitter.bin] [input.log]" >&2
    exit 1
fi

mkdir -p "$(dirname "$out_base")"
extra=()
if [ -n "$jitter_bin" ] && [ -f "$jitter_bin" ]; then
    extra=(--input-jitter-bin "$jitter_bin")
elif [ -n "$input_log" ] && [ -f "$input_log" ]; then
    extra=(--input-log "$input_log")
else
    echo "⚠️  skip plot [$label/$kind]: no jitter.bin or log"
    exit 0
fi

python3 "$PLOT" \
    "${extra[@]}" \
    --output "${out_base}_${kind}.png" \
    --latency-output "${out_base}_${kind}_latency.png" \
    || { echo "⚠️  plot failed [$label/$kind]"; exit 0; }

echo "✅ dual plot [$label/$kind]: ${out_base}_${kind}.png + _latency.png"
