#!/bin/bash
# ============================================================================
# run_paper_missing__补跑缺失.sh — 仅跑此前缺失/不足的论文实验
# ============================================================================
# 缺失项: userspace 基线、消融、第二应用、W5 多任务、可行性刷新
# 已有一次 baseline_ko 时可 SKIP_BASELINE_KO=1 省时（结果合并见 paper_consolidate）
#
# 用法:
#   sudo -v
#   bash scripts/paper/run_paper_missing__补跑缺失.sh
#   PAPER_RUNS=3 DURATION_MIN=15 bash scripts/paper/run_paper_missing__补跑缺失.sh
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"

export PATH="${PATH:-/usr/local/llvm-17/bin:/usr/lib/llvm-19/bin:$PATH}"
PAPER_RUNS="${PAPER_RUNS:-3}"
DURATION_MIN="${DURATION_MIN:-15}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$SCRIPT_DIR/../../results/paper/missing_run_${STAMP}.log"
mkdir -p "$(dirname "$LOG")"

exec > >(tee -a "$LOG") 2>&1

echo "=== 论文缺失实验补跑 ${STAMP} ==="
echo "    PAPER_RUNS=$PAPER_RUNS DURATION_MIN=$DURATION_MIN"
plc_check_sudo 1

# 1) 可行性（含 plc_multitask 等新 manifest）
echo ""
echo "########## [1/5] 可行性扫描 ##########"
bash "$SCRIPT_DIR/run_paper_feasibility__论文可行性扫描.sh"

# 2) userspace + fused 补跑（跳过已有 baseline_ko 时可设 SKIP_BASELINE_KO=1）
echo ""
echo "########## [2/5] 基线矩阵（含 userspace）##########"
export PAPER_RUNS DURATION_MIN
export MATRIX_COOLDOWN_SEC="${MATRIX_COOLDOWN_SEC:-120}"
export SKIP_BASELINE_KO="${SKIP_BASELINE_KO:-1}"
bash "$SCRIPT_DIR/run_paper_baseline_matrix__论文基线矩阵.sh"

# 3) 消融
echo ""
echo "########## [3/5] 消融矩阵 ##########"
DURATION_MIN="$DURATION_MIN" bash "$SCRIPT_DIR/run_paper_ablation_matrix__论文消融矩阵.sh"

# 4) 第二应用
echo ""
echo "########## [4/5] signaltest 第二应用 ##########"
DURATION_SEC="${DURATION_SEC:-120}" bash "$SCRIPT_DIR/run_paper_second_app__论文第二应用.sh"

# 5) W5 多任务测量（userspace vs fused × soak/stress）
echo ""
echo "########## [5/5] plc_multitask W5 测量 ##########"
export PAPER_RUNS DURATION_SEC
bash "$SCRIPT_DIR/run_paper_multitask__论文多任务.sh"

echo ""
echo "✅ 缺失实验补跑完成 log=$LOG"
bash "$SCRIPT_DIR/paper_consolidate__整理结果.sh"
