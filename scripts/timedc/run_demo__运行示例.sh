#!/bin/bash
# Quick smoke: compile and run KTC upstream demo1 (soft sdelay).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEMO="$ROOT/third_party/ktc/examples/sdelay.c"

bash "$SCRIPT_DIR/build_timedc__编译TimedC.sh" "$DEMO" demo1
BIN="$ROOT/third_party/ktc/build/aarch64-apps/demo1.out"
echo "=== run $BIN ==="
"$BIN"
