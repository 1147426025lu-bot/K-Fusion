#!/bin/bash
# ============================================================================
# run_paper_ablation_matrix__论文消融矩阵.sh — runner/隔离开关消融
# ============================================================================
# 用法: DURATION_MIN=5 bash scripts/paper/run_paper_ablation_matrix__论文消融矩阵.sh
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
DEPLOY="$SCRIPT_DIR/../deploy"

DURATION_MIN="${DURATION_MIN:-15}"
RUN_ID="paper_ablation_$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$(paper_results_dir ablation)"
CSV="$OUT_DIR/${RUN_ID}.csv"
paper_csv_header "$CSV"

run_ablation() {
    local label="$1"
    shift
    local log rc=0
    log="$OUT_DIR/ablation_${label}_$(date +%H%M%S).log"
    echo "=== ablation: $label ==="
    export PLC_PROFILE="$DEPLOY/profiles/profile_soak_l2_best__安静浸泡.env.sh"
    # shellcheck source=/dev/null
    source "$PLC_PROFILE"
    export RUNNER_PROFILE="fused_ablation_${label}"
    while [ $# -gt 0 ]; do
        export "$1"
        shift
    done
    if ! DURATION_MIN="$DURATION_MIN" bash "$DEPLOY/run_soak_cycletest__浸泡长测.sh" >"$log" 2>&1; then
        rc=1
    fi
    paper_append_csv "$CSV" "$RUN_ID" "fused_ablation" "soak" "${ISOLATION_LEVEL:-}" "1" \
        "$DURATION_MIN" "$log" "$rc" "$label"
}

echo "=== 论文消融矩阵 RUN_ID=$RUN_ID duration=${DURATION_MIN}min ==="

# 全关热路径负担（L2 默认）
run_ablation "l2_default" \
    FUSED_HIST_ENABLE=0 FUSED_WAKE_TIMERTHREAD=0 FUSED_RINGBUF_ENABLE=0 PROBE_RT_ENABLE=0 \
    || true
sleep 60

# 逐项打开
run_ablation "hist_on" FUSED_HIST_ENABLE=1 || true
sleep 60
run_ablation "wake_on" FUSED_WAKE_TIMERTHREAD=1 || true
sleep 60
run_ablation "ring_on" FUSED_RINGBUF_ENABLE=1 EXPORT_DECIM_MAX=72000 || true
sleep 60
run_ablation "probe_rt_on" PROBE_RT_ENABLE=1 || true
sleep 60

# 隔离级别
run_ablation "iso_l1" ISOLATION_LEVEL=1 || true
sleep 60
run_ablation "iso_l2" ISOLATION_LEVEL=2 || true
sleep 60

# resync 关（诚实）
run_ablation "no_resync" JITTER_RESYNC_THRESH_NS=0 || true

echo ""
python3 "$SCRIPT_DIR/paper_summarize_results__论文结果汇总.py" --csv "$CSV" --out "$OUT_DIR/${RUN_ID}_summary.md" || true
echo "=== 消融完成 → $CSV ==="
