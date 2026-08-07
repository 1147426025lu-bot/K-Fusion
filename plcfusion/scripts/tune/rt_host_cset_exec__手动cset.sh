#!/bin/bash
# 在 cset user cpuset (CPU3) 内执行探针压测命令
set -euo pipefail

PROBE_CPU="${JITTER_PROBE_CPU:-3}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v cset >/dev/null 2>&1; then
    echo "❌ 未安装 cset"
    exit 1
fi

if [ "$#" -lt 1 ]; then
    echo "用法: $0 <command...>"
    exit 1
fi

sudo_cmd() {
    if sudo -n true 2>/dev/null; then sudo -n "$@"; else "$@"; fi
}

cleanup() {
    sudo_cmd cset shield --reset 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "🛡️ [ISO-L3] cset shield --exec cpu=${PROBE_CPU} kthread=on"
sudo_cmd cset shield --reset 2>/dev/null || true
sudo_cmd cset shield --cpu="${PROBE_CPU}" --kthread=on --force
sudo_cmd cset shield --exec -- "$@"
