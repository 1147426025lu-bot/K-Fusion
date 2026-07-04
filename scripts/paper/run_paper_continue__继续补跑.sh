#!/bin/bash
# ============================================================================
# run_paper_continue__继续补跑.sh — 修复 cyclictest 参数后重建并补跑未完成项
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"

export PATH="${PATH:-/usr/local/llvm-17/bin:/usr/lib/llvm-19/bin:$PATH}"
PRJ="$(paper_root)"
PAPER_RUNS="${PAPER_RUNS:-3}"
DURATION_MIN="${DURATION_MIN:-15}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$PRJ/results/paper/continue_run_${STAMP}.log"
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

echo "=== 论文实验继续 ${STAMP} ==="
plc_check_sudo 1

echo ""
echo "########## [0] 重建 cyclictest fused .ko（修正 FUSE_MAIN_ARGS）##########"
bash "$PRJ/scripts/plc_fuse__内核化主流程.sh" \
    "$PRJ/manifests/manifest_cyclictest__主线压测.env"
(
    cd "$PRJ/scripts/deploy"
    export FORCE_REBUILD_KERNEL_O=1 IGNITE_BUILD_ONLY=1
    bash ignite_official_cycletest__cyclictest主线.sh
)
if lsmod | grep -q '^official_cycletest_mod'; then
    bash "$PRJ/scripts/safe_rmmod_fused__安全卸载.sh" official_cycletest_mod || true
fi

echo ""
echo "########## [1] 基线矩阵 userspace + fused（跳过 baseline_ko）##########"
export PAPER_RUNS DURATION_MIN SKIP_BASELINE_KO=1 FUSED_SOAK_COOLDOWN_SEC=300
bash "$SCRIPT_DIR/run_paper_baseline_matrix__论文基线矩阵.sh"

echo ""
echo "########## [2] 消融矩阵 ##########"
DURATION_MIN="$DURATION_MIN" bash "$SCRIPT_DIR/run_paper_ablation_matrix__论文消融矩阵.sh"

echo ""
echo "########## [3] signaltest 第二应用 ##########"
DURATION_SEC="${DURATION_SEC:-120}" bash "$SCRIPT_DIR/run_paper_second_app__论文第二应用.sh"

echo ""
echo "########## [4] 整理与清理 ##########"
bash "$SCRIPT_DIR/paper_consolidate__整理结果.sh"

echo "✅ 继续补跑完成 log=$LOG"
