#!/bin/bash
# gcc wrapper for KTC on Pi: inject header shims before preprocessing.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SHIM="$SCRIPT_DIR/ktc_pi_header_shim.h"
exec gcc -include "$SHIM" -U__SIZEOF_INT128__ "$@"
