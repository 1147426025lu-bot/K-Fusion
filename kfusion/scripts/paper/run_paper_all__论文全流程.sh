#!/bin/bash
# ============================================================================
# run_paper_all__论文全流程.sh — 按步骤跑论文实验（可分段、可跳过）
# ============================================================================
# 用法:
#   bash scripts/paper/run_paper_all__论文全流程.sh          # 全流程（耗时长）
#   PAPER_STEP=1 bash ...                                     # 只跑第 1 步
#   PAPER_QUICK=1 bash ...                                    # 快速冒烟（2min×1次）
#
# 步骤:
#   1 可行性扫描（秒级）
#   2 基线矩阵（userspace / 手写ko / fused × soak/stress）
#   3 消融矩阵
#   4 第二应用 signaltest
#   5 汇总已有 CSV
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "${PAPER_QUICK:-0}" = "1" ]; then
    export PAPER_RUNS=1
    export DURATION_MIN=15
    export MATRIX_COOLDOWN_SEC=60
    export SKIP_USERSPACE=1
fi

step="${PAPER_STEP:-all}"

run_step() {
    local n="$1" name="$2" cmd="$3"
    if [ "$step" != "all" ] && [ "$step" != "$n" ]; then
        return 0
    fi
    echo ""
    echo "########## 步骤 $n: $name ##########"
    eval "$cmd"
}

run_step 1 "可行性扫描" "bash '$SCRIPT_DIR/run_paper_feasibility__论文可行性扫描.sh'"
run_step 2 "基线矩阵" "bash '$SCRIPT_DIR/run_paper_baseline_matrix__论文基线矩阵.sh'"
run_step 3 "消融矩阵" "bash '$SCRIPT_DIR/run_paper_ablation_matrix__论文消融矩阵.sh'"
run_step 4 "第二应用 signaltest" "bash '$SCRIPT_DIR/run_paper_second_app__论文第二应用.sh'"
run_step 5 "汇总 CSV" "
    for f in '$SCRIPT_DIR/../../results/paper/'*/*.csv; do
        [ -f \"\$f\" ] || continue
        python3 '$SCRIPT_DIR/paper_summarize_results__论文结果汇总.py' --csv \"\$f\" --out \"\${f%.csv}_summary.md\" || true
    done
"

echo ""
echo "✅ 论文流程完成。结果目录: results/paper/"
echo "   计划文档: docs/paper/PAPER_PLAN__论文计划.md"
