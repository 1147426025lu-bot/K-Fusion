#!/bin/bash
# Apply PLCFusion aarch64 patches onto cloned timed-c/ktc.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=timedc_common__TimedC公共.sh
source "$SCRIPT_DIR/timedc_common__TimedC公共.sh"
ROOT="$(timedc_root)"
OVERLAY="$SCRIPT_DIR/ktc_overlay"

if [ ! -d "$ROOT" ]; then
    echo "KTC root missing: $ROOT (run install_ktc_rpi5 first)" >&2
    exit 1
fi

cp -f "$OVERLAY/Makefile" "$ROOT/Makefile"
cp -f "$OVERLAY/timedc-lib/src/fprofile.c" "$ROOT/timedc-lib/src/fprofile.c"
cp -f "$OVERLAY/timedc-lib/src/cillib.h" "$ROOT/timedc-lib/src/cillib.h"
echo "Applied KTC overlay → $ROOT"
