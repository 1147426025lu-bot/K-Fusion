#!/bin/bash
# ============================================================================
# run_ci_wcet_nightly__WCET夜间门禁.sh — wcet-benchmark manifest 静态 WCET 实验
# ============================================================================
# 用法: bash scripts/run_ci_wcet_nightly__WCET夜间门禁.sh
# 环境:
#   WCET_NIGHTLY_INSMOD=1     有 Pi + sudo 时对 cyclictest 主线跑短 insmod autotune
#   WCET_NIGHTLY_SKIP_BUILD=1 跳过 Pass 编译
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
export FUSE_AST_APPLY_SUGGEST=0

WCET_MANIFESTS=(
    "$PRJ/manifests/manifest_cyclictest__主线压测.env"
    "$PRJ/manifests/manifest_cyclictest__多TU压测.env"
    "$PRJ/manifests/manifest_signaltest__信号测试.env"
    "$PRJ/manifests/manifest_ptsematest__互斥锁测试.env"
)

echo "=== WCET nightly (${#WCET_MANIFESTS[@]} wcet-benchmark manifests) ==="

if [ "${WCET_NIGHTLY_SKIP_BUILD:-0}" != "1" ]; then
    (cd "$PRJ/build" && cmake .. >/dev/null && make PLCFusionPass PLCLowJitterPass -j"$(nproc)" >/dev/null)
fi

for m in "${WCET_MANIFESTS[@]}"; do
    echo "    fuse+sweep $(basename "$m")"
    WCET_SWEEP_RUN_FUSE=1 bash "$SCRIPT_DIR/plc_fusion_wcet_sweep__tail对照.sh" "$m"
done

CYCLIC="$PRJ/manifests/manifest_cyclictest__主线压测.env"
if [ "${WCET_NIGHTLY_INSMOD:-0}" = "1" ] && sudo -n true 2>/dev/null; then
    echo "    autotune cyclictest（短 insmod）..."
    bash "$SCRIPT_DIR/plc_fusion_wcet_autotune__WCET自动调优.sh" "$CYCLIC"
else
    echo "    autotune cyclictest（静态 WCET_AUTOTUNE_SKIP_INSMOD=1）..."
    WCET_AUTOTUNE_SKIP_INSMOD=1 bash "$SCRIPT_DIR/plc_fusion_wcet_autotune__WCET自动调优.sh" "$CYCLIC"
fi

echo "✅ WCET nightly 完成"
