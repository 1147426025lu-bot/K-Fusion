#!/bin/bash
# Clone Fixstars cRTOS reference (x86 stack) for NuttX/Jailhouse integration study.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../../plcfusion/scripts/lib/repo_paths__仓库路径.sh"
PRJ="$REPO_ROOT"
ROOT="$CRTOS_UPSTREAM"

if [ -d "$ROOT/.git" ]; then
    echo "cRTOS already at $ROOT"
    exit 0
fi

mkdir -p "$(dirname "$ROOT")"
git clone --depth 1 https://github.com/fixstars/cRTOS.git "$ROOT"
echo "Cloned fixstars/cRTOS → $ROOT"
echo "Note: upstream targets x86_64 + NuttX Linux-ABI port; Pi5 needs ARM NuttX inmate build."
