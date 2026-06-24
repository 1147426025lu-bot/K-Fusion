#!/bin/bash
# ============================================================================
# run_paper_baseline_ko__论文手写基线.sh — 公平环境下跑手写 hrtimer 基线
# ============================================================================
# 用法: MEASURE_KIND=soak DURATION_MIN=5 bash run_paper_baseline_ko__论文手写基线.sh
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
DEPLOY="$SCRIPT_DIR/../deploy"
PROJECT_ROOT="$(paper_root)"

DURATION_MIN="${DURATION_MIN:-15}"
MEASURE_KIND="${MEASURE_KIND:-soak}"
DURATION_SEC=$((DURATION_MIN * 60))
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$(paper_results_dir baseline_ko)"
LOG="$OUT_DIR/baseline_ko_${MEASURE_KIND}_${STAMP}_${DURATION_MIN}min.log"

cleanup() { paper_stress_stop; paper_env_teardown; }
trap cleanup EXIT INT TERM

{
    echo "=== paper baseline_ko ${MEASURE_KIND} ${DURATION_MIN}min ==="
    paper_env_setup "$MEASURE_KIND"
    if [ "$MEASURE_KIND" = "stress" ]; then
        export STRESS_LOAD_ENABLE=1
        paper_stress_start
    fi
    bash "$SCRIPT_DIR/ignite_baseline_cyclic__手写基线.sh" load
    sudo -n dmesg -c >/dev/null
    echo "sleep ${DURATION_SEC}s..."
    sleep "$DURATION_SEC"
    sudo -n cat /sys/kernel/debug/baseline_stats 2>/dev/null || true
    paper_stress_stop
    sudo -n rmmod baseline_cyclic_mod
    sudo -n dmesg
} 2>&1 | tee "$LOG"

echo "LOG=$LOG"
