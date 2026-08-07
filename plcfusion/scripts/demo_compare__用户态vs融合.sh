#!/bin/bash
# ============================================================================
# demo_compare__用户态vs融合.sh — 用户态 cyclictest vs PLCFusion 短测对比
# ============================================================================
# 功能: 编译上游 cyclictest（用户态）→ 短跑采样；再跑 fused 主线短测；对比摘要
# 输入: 可选环境变量（见下）
# 输出: results/raw/compare_*.log、终端对比表
# 用法:
#   bash scripts/demo_compare__用户态vs融合.sh
#   DURATION_SEC=120 bash scripts/demo_compare__用户态vs融合.sh
# 环境:
#   DURATION_SEC=300     每侧采样时长（默认 300s）
#   SKIP_USERSPACE=1     仅跑 fused（无 rt-tests 编译环境时）
#   SKIP_FUSED=1         仅跑用户态
#   RT_TESTS_DIR=        默认 test/rt-tests
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
DEPLOY="$PROJECT_ROOT/scripts/deploy"
MANIFEST="$PROJECT_ROOT/manifests/manifest_cyclictest__主线压测.env"
RT_TESTS="${RT_TESTS_DIR:-$PROJECT_ROOT/test/rt-tests}"
DURATION_SEC="${DURATION_SEC:-300}"
RESULTS_RAW="$RESULTS_ROOT/raw"
STAMP="$(date +%Y%m%d_%H%M%S)"
COMPARE_LOG="$RESULTS_RAW/compare_${STAMP}_${DURATION_SEC}s.log"

mkdir -p "$RESULTS_RAW"

parse_cyclictest_log() {
    local log="$1"
    local max min avg samples
    max=$(grep -E 'Max:|Max Latency|max:' "$log" 2>/dev/null | tail -1 | sed -n 's/.*Max:[[:space:]]*\([0-9]*\).*/\1/p')
    [ -z "$max" ] && max=$(grep -E 'Max Latency|max:' "$log" 2>/dev/null | tail -1 | grep -oE '[0-9]+' | tail -1 || true)
    min=$(grep -E 'Min:|Min Latency|min:' "$log" 2>/dev/null | tail -1 | sed -n 's/.*Min:[[:space:]]*\([0-9]*\).*/\1/p')
    avg=$(grep -E 'Avg:|Avg Latency|avg:' "$log" 2>/dev/null | tail -1 | sed -n 's/.*Avg:[[:space:]]*\([0-9.]*\).*/\1/p')
    samples=$(grep -cE '^T:|cycle:' "$log" 2>/dev/null || echo 0)
    echo "${max:-na}|${min:-na}|${avg:-na}|${samples}"
}

parse_fused_summary() {
    local log="$1"
    local stats="${2:-}"
    local line abs_max outliers min_ns cycles

    if [ -n "$stats" ] && [ -f "$stats" ]; then
        abs_max=$(sed -n 's/.*abs_max_ns=\([0-9-]*\).*/\1/p' "$stats" | tail -1)
        min_ns=$(sed -n 's/.*min_ns=\([0-9-]*\).*/\1/p' "$stats" | tail -1)
        cycles=$(sed -n 's/.*cycles=\([0-9]*\).*/\1/p' "$stats" | tail -1)
        [ -n "$abs_max" ] && echo "${abs_max}|0|stats cycles=${cycles} min_ns=${min_ns} abs_max_ns=${abs_max}" && return
    fi

    line=$(grep -E 'JitterSummary:|FusedSummary:' "$log" 2>/dev/null | tail -1 || true)
    abs_max=$(echo "$line" | sed -n 's/.*abs_max_ns=\([0-9-]*\).*/\1/p')
    [ -z "$abs_max" ] && abs_max=$(echo "$line" | sed -n 's/.*max_ns=\([0-9-]*\).*/\1/p')
    outliers=$(echo "$line" | sed -n 's/.*outliers=\([0-9]*\).*/\1/p')
    if [ -n "$abs_max" ]; then
        echo "${abs_max}|${outliers:-0}|$line"
        return
    fi

    abs_max=$(grep -oE 'Jitter: [0-9]+' "$log" 2>/dev/null | awk '{print $2}' | sort -n | tail -1)
    [ -n "$abs_max" ] && echo "${abs_max}|0|fallback Jitter max=${abs_max}" && return
    echo "na|na|"
}

USERSPACE_LOG="$RESULTS_RAW/userspace_${STAMP}_${DURATION_SEC}s.log"
FUSED_RAW="$RESULTS_RAW/fused_${STAMP}_${DURATION_SEC}s.raw.log"
FUSED_STATS="$RESULTS_RAW/fused_${STAMP}_${DURATION_SEC}s.stats"
US_STAT="na|na|na|0"
FUSED_STAT="na|na|"

echo "=== PLCFusion 对比 demo（${DURATION_SEC}s）==="
echo "    stamp=$STAMP"

if [ "${SKIP_USERSPACE:-0}" != "1" ]; then
    if [ ! -d "$RT_TESTS" ]; then
        plc_warn "rt-tests 目录不存在: $RT_TESTS" "设 SKIP_USERSPACE=1 或先融合拉取 git"
    else
        echo "🧑 [1/2] 用户态 cyclictest（${DURATION_SEC}s）..."
        CYCLIC_BIN="$RT_TESTS/cyclictest"
        if [ ! -x "$CYCLIC_BIN" ]; then
            echo "    编译 rt-tests cyclictest..."
            if ! (cd "$RT_TESTS" && make -j"$(nproc)" cyclictest >/dev/null 2>&1); then
                plc_warn "rt-tests make cyclictest 失败" "检查 test/rt-tests 或设 SKIP_USERSPACE=1"
            else
                CYCLIC_BIN="$RT_TESTS/cyclictest"
            fi
        fi
        if [ -x "$CYCLIC_BIN" ]; then
            # 与 fused 主线相近：单线程、优先级 80、间隔 200µs、时长 DURATION_SEC
            if sudo -n "$CYCLIC_BIN" -t1 -p80 -i200 -D "$DURATION_SEC" -q > "$USERSPACE_LOG" 2>&1; then
                US_STAT="$(parse_cyclictest_log "$USERSPACE_LOG")"
                echo "    userspace log=$USERSPACE_LOG"
            else
                plc_warn "用户态 cyclictest 运行失败" "见 $USERSPACE_LOG"
            fi
        fi
    fi
else
    echo "⏭️  SKIP_USERSPACE=1"
fi

if [ "${SKIP_FUSED:-0}" != "1" ]; then
    echo "🔥 [2/2] fused cyclictest 主线（${DURATION_SEC}s）..."
    plc_require_file "$MANIFEST" "cyclictest manifest"
    cd "$DEPLOY"
    if lsmod | grep -q '^official_cycletest_mod'; then
        bash ./safe_rmmod_official__cyclictest卸载.sh || true
        sleep 2
    fi
    export FORCE_REBUILD_KERNEL_O=0
    export FOLLOW_DMESG=0
    export PLC_FUSE_MANIFEST="$MANIFEST"
    bash ./ignite_official_cycletest__cyclictest主线.sh
    if ! lsmod | grep -q '^official_cycletest_mod'; then
        plc_die "$PLC_E_KMOD" "fused 模块未加载"
    fi
    sudo -n dmesg -c >/dev/null 2>&1 || true
    sleep "$DURATION_SEC"
    sudo -n cat /sys/kernel/debug/fused_stats > "$FUSED_STATS" 2>/dev/null || true
    bash ./safe_rmmod_official__cyclictest卸载.sh || true
    sudo -n dmesg > "$FUSED_RAW" 2>/dev/null || true
    FUSED_STAT="$(parse_fused_summary "$FUSED_RAW" "$FUSED_STATS")"
    echo "    fused stats=$FUSED_STATS"
    echo "    fused raw=$FUSED_RAW"
else
    echo "⏭️  SKIP_FUSED=1"
fi

IFS='|' read -r us_max us_min us_avg us_samples <<< "$US_STAT"
IFS='|' read -r fused_abs fused_outliers fused_line <<< "$FUSED_STAT"

{
    echo "# compare $STAMP duration=${DURATION_SEC}s"
    echo "userspace_max_us=$us_max userspace_min_us=$us_min userspace_avg_us=$us_avg samples=$us_samples"
    echo "fused_abs_max_ns=$fused_abs fused_outliers=$fused_outliers"
    echo "fused_summary=$fused_line"
    echo "fused_stats=$FUSED_STATS"
    echo "userspace_log=$USERSPACE_LOG"
    echo "fused_raw=$FUSED_RAW"
} > "$COMPARE_LOG"

echo
echo "=== 对比摘要 ==="
printf "%-12s %12s %12s %12s %10s\n" "侧" "max" "min/alt" "avg/out" "samples"
printf "%-12s %12s %12s %12s %10s\n" "userspace" "${us_max}" "${us_min}" "${us_avg}" "${us_samples}"
printf "%-12s %12s %12s %12s %10s\n" "fused" "${fused_abs}ns" "—" "out=${fused_outliers}" "—"
echo
echo "    compare_log=$COMPARE_LOG"
echo "    融合报告: test/official_cycletest.fusion_report"
echo "    长测绘图: cd scripts/deploy && bash run_soak_cycletest__浸泡长测.sh"
