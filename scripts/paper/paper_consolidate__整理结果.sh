#!/bin/bash
# ============================================================================
# paper_consolidate__整理结果.sh — 合并 CSV、生成总表、删除冗余 paper 产物
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
PRJ="$(paper_root)"
PAPER="$PRJ/results/paper"

echo "=== 整理 results/paper ==="

# --- 合并 baseline CSV ---
BASE_DIR="$PAPER/baseline_matrix"
MERGED="$BASE_DIR/paper_baseline_merged.csv"
: >"$MERGED"
paper_csv_header "$MERGED"
shopt -s nullglob
for f in "$BASE_DIR"/paper_baseline_*.csv; do
    case "$(basename "$f")" in
        paper_baseline_merged.csv) continue ;;
    esac
    tail -n +2 "$f" >>"$MERGED"
done
if [ -s "$MERGED" ]; then
    python3 "$SCRIPT_DIR/paper_summarize_results__论文结果汇总.py" \
        --csv "$MERGED" --out "$BASE_DIR/paper_baseline_merged_summary.md"
    echo "✅ 基线合并 → $MERGED"
    export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplconfig}"
    mkdir -p "$MPLCONFIGDIR"
    python3 "$SCRIPT_DIR/paper_plot_results__论文出图.py" --paper-dir "$PAPER" \
        --out-dir "$PRJ/docs/paper/figures" || echo "⚠️  数据出图跳过（无有效 abs_max 或 matplotlib）"
fi

# --- 保留最新 feasibility ---
FEAS="$PAPER/feasibility"
if [ -d "$FEAS" ]; then
    latest_feas="$(ls -t "$FEAS"/paper_feasibility_*.csv 2>/dev/null | head -1 || true)"
    if [ -n "$latest_feas" ]; then
        cp -f "$latest_feas" "$FEAS/LATEST_feasibility.csv"
        latest_sum="${latest_feas%.csv}_summary.md"
        [ -f "$latest_sum" ] && cp -f "$latest_sum" "$FEAS/LATEST_feasibility_summary.md"
        for old in "$FEAS"/paper_feasibility_*.csv; do
            [ "$old" = "$latest_feas" ] && continue
            [ "$old" = "$FEAS/LATEST_feasibility.csv" ] && continue
            rm -f "$old" "${old%.csv}_summary.md"
        done
        echo "✅ feasibility 保留最新: $(basename "$latest_feas")"
    fi
fi

# --- 删除 baseline_matrix 中无效补跑（无 abs_max 的 userspace/fused）---
if [ -f "$MERGED" ]; then
    :
fi
python3 - <<'PY' "$BASE_DIR" 2>/dev/null || true
import csv, sys
from pathlib import Path
base = Path(sys.argv[1])
for name in list(base.glob("paper_baseline_20260701_223219.csv")):
    rows = list(csv.DictReader(name.open()))
    kept = [r for r in rows if r.get("abs_max_ns") or r.get("baseline") == "baseline_ko"]
    if len(kept) < len(rows):
        out = name.with_suffix(".csv.bak")
        name.rename(out)
        with name.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(kept)
        print(f"trimmed invalid rows: {name.name} ({len(rows)-len(kept)} removed)")
PY

# --- 删除 baseline_ko 重复目录（日志已在 baseline_matrix/）---
if [ -d "$PAPER/baseline_ko" ]; then
    rm -rf "$PAPER/baseline_ko"
    echo "✅ 删除冗余 baseline_ko/（日志已在 baseline_matrix/）"
fi

if [ -d "$PAPER/userspace" ]; then
    rm -rf "$PAPER/userspace"
    echo "✅ 删除冗余 userspace/（日志在 baseline_matrix/）"
fi

# --- 删除本地论文导出副本 ---
rm -rf "$PRJ/plc_paper_writing" "$PRJ/plc_paper_writing.tar.gz" 2>/dev/null || true

# --- 删除补跑/矩阵过程中的空 log ---
find "$PAPER" -type f -name '*.log' -size 0 -delete 2>/dev/null || true

# --- 总览 README ---
LATEST_BASE="$MERGED"
LATEST_ABL="$(ls -t "$PAPER/ablation"/paper_ablation_*.csv 2>/dev/null | head -1 || true)"
LATEST_FEAS="$FEAS/LATEST_feasibility.csv"

{
    echo "# 论文实验结果索引"
    echo ""
    echo "生成: $(date -Iseconds)"
    echo ""
    echo "## 主表（三基线）"
    echo ""
    if [ -f "$BASE_DIR/paper_baseline_merged_summary.md" ]; then
        cat "$BASE_DIR/paper_baseline_merged_summary.md"
    else
        echo "（尚无 baseline 合并表，请先跑 run_paper_baseline_matrix 或 run_paper_missing）"
    fi
    echo ""
    echo "## 可行性（最新）"
    echo ""
    if [ -f "$LATEST_FEAS" ]; then
        echo "CSV: \`feasibility/$(basename "$LATEST_FEAS")\`"
        [ -f "$FEAS/LATEST_feasibility_summary.md" ] && cat "$FEAS/LATEST_feasibility_summary.md"
    fi
    echo ""
    echo "## 消融"
    echo ""
    if [ -n "$LATEST_ABL" ]; then
        python3 "$SCRIPT_DIR/paper_summarize_results__论文结果汇总.py" \
            --csv "$LATEST_ABL" --out "$PAPER/ablation/LATEST_ablation_summary.md" 2>/dev/null || true
        echo "CSV: \`ablation/$(basename "$LATEST_ABL")\`"
        [ -f "$PAPER/ablation/LATEST_ablation_summary.md" ] && cat "$PAPER/ablation/LATEST_ablation_summary.md"
    else
        echo "（尚无消融 CSV）"
    fi
    echo ""
    echo "## 第二应用"
    echo ""
    if [ -f "$PAPER/second_app/signaltest_runs.csv" ]; then
        echo "CSV: \`second_app/signaltest_runs.csv\`"
        tail -5 "$PAPER/second_app/signaltest_runs.csv" | sed 's/^/    /'
    fi
    echo ""
    echo "## 复现命令"
    echo ""
    echo '```bash'
    echo "bash scripts/paper/run_paper_continue__继续补跑.sh"
    echo "bash scripts/paper/paper_consolidate__整理结果.sh"
    echo '```'
} >"$PAPER/README__论文实验索引.md"

echo ""
echo "✅ 总索引 → $PAPER/README__论文实验索引.md"

# --- soak/stress 工程日志清理 ---
if [ -x "$PRJ/scripts/maintenance/cleanup_results__清理结果.sh" ]; then
    bash "$PRJ/scripts/maintenance/cleanup_results__清理结果.sh"
fi

echo "✅ 整理完成"
