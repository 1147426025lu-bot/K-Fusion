#!/bin/bash
# plot_pi4_crtos__出图.sh — 用仓库同款出图脚本，为 Pi4 测量产出「抖动分布图」+「延迟时间线图」
#
# 复用仓库既有出图器：kfusion/src/plot_frequency_polygon__抖动绘图.py
# （该脚本被 scripts/paper/plot_dual_from_artifact__双图出图.sh 调用，产出与
#   kfusion/docs/examples/jitter_distribution_*.png / jitter_latency_timeline_*.png 同款）
#
# 用法:
#   bash crtos/scripts/pi4/plot_pi4_crtos__出图.sh <INMATE_LOG> [LABEL] [OUT_DIR]
#
# 输入：rtlat 的串口日志（含 rtlat CLKRES / STATS / HIST / HISTDATA / SAMPLE 行）
# 输出：<OUT_DIR>/fig_pi4_inmate_dist__inmate抖动分布.{png,svg}
#       <OUT_DIR>/fig_pi4_inmate_timeline__inmate延迟时间线.{png,svg}
set -euo pipefail

INMATE_LOG="${1:?usage: plot_pi4_crtos__出图.sh <INMATE_LOG> [LABEL] [OUT_DIR]}"
LABEL="${2:-Pi4B NuttX inmate (Jailhouse cell, tickless)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CRTOS="$(cd "$HERE/../.." && pwd)"
REPO="$(cd "$CRTOS/.." && pwd)"
OUT_DIR="${3:-$CRTOS/results/figures}"
PLOT="$REPO/kfusion/src/plot_frequency_polygon__抖动绘图.py"

[ -f "$INMATE_LOG" ] || { echo "输入日志不存在: $INMATE_LOG" >&2; exit 1; }
[ -f "$PLOT" ] || { echo "出图脚本不存在: $PLOT" >&2; exit 1; }

# 选 Python：优先仓库隔离 venv，回退系统 python3
if [ -x "$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe" ]; then
  PY="$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
elif [ -x "/c/Users/86150/.workbuddy/binaries/python/envs/default/Scripts/python.exe" ]; then
  PY="/c/Users/86150/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
else
  PY="python3"
fi

mkdir -p "$OUT_DIR"
echo "== 出图 =="
echo "输入: $INMATE_LOG"
echo "脚本: $PLOT"
echo "Python: $PY"

"$PY" "$PLOT" \
  --input-log "$INMATE_LOG" \
  --output "$OUT_DIR/fig_pi4_inmate_dist__inmate抖动分布.png" \
  --latency-output "$OUT_DIR/fig_pi4_inmate_timeline__inmate延迟时间线.png" \
  --label "$LABEL" \
  --svg

echo "完成 → $OUT_DIR"
