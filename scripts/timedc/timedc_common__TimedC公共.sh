#!/bin/bash
# shellcheck disable=SC2034
set -euo pipefail

TIMEDC_ROOT_DEFAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/third_party/ktc"

timedc_root() {
    echo "${TIMEDC_ROOT:-$TIMEDC_ROOT_DEFAULT}"
}

timedc_bin_dir() {
    echo "$(timedc_root)/bin"
}

timedc_ensure_root() {
    local root
    root="$(timedc_root)"
    if [ ! -d "$root/src" ]; then
        echo "Timed C (KTC) not found at $root" >&2
        echo "Run: bash scripts/timedc/install_ktc_rpi5__安装KTC.sh" >&2
        exit 1
    fi
}

TIMEDC_OPAM_SWITCH="${TIMEDC_OPAM_SWITCH:-ktc-4.14}"

timedc_eval_opam() {
    if command -v opam >/dev/null 2>&1; then
        # shellcheck disable=SC1090
        eval "$(opam env --switch="$TIMEDC_OPAM_SWITCH" 2>/dev/null || opam config env)"
    fi
}

timedc_gcc() {
    echo "${TIMEDC_GCC:-gcc}"
}
