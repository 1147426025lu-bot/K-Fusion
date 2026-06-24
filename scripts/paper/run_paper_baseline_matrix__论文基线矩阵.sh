#!/bin/bash
# ============================================================================
# run_paper_baseline_matrix__论文基线矩阵.sh — 三基线 × soak/stress × N 次
# ============================================================================
# 基线: userspace | baseline_ko (手写) | fused_soak | fused_stress
# 用法:
#   bash scripts/paper/run_paper_baseline_matrix__论文基线矩阵.sh
#   PAPER_RUNS=3 DURATION_MIN=5 SKIP_USERSPACE=1 bash ...
# 环境:
#   PAPER_RUNS=5          每格重复次数（论文建议 ≥5）
#   DURATION_MIN=15       单次时长
#   MATRIX_COOLDOWN_SEC=120
#   SKIP_USERSPACE=1      跳过用户态（省时）
#   SKIP_BASELINE_KO=1    跳过手写基线
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
DEPLOY="$SCRIPT_DIR/../deploy"

PAPER_RUNS="${PAPER_RUNS:-3}"
DURATION_MIN="${DURATION_MIN:-15}"
COOLDOWN="${MATRIX_COOLDOWN_SEC:-120}"
FUSED_SOAK_COOLDOWN_SEC="${FUSED_SOAK_COOLDOWN_SEC:-600}"
RUN_ID="paper_baseline_$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$(paper_results_dir baseline_matrix)"
CSV="$OUT_DIR/${RUN_ID}.csv"
paper_csv_header "$CSV"

run_fused() {
    local kind="$1" idx="$2"
    local log rc=0 profile
    log="$OUT_DIR/fused_${kind}_run${idx}_$(date +%H%M%S).log"
    if [ "$kind" = "stress" ]; then
        profile="$DEPLOY/profiles/profile_stress_l2__背景加压.env.sh"
    else
        profile="$DEPLOY/profiles/profile_soak_l2_best__安静浸泡.env.sh"
    fi
    if [ "$kind" = "stress" ]; then
        if ! PLC_PROFILE="$profile" DURATION_MIN="$DURATION_MIN" \
            bash "$DEPLOY/run_stress_cycletest__加压长测.sh" >"$log" 2>&1; then
            rc=1
        fi
    else
        if ! PLC_PROFILE="$profile" DURATION_MIN="$DURATION_MIN" \
            bash "$DEPLOY/run_soak_cycletest__浸泡长测.sh" >"$log" 2>&1; then
            rc=1
        fi
    fi
    paper_append_csv "$CSV" "$RUN_ID" "fused" "$kind" "${ISOLATION_LEVEL:-}" "$idx" "$DURATION_MIN" "$log" "$rc" ""
    echo "  fused_${kind} run${idx}: rc=$rc log=$log"
}

run_cell() {
    local baseline="$1" kind="$2" idx="$3"
    local log rc=0 iso="${ISOLATION_LEVEL:-}"

    case "$baseline" in
        userspace)
            log="$OUT_DIR/userspace_${kind}_run${idx}_$(date +%H%M%S).log"
            if ! MEASURE_KIND="$kind" DURATION_MIN="$DURATION_MIN" \
                bash "$SCRIPT_DIR/run_paper_userspace__论文用户态.sh" >"$log" 2>&1; then
                rc=1
            fi
            iso=$([ "$kind" = "stress" ] && echo 1 || echo 2)
            ;;
        baseline_ko)
            log="$OUT_DIR/baseline_ko_${kind}_run${idx}_$(date +%H%M%S).log"
            if ! MEASURE_KIND="$kind" DURATION_MIN="$DURATION_MIN" \
                bash "$SCRIPT_DIR/run_paper_baseline_ko__论文手写基线.sh" >"$log" 2>&1; then
                rc=1
            fi
            iso=$([ "$kind" = "stress" ] && echo 1 || echo 2)
            ;;
        fused)
            run_fused "$kind" "$idx"
            return
            ;;
        *)
            echo "unknown baseline $baseline"; return 1
            ;;
    esac
    paper_append_csv "$CSV" "$RUN_ID" "$baseline" "$kind" "$iso" "$idx" "$DURATION_MIN" "$log" "$rc" ""
    echo "  ${baseline}_${kind} run${idx}: rc=$rc"
}

echo "=== 论文基线矩阵 RUN_ID=$RUN_ID ==="
echo "    runs=$PAPER_RUNS duration_min=$DURATION_MIN csv=$CSV"

for idx in $(seq 1 "$PAPER_RUNS"); do
    echo ""
    echo "--- 重复 $idx/$PAPER_RUNS ---"
    if [ "${SKIP_USERSPACE:-0}" != "1" ]; then
        run_cell userspace soak "$idx" || true
        sleep "$COOLDOWN"
        run_cell userspace stress "$idx" || true
        sleep "$COOLDOWN"
    fi
    if [ "${SKIP_BASELINE_KO:-0}" != "1" ]; then
        run_cell baseline_ko soak "$idx" || true
        sleep "$COOLDOWN"
        run_cell baseline_ko stress "$idx" || true
        sleep "$COOLDOWN"
    fi
    if [ "$FUSED_SOAK_COOLDOWN_SEC" -gt 0 ] 2>/dev/null; then
        echo "=== fused soak 前额外冷却 ${FUSED_SOAK_COOLDOWN_SEC}s（stress 余热）==="
        sleep "$FUSED_SOAK_COOLDOWN_SEC"
    fi
    run_cell fused soak "$idx" || true
    sleep "$COOLDOWN"
    run_cell fused stress "$idx" || true
    sleep "$COOLDOWN"
done

echo ""
echo "=== 矩阵完成 → $CSV ==="
python3 "$SCRIPT_DIR/paper_summarize_results__论文结果汇总.py" --csv "$CSV" --out "$OUT_DIR/${RUN_ID}_summary.md" || true
