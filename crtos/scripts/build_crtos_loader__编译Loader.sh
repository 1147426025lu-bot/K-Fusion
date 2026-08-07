#!/bin/bash
# Build Fixstars cRTOS loader (x86_64 Linux ABI proxy).
# Native build on Pi 5 (aarch64) is NOT supported — raw_syscall.S is x86_64 only.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../../plcfusion/scripts/lib/repo_paths__仓库路径.sh"
PRJ="$REPO_ROOT"
LOADER="$CRTOS_UPSTREAM/crtos-loader"

bash "$SCRIPT_DIR/init_crtos_submodules__初始化cRTOS子模块.sh"

arch="$(uname -m)"
if [ "$arch" != "x86_64" ]; then
    echo "⚠️  crtos-loader is x86_64-only (SyscallDecoder + raw_syscall.S)." >&2
    echo "Pi 5 needs an aarch64 loader port — see docs/crtos/LOADER_IVSHMEM_ARM__IVSHMEM与Loader.md" >&2
    exit 2
fi

cd "$LOADER"
CXX="${CXX:-g++-9}"
if ! command -v "$CXX" >/dev/null; then
    CXX=g++
fi
make clean 2>/dev/null || true
make CXX="$CXX" LD="${LD:-g++}"

echo "✅ loader → $LOADER/loader"
echo "Run: chrt -f 90 $LOADER/loader <elf> [args...]"
