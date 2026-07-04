#!/bin/bash
# ============================================================================
# run_paper_compare_dual__论文对比双图.sh
# 三基线（userspace / timedc / fused）× soak/stress → 各自双图 + 叠加对比图
#
# 用法:
#   sudo -v
#   bash scripts/paper/run_paper_compare_dual__论文对比双图.sh
#   DURATION_MIN=3 bash ...                    # 快速验证（约 18min 跑满 6 格）
#   PLOT_ONLY=1 COMPARE_DIR=results/paper/compare/compare_xxx bash ...  # 仅重出图
#   SKIP_FUSED=1 bash ...                      # 跳过 fused（需已 fuse 或仅对比 userspace+timedc）
#
# 产出:
#   results/paper/compare/compare_<stamp>/
#     soak|stress/{userspace,timedc,fused}_*.png + *.jitter.bin + *.log
#     fig_compare_*__对比叠加.png → docs/paper/figures/
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"
PRJ="$(plc_project_root)"
DEPLOY="$SCRIPT_DIR/../deploy"
export PATH="${PATH:-/usr/local/llvm-17/bin:/usr/lib/llvm-19/bin:$PATH}"

DURATION_MIN="${DURATION_MIN:-15}"
STAMP="${COMPARE_STAMP:-$(date +%Y%m%d_%H%M%S)}"
COMPARE_DIR="${COMPARE_DIR:-$PRJ/results/paper/compare/compare_${STAMP}}"
FIG_DIR="$PRJ/docs/paper/figures"
MANIFEST="$COMPARE_DIR/manifest.csv"
LOG="$COMPARE_DIR/run.log"
mkdir -p "$COMPARE_DIR" "$(dirname "$LOG")"

exec > >(tee -a "$LOG") 2>&1

append_manifest() {
    local kind="$1" baseline="$2" log_path="$3" bin_path="$4" jitter_png="$5" latency_png="$6"
    echo "${kind},${baseline},${log_path},${bin_path},${jitter_png},${latency_png}" >>"$MANIFEST"
}

cell_done() {
    local kind="$1" baseline="$2"
    local kind_dir="$COMPARE_DIR/$kind"
    local png="$kind_dir/${baseline}_${kind}.png"
    [ -f "$png" ] && [ -s "$png" ]
}

init_manifest() {
    if [ "${RESUME:-0}" = "1" ] && [ -f "$MANIFEST" ]; then
        return 0
    fi
    echo "kind,baseline,log,jitter_bin,jitter_png,latency_png" >"$MANIFEST"
}

plot_cell() {
    local baseline="$1" kind="$2" out_base="$3" bin_path="${4:-}" log_path="${5:-}"
    bash "$SCRIPT_DIR/plot_dual_from_artifact__双图出图.sh" \
        "$baseline" "$kind" "$out_base" "$bin_path" "$log_path" || true
    append_manifest "$kind" "$baseline" "$log_path" "$bin_path" \
        "${out_base}_${kind}.png" "${out_base}_${kind}_latency.png"
}

run_userspace_cell() {
    local kind="$1"
    local kind_dir="$COMPARE_DIR/$kind"
    local out_base="$kind_dir/userspace"
    local log_path="$kind_dir/userspace_${DURATION_MIN}min.log"
    mkdir -p "$kind_dir"

    if [ "${RESUME:-0}" = "1" ] && cell_done "$kind" userspace; then
        echo "⏭️  skip userspace/$kind (已有图)"
        return 0
    fi

    if [ "${PLOT_ONLY:-0}" = "1" ]; then
        plot_cell userspace "$kind" "$out_base" "" "$log_path"
        return 0
    fi

    MEASURE_KIND="$kind" DURATION_MIN="$DURATION_MIN" \
        bash "$SCRIPT_DIR/run_paper_userspace__论文用户态.sh" 2>&1 | tee "$log_path"
    plot_cell userspace "$kind" "$out_base" "" "$log_path"
}

run_timedc_cell() {
    local kind="$1"
    local kind_dir="$COMPARE_DIR/$kind"
    local out_base="$kind_dir/timedc"
    local bin_path="$kind_dir/timedc.jitter.bin"
    local log_path="$kind_dir/timedc_${DURATION_MIN}min.log"
    mkdir -p "$kind_dir"

    if [ "${RESUME:-0}" = "1" ] && cell_done "$kind" timedc; then
        echo "⏭️  skip timedc/$kind (已有图)"
        return 0
    fi
    if [ "${RESUME:-0}" = "1" ] && [ -f "$log_path" ] && grep -q 'TimedCSummary:' "$log_path" \
        && [ -f "$bin_path" ] && [ -s "$bin_path" ]; then
        echo "♻️  replot timedc/$kind from existing bin"
        plot_cell timedc "$kind" "$out_base" "$bin_path" "$log_path"
        return 0
    fi

    if [ "${PLOT_ONLY:-0}" = "1" ]; then
        plot_cell timedc "$kind" "$out_base" "$bin_path" "$log_path"
        return 0
    fi

    export TIMEDC_JITTER_BIN="$bin_path"
    export PAPER_DUAL_PLOT=0
    MEASURE_KIND="$kind" DURATION_MIN="$DURATION_MIN" \
        bash "$SCRIPT_DIR/run_paper_timedc__论文TimedC.sh" 2>&1 | tee "$log_path"
    if [ ! -f "$bin_path" ] && [ -f /tmp/timedc_export.jitter.bin ]; then
        sudo cp -f /tmp/timedc_export.jitter.bin "$bin_path" 2>/dev/null || true
        sudo chown "$(id -u):$(id -g)" "$bin_path" 2>/dev/null || true
    fi
    plot_cell timedc "$kind" "$out_base" "$bin_path" "$log_path"
}

run_fused_cell() {
    local kind="$1"
    local kind_dir="$COMPARE_DIR/$kind"
    local out_base="$kind_dir/fused"
    local bin_path="$kind_dir/fused.jitter.bin"
    local log_path="$kind_dir/fused_${DURATION_MIN}min.log"
    local profile soak_script

    mkdir -p "$kind_dir"
    if [ "${RESUME:-0}" = "1" ] && cell_done "$kind" fused; then
        echo "⏭️  skip fused/$kind (已有图)"
        return 0
    fi

    if [ "$kind" = "stress" ]; then
        profile="$DEPLOY/profiles/profile_paper_plot_stress__论文加压出图.env.sh"
        soak_script="$DEPLOY/run_stress_cycletest__加压长测.sh"
    else
        profile="$DEPLOY/profiles/profile_paper_plot__论文抖动出图.env.sh"
        soak_script="$DEPLOY/run_soak_cycletest__浸泡长测.sh"
    fi

    if [ "${PLOT_ONLY:-0}" = "1" ]; then
        plot_cell fused "$kind" "$out_base" "$bin_path" "$log_path"
        return 0
    fi

    export RING_EXPORT_PATH="$bin_path"
    export PAPER_JITTER_PLOTS=0
    export FORCE_REBUILD_KERNEL_O=0
    export SOAK_SKIP_KBUILD=1
    PLC_PROFILE="$profile" DURATION_MIN="$DURATION_MIN" RING_EXPORT_PATH="$bin_path" \
        bash "$soak_script" 2>&1 | tee "$log_path"
    if [ ! -f "$bin_path" ] || [ ! -s "$bin_path" ]; then
        echo "⚠️  fused/$kind: jitter.bin 未导出 path=$bin_path"
    fi
    plot_cell fused "$kind" "$out_base" "$bin_path" "$log_path"
}

ensure_fused_ko() {
    if [ "${SKIP_FUSED:-0}" = "1" ]; then
        return 0
    fi
    if [ "${PLOT_ONLY:-0}" = "1" ]; then
        return 0
    fi
    if [ "${SKIP_PREP:-0}" = "1" ] || [ "${RESUME:-0}" = "1" ]; then
        echo "=== [prep] 跳过 fused .ko 重建（RESUME/SKIP_PREP）==="
        return 0
    fi
    plc_check_sudo 1
    if lsmod | grep -q '_mod'; then
        plc_warn "检测到已加载模块，请先 rmmod 所有 *_mod"
    fi
    echo "=== [prep] cyclictest fused .ko（对比用）==="
    bash "$PRJ/scripts/plc_fuse__内核化主流程.sh" \
        "$PRJ/manifests/manifest_cyclictest__主线压测.env"
    (
        cd "$DEPLOY"
        cp -f "$PRJ/src/plc_runtime_stubs__POSIX桩.c" "$PRJ/test/plc_runtime_stubs.c"
        cp -f "$PRJ/src/plc_runner_official__cyclictest宿主.c" "$PRJ/test/plc_runner_official.c"
        export FORCE_REBUILD_KERNEL_O=1 IGNITE_BUILD_ONLY=1 SYNC_RUNNER_SOURCE=1 SOAK_SKIP_KBUILD=0
        bash ignite_official_cycletest__cyclictest主线.sh
    )
}

plc_check_sudo 1
init_manifest

echo "=== 论文三基线对比双图 DURATION_MIN=${DURATION_MIN} OUT=${COMPARE_DIR} ==="

ensure_fused_ko

for kind in soak stress; do
    echo ""
    echo "########## [$kind] ##########"
    if [ "${SKIP_USERSPACE:-0}" != "1" ]; then
        run_userspace_cell "$kind"
    fi
    if [ "${SKIP_TIMEDC:-0}" != "1" ]; then
        run_timedc_cell "$kind"
    fi
    if [ "${SKIP_FUSED:-0}" != "1" ]; then
        run_fused_cell "$kind"
    fi
done

echo ""
echo "=== 生成叠加对比图 ==="
python3 "$SCRIPT_DIR/plot_paper_compare__论文对比出图.py" \
    --compare-dir "$COMPARE_DIR" \
    --out-dir "$FIG_DIR" \
    --duration-min "$DURATION_MIN"

echo ""
echo "=== 完成 ==="
echo "   对比目录: $COMPARE_DIR"
echo "   manifest: $MANIFEST"
echo "   叠加图:   $FIG_DIR/fig_compare_*"
echo "   日志:     $LOG"
ls -la "$COMPARE_DIR"/*/*.png 2>/dev/null | head -30 || true
