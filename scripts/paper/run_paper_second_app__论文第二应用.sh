#!/bin/bash
# ============================================================================
# run_paper_second_app__论文第二应用.sh — signaltest 融合短测（非 cyclictest 案例）
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MANIFEST="$PROJECT_ROOT/manifests/manifest_signaltest__信号测试.env"
DURATION_SEC="${DURATION_SEC:-120}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$PROJECT_ROOT/results/paper/second_app"
LOG="$OUT_DIR/signaltest_${STAMP}_${DURATION_SEC}s.log"
CSV="$OUT_DIR/signaltest_runs.csv"
mkdir -p "$OUT_DIR"

[ ! -f "$CSV" ] && echo "timestamp,app,duration_sec,insmod_ok,dmesg_tail,log" >"$CSV"

plc_check_sudo 1
{
    echo "=== paper second_app: signaltest ${DURATION_SEC}s ==="
    bash "$PROJECT_ROOT/scripts/plc_fuse__内核化主流程.sh" "$MANIFEST"
    if lsmod | grep -q '^signaltest_mod'; then
        bash "$PROJECT_ROOT/scripts/safe_rmmod_fused__安全卸载.sh" signaltest_mod || true
    fi
    bash "$PROJECT_ROOT/scripts/ignite_fused__通用ko构建.sh" "$MANIFEST"
    sudo -n dmesg -c >/dev/null
    sudo -n insmod "$PROJECT_ROOT/test/signaltest_mod.ko"
    sleep "$DURATION_SEC"
    echo 1 | sudo -n tee /sys/module/signaltest_mod/parameters/shutdown_request >/dev/null
    sleep 2
    bash "$PROJECT_ROOT/scripts/safe_rmmod_fused__安全卸载.sh" signaltest_mod
    sudo -n dmesg | tail -30
} 2>&1 | tee "$LOG"

rc=0
grep -qiE 'error|unresolved|fail' "$LOG" && rc=1
echo "$(date -Iseconds),signaltest,${DURATION_SEC},$((1-rc)),,$(basename "$LOG")" >>"$CSV"
echo "LOG=$LOG"
