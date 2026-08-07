#!/bin/bash
# Build NuttX flat binary for Pi5 Jailhouse rpi5-nuttx cell (draft).
# Requires: init_crtos_submodules, jailhouse-enabling kernel (later for load).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../../plcfusion/scripts/lib/repo_paths__仓库路径.sh"
PRJ="$REPO_ROOT"
CRTOS="$CRTOS_UPSTREAM"
NUTTX="$CRTOS/crtos-nuttx"
APPS="$CRTOS/crtos-nuttx-apps"
OUT_BIN="${NUTTX_BIN:-$CRTOS_UPSTREAM/crtos-nuttx/nuttx.bin}"

bash "$SCRIPT_DIR/init_crtos_submodules__初始化cRTOS子模块.sh"

[ -d "$NUTTX" ] || { echo "Missing $NUTTX" >&2; exit 1; }

# Pi5 rpi5-nuttx.c: RAM phys 0x10000000, size 0x60000000, entry loadable at virt 0
export CRTOS_NUTTX_RAM_PHYS=0x10000000
export CRTOS_NUTTX_RAM_SIZE=0x60000000

cd "$NUTTX"
if [ ! -f .config ]; then
    if [ -x tools/configure.sh ] && [ -d boards ]; then
        # Upstream NuttX path — pick closest jailhouse/arm64 if present
        if boards/*/arm64-*jailhouse* >/dev/null 2>&1; then
            cfg="$(ls -d boards/*/arm64-*jailhouse* 2>/dev/null | head -1)"
            board="$(basename "$(dirname "$cfg")")"
            chip="$(basename "$cfg")"
            tools/configure.sh "$board:$chip"
        else
            echo "No jailhouse board defconfig found in $NUTTX" >&2
            echo "Fixstars cRTOS NuttX uses x86 qemu-intel64:crtos — ARM port is manual." >&2
            echo "See docs/crtos/NUTTX_ARM__NuttX移植.md" >&2
            exit 2
        fi
    else
        echo "Run Fixstars configure per Installation.md (x86 reference):" >&2
        echo "  tools/configure.sh qemu-intel64:crtos" >&2
        exit 2
    fi
fi

make -j"$(nproc)"
make export
mkdir -p "$(dirname "$OUT_BIN")"
cp -f nuttx.bin "$OUT_BIN" 2>/dev/null || cp -f nuttx "$OUT_BIN"
echo "✅ NuttX binary → $OUT_BIN"
echo "Load: NUTTX_BIN=$OUT_BIN sudo bash scripts/crtos/deploy_crtos_jailhouse__部署cRTOS.sh nuttx-cell"
