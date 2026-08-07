#!/bin/bash
# Free disk for jailhouse kernel build (~12 GiB needed for full compile).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../../kfusion/scripts/lib/repo_paths__仓库路径.sh"
PRJ="$REPO_ROOT"
TARGET_GB="${TARGET_GB:-12}"

free_gb() {
    df -k "$PRJ" | awk 'NR==2{printf "%d", $4/1024/1024}'
}

echo "=== Disk before ==="
df -h "$PRJ"
echo "Free: ~$(free_gb) GiB (target >= ${TARGET_GB} GiB for kernel build)"
echo

# Safe system cleanups (no user project data)
if [ "$(id -u)" -eq 0 ]; then
    apt-get clean 2>/dev/null || true
    journalctl --vacuum-size=64M 2>/dev/null || true
else
    sudo apt-get clean 2>/dev/null || true
    sudo journalctl --vacuum-size=64M 2>/dev/null || true
fi

echo "=== Disk after apt/journal cleanup ==="
df -h "$PRJ"
echo "Free: ~$(free_gb) GiB"
echo

if [ "$(free_gb)" -ge "$TARGET_GB" ]; then
    echo "✅ Enough space — run:"
    echo "   bash scripts/crtos/build_jailhouse_kernel_rpi5__编译JAIL内核.sh"
    exit 0
fi

echo "⚠️  Still short of ${TARGET_GB} GiB. Optional manual frees:"
echo
echo "  # Remote IDE caches (re-download on next connect):"
echo "  rm -rf ~/.vscode-server/extensions/*"
echo "  rm -rf ~/.cursor-server/extensions/*"
echo
echo "  # OCaml opam if unused (~1.3 GiB):"
echo "  rm -rf ~/.opam"
echo
echo "  # Or build kernel on external USB (example):"
echo "  export LINUX_ROOT=/media/pi/USB/linux-rpi"
echo "  bash scripts/crtos/build_jailhouse_kernel_rpi5__编译JAIL内核.sh"
echo
echo "Clone-only (needs ~3 GiB, no compile):"
echo "  CLONE_ONLY=1 bash scripts/crtos/build_jailhouse_kernel_rpi5__编译JAIL内核.sh"
echo
echo "If git times out (curl 56), use resumable tarball:"
echo "  CLONE_METHOD=tarball CLONE_ONLY=1 bash scripts/crtos/build_jailhouse_kernel_rpi5__编译JAIL内核.sh"
echo "  # interrupted? re-run same command — wget -c resumes"
