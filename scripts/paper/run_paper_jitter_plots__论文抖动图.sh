#!/bin/bash
# ============================================================================
# run_paper_jitter_plots__论文抖动图.sh — 跑 15min 浸泡/加压并生成抖动分布 + 延迟时序图
# ============================================================================
# 产出:
#   results/soak/png/*_soak.png + *_latency.png
#   results/stress/png/*_stress.png + *_latency.png
#   results/paper/plots/          （复制一份便于写论文）
#   docs/paper/figures/           （最新 soak/stress 图各一对）
#
# 用法:
#   sudo -v
#   bash scripts/paper/run_paper_jitter_plots__论文抖动图.sh
#   DURATION_MIN=15 PLOT_SOAK_ONLY=1 bash ...    # 只跑浸泡
#   DURATION_MIN=3  bash ...                     # 快速验证出图
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"
PRJ="$(plc_project_root)"
DEPLOY="$SCRIPT_DIR/../deploy"
export PATH="${PATH:-/usr/local/llvm-17/bin:/usr/lib/llvm-19/bin:$PATH}"

DURATION_MIN="${DURATION_MIN:-15}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$PRJ/results/paper/jitter_plots_${STAMP}.log"
PAPER_PLOTS="$PRJ/results/paper/plots"
FIG_DIR="$PRJ/docs/paper/figures"
mkdir -p "$PAPER_PLOTS" "$(dirname "$LOG")"

exec > >(tee -a "$LOG") 2>&1

plc_check_sudo 1
if lsmod | grep -q '_mod'; then
    plc_warn "检测到已加载模块，请先 rmmod 所有 *_mod"
fi

echo "=== 论文抖动/延迟出图 DURATION_MIN=${DURATION_MIN} ==="

echo ""
echo "########## [0] 确保 cyclictest .ko 已融合（FUSE_MAIN_ARGS 已修正）##########"
bash "$PRJ/scripts/plc_fuse__内核化主流程.sh" \
    "$PRJ/manifests/manifest_cyclictest__主线压测.env"
(
    cd "$DEPLOY"
    cp -f "$PRJ/src/plc_runtime_stubs__POSIX桩.c" "$PRJ/test/plc_runtime_stubs.c"
    cp -f "$PRJ/src/plc_runner_official__cyclictest宿主.c" "$PRJ/test/plc_runner_official.c"
    export FORCE_REBUILD_KERNEL_O=1 IGNITE_BUILD_ONLY=1 SYNC_RUNNER_SOURCE=1 SOAK_SKIP_KBUILD=0
    bash ignite_official_cycletest__cyclictest主线.sh
)

copy_latest_pair() {
    local kind="$1" label="$2"
    local png_dir="$PRJ/results/${kind}/png"
    mkdir -p "$png_dir"
    local dist lat
    dist="$(ls -t "$png_dir"/*_${DURATION_MIN}min_${kind}.png 2>/dev/null | head -1 || true)"
    lat="$(ls -t "$png_dir"/*_${DURATION_MIN}min_${kind}_latency.png 2>/dev/null | head -1 || true)"
    if [ -z "$dist" ]; then
        dist="$(ls -t "$png_dir"/*_${kind}.png 2>/dev/null | grep -v latency | head -1 || true)"
        lat="$(ls -t "$png_dir"/*_${kind}_latency.png 2>/dev/null | head -1 || true)"
    fi
    if [ -n "$dist" ] && [ -f "$dist" ]; then
        cp -f "$dist" "$PAPER_PLOTS/${label}_jitter_${DURATION_MIN}min.png"
        cp -f "$dist" "$FIG_DIR/fig_${label}_jitter__${label}抖动分布.png"
        echo "✅ 抖动分布图 [$kind]: $dist"
    else
        echo "❌ 未找到 ${kind} 抖动分布 PNG"
        return 1
    fi
    if [ -n "$lat" ] && [ -f "$lat" ]; then
        cp -f "$lat" "$PAPER_PLOTS/${label}_latency_${DURATION_MIN}min.png"
        cp -f "$lat" "$FIG_DIR/fig_${label}_latency__${label}延迟时序.png"
        echo "✅ 延迟时序图 [$kind]: $lat"
    else
        echo "⚠️  未找到 ${kind} 延迟时序 PNG"
    fi
    return 0
}

run_soak=1
run_stress=1
[ "${PLOT_SOAK_ONLY:-0}" = "1" ] && run_stress=0
[ "${PLOT_STRESS_ONLY:-0}" = "1" ] && run_soak=0

if [ "$run_soak" = "1" ]; then
    echo ""
    echo "########## [1] 浸泡 ${DURATION_MIN}min（ringbuf 开，出图）##########"
    PLC_PROFILE="$DEPLOY/profiles/profile_paper_plot__论文抖动出图.env.sh" \
        DURATION_MIN="$DURATION_MIN" PAPER_JITTER_PLOTS=1 \
        bash "$DEPLOY/run_soak_cycletest__浸泡长测.sh"
    copy_latest_pair soak soak || true
fi

if [ "$run_stress" = "1" ]; then
    echo ""
    echo "########## [2] 加压 ${DURATION_MIN}min（ringbuf 开，出图）##########"
    PLC_PROFILE="$DEPLOY/profiles/profile_paper_plot_stress__论文加压出图.env.sh" \
        DURATION_MIN="$DURATION_MIN" PAPER_JITTER_PLOTS=1 \
        bash "$DEPLOY/run_stress_cycletest__加压长测.sh"
    copy_latest_pair stress stress || true
fi

echo ""
echo "=== 完成 ==="
echo "   论文用副本: $PAPER_PLOTS/"
echo "   插图目录:   $FIG_DIR/fig_*_jitter*.png fig_*_latency*.png"
echo "   原始 PNG:   results/soak/png/ results/stress/png/"
echo "   日志:       $LOG"
ls -la "$PAPER_PLOTS/" 2>/dev/null || true
