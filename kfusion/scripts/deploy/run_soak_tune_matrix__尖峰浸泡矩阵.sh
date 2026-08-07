#!/bin/bash
# ============================================================================
# run_soak_tune_matrix__尖峰浸泡矩阵.sh — 浸泡测短/中/长档位矩阵
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/repo_paths__仓库路径.sh"
PROJECT_ROOT="$PLCFUSION_ROOT"
RESULTS="$RESULTS_ROOT/soak/stability"
SUMMARY="$RESULTS/soak_matrix_summary.csv"
PROFILE="${PLC_PROFILE:-$SCRIPT_DIR/profiles/profile_soak_l2_best__安静浸泡.env.sh}"
COOLDOWN="${MATRIX_COOLDOWN_SEC:-120}"

mkdir -p "$RESULTS"

if [ ! -f "$SUMMARY" ]; then
    echo "timestamp,label,duration_min,measure_kind,abs_max_ns,spikes_skip,resync,verdict,png,raw_log" > "$SUMMARY"
fi

run_tier() {
    local label="$1"
    local mins="$2"
    local target="$3"
    local stamp
    stamp="$(date +%Y%m%d_%H%M%S)"

    echo ""
    echo "################################################################"
    echo "### 浸泡矩阵: ${label} (${mins} min) target_abs_max<${target} ns"
    echo "################################################################"

    export PLC_PROFILE="$PROFILE"
    export MEASURE_KIND=soak
    export PRJ="$PROJECT_ROOT"
    export PATH="${PATH:-/usr/local/llvm-17/bin:/usr/bin:/bin}"
    export DURATION_MIN="$mins"
    export TARGET_ABS_MAX_NS="$target"
    export EXPORT_DECIM_MAX=0
    export RING_EXPORT_PATH="$RESULTS_ROOT/jitter_soak_${label}_${stamp}.bin"
    export MAX_UPTIME_SEC=0
    export FORCE_REBUILD_KERNEL_O=0
    export PRE_IDLE_SEC="${PRE_IDLE_SEC:-90}"

    local log="$RESULTS/soak_matrix_${label}_${stamp}.log"
    if ! bash "$SCRIPT_DIR/run_soak_cycletest__浸泡长测.sh" 2>&1 | tee "$log"; then
        echo "❌ ${label} 失败 → $log"
        echo "$(date -Iseconds),${label},${mins},soak,FAIL,,,FAIL,,$log" >> "$SUMMARY"
        return 1
    fi

    local abs_max spikes png verdict="PASS"
    abs_max="$(grep -E 'abs_max_ns=' "$log" | tail -1 | sed -n 's/.*abs_max_ns=\([0-9]*\).*/\1/p')"
    spikes="$(grep -oE 'spike_resync=[0-9]+' "$log" | tail -1 | sed 's/spike_resync=//' || true)"
    png="$(grep -E '分布图:' "$log" | tail -1 | awk '{print $NF}' || true)"
    if [ -n "$abs_max" ] && [ "$abs_max" -ge "$target" ] 2>/dev/null; then
        verdict="WARN"
    fi
    echo "$(date -Iseconds),${label},${mins},soak,${abs_max:-?},${spikes:-0},${spikes:-0},${verdict},${png},$log" >> "$SUMMARY"
    echo "✅ ${label}: abs_max=${abs_max:-?}ns"
    sleep "$COOLDOWN"
}

echo "=== 安静浸泡矩阵 ==="
echo "    profile=$PROFILE"
echo "    summary=$SUMMARY"

run_tier short 15 5000 || true
run_tier medium 60 5000 || true
if [ "${SKIP_8H:-0}" != "1" ]; then
    run_tier long 480 5000 || true
fi

echo ""
column -t -s, "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
