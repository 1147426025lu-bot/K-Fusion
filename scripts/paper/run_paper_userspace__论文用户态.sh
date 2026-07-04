#!/bin/bash
# ============================================================================
# run_paper_userspace__论文用户态.sh — 与 fused 相同隔离下的用户态 cyclictest
# ============================================================================
# 用法: MEASURE_KIND=stress DURATION_MIN=5 bash run_paper_userspace__论文用户态.sh
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(paper_root)"
RT_TESTS="${RT_TESTS_DIR:-$PROJECT_ROOT/test/rt-tests}"
CYCLIC_BIN="$RT_TESTS/cyclictest"
PROBE="${JITTER_PROBE_CPU:-3}"

DURATION_MIN="${DURATION_MIN:-15}"
MEASURE_KIND="${MEASURE_KIND:-soak}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$(paper_results_dir userspace)"
LOG="$OUT_DIR/userspace_${MEASURE_KIND}_${STAMP}_${DURATION_MIN}min.log"

cleanup() { paper_stress_stop; paper_env_teardown; }
trap cleanup EXIT INT TERM

plc_check_sudo 1
if [ ! -x "$CYCLIC_BIN" ]; then
    (cd "$RT_TESTS" && make -j"$(nproc)" cyclictest)
fi

{
    echo "=== paper userspace cyclictest ${MEASURE_KIND} ${DURATION_MIN}min cpu=${PROBE} ==="
    paper_env_setup "$MEASURE_KIND"
    if [ "$MEASURE_KIND" = "stress" ]; then
        export STRESS_LOAD_ENABLE=1
        paper_stress_start
    fi
    taskset -c "$PROBE" sudo -n "$CYCLIC_BIN" -p 99 -i 1000 -m -q -h 100000 -D "${DURATION_MIN}m"
    paper_stress_stop
} 2>&1 | tee "$LOG"

echo "LOG=$LOG"
