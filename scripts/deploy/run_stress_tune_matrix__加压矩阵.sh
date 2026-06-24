#!/bin/bash
# ============================================================================
# run_stress_tune_matrix__加压矩阵.sh — 加压测短/中档位矩阵
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS="$PROJECT_ROOT/results/stress/stability"
SUMMARY="$RESULTS/stress_matrix_summary.csv"
PROFILE="${PLC_PROFILE:-$SCRIPT_DIR/profiles/profile_stress_l2__背景加压.env.sh}"
COOLDOWN="${MATRIX_COOLDOWN_SEC:-180}"

mkdir -p "$RESULTS"

if [ ! -f "$SUMMARY" ]; then
    echo "timestamp,label,duration_min,measure_kind,abs_max_ns,hackbench_loops,target,verdict,raw_log" > "$SUMMARY"
fi

run_tier() {
    local label="$1"
    local mins="$2"
    local target="$3"
    local stamp
    stamp="$(date +%Y%m%d_%H%M%S)"

    echo ""
    echo "################################################################"
    echo "### 加压矩阵: ${label} (${mins} min) target_abs_max<${target} ns"
    echo "################################################################"

    export PLC_PROFILE="$PROFILE"
    export MEASURE_KIND=stress
    export PRJ="$PROJECT_ROOT"
    export PATH="${PATH:-/usr/local/llvm-17/bin:/usr/bin:/bin}"
    export DURATION_MIN="$mins"
    export TARGET_ABS_MAX_NS="$target"
    export PRE_IDLE_SEC="${PRE_IDLE_SEC:-60}"
    export FORCE_REBUILD_KERNEL_O=0

    local log="$RESULTS/stress_matrix_${label}_${stamp}.log"
    if ! bash "$SCRIPT_DIR/run_stress_cycletest__加压长测.sh" 2>&1 | tee "$log"; then
        echo "❌ ${label} 失败 → $log"
        echo "$(date -Iseconds),${label},${mins},stress,FAIL,${STRESS_HACKBENCH_LOOPS:-8},${target},FAIL,$log" >> "$SUMMARY"
        return 1
    fi

    local abs_max verdict="PASS"
    abs_max="$(grep -E 'abs_max_ns=' "$log" | tail -1 | sed -n 's/.*abs_max_ns=\([0-9]*\).*/\1/p')"
    if [ -n "$abs_max" ] && [ "$abs_max" -ge "$target" ] 2>/dev/null; then
        verdict="WARN"
    fi
    echo "$(date -Iseconds),${label},${mins},stress,${abs_max:-?},${STRESS_HACKBENCH_LOOPS:-8},${target},${verdict},$log" >> "$SUMMARY"
    echo "✅ ${label}: abs_max=${abs_max:-?}ns"
    sleep "$COOLDOWN"
}

echo "=== 背景加压矩阵 (hackbench on ${STRESS_LOAD_CPUS:-0-2}) ==="
echo "    profile=$PROFILE"

run_tier medium 15 15000 || true

echo ""
column -t -s, "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
