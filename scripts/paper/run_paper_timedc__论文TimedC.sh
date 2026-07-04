#!/bin/bash
# Timed C baseline: same rt-tests cyclictest source + same paper isolation as userspace/fused.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(paper_root)"

TIMEDC_DIR="$SCRIPT_DIR/../timedc"
RT_TESTS="${RT_TESTS_DIR:-$PROJECT_ROOT/test/rt-tests}"
CYCLIC_SRC="$RT_TESTS/src/cyclictest/cyclictest.c"
SRC="$PROJECT_ROOT/examples/timedc/cyclictest_paper__论文同源.c"
BIN="$PROJECT_ROOT/third_party/ktc/build/aarch64-apps/cyclictest_paper.out"
PROBE="${JITTER_PROBE_CPU:-3}"

DURATION_MIN="${DURATION_MIN:-15}"
MEASURE_KIND="${MEASURE_KIND:-soak}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$(paper_results_dir timedc)"
LOG="$OUT_DIR/timedc_${MEASURE_KIND}_${STAMP}_${DURATION_MIN}min.log"
JITTER_BIN="${TIMEDC_JITTER_BIN:-$OUT_DIR/timedc_${MEASURE_KIND}_${STAMP}_${DURATION_MIN}min.jitter.bin}"
TIMEDC_EXPORT="/tmp/timedc_export.jitter.bin"
sudo rm -f "$TIMEDC_EXPORT" 2>/dev/null || rm -f "$TIMEDC_EXPORT" 2>/dev/null || true

paper_copy_timedc_jitter_bin() {
    local src="$1" dst="$2"
    if [ ! -f "$src" ]; then
        return 1
    fi
    mkdir -p "$(dirname "$dst")"
    if cp -f "$src" "$dst" 2>/dev/null; then
        return 0
    fi
    sudo cp -f "$src" "$dst"
    sudo chown "$(id -u):$(id -g)" "$dst"
}

cleanup() {
    paper_stress_stop
    if [ -f "$TIMEDC_EXPORT" ] && { [ ! -f "$JITTER_BIN" ] || [ ! -s "$JITTER_BIN" ]; }; then
        paper_copy_timedc_jitter_bin "$TIMEDC_EXPORT" "$JITTER_BIN" || true
    fi
    paper_env_teardown
}
trap cleanup EXIT INT TERM

plc_check_sudo 1
bash "$TIMEDC_DIR/ensure_rt_tests__拉取cyclictest源码.sh"
if [ ! -f "$CYCLIC_SRC" ]; then
    echo "missing cyclictest source: $CYCLIC_SRC" >&2
    exit 1
fi
bash "$TIMEDC_DIR/build_timedc__编译TimedC.sh" "$SRC" "cyclictest_paper"

{
    echo "=== Timed C paper baseline ${MEASURE_KIND} ${DURATION_MIN}min cpu=${PROBE} ==="
    echo "=== upstream_ref=${CYCLIC_SRC} ==="
    echo "=== timedc_src=${SRC} ==="
    paper_env_setup "$MEASURE_KIND"
    if [ "$MEASURE_KIND" = "stress" ]; then
        export STRESS_LOAD_ENABLE=1
        paper_stress_start
    fi
    timeout --signal=TERM "${DURATION_MIN}m" \
        taskset -c "$PROBE" sudo -n chrt -f 99 "$BIN"
    paper_stress_stop
} 2>&1 | tee "$LOG"

if [ -f "$JITTER_BIN" ] && [ "${PAPER_DUAL_PLOT:-0}" = "1" ]; then
    plot_out="${PAPER_DUAL_PLOT_BASE:-$OUT_DIR/timedc_${MEASURE_KIND}}"
    bash "$SCRIPT_DIR/plot_dual_from_artifact__双图出图.sh" \
        "timedc" "$MEASURE_KIND" "$plot_out" "$JITTER_BIN" ""
fi

echo "LOG=$LOG"
echo "JITTER_BIN=$JITTER_BIN"
