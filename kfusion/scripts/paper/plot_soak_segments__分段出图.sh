#!/bin/bash
# ============================================================================
# plot_soak_segments__分段出图.sh — 对一个浸泡根目录下的所有段逐一出图
# ============================================================================
# 为什么要按段循环而不是一条命令画一张：
#   三段（15m/1h/8h）的数据量差 32 倍，8h 的 run.log 有 1M 抽稀样本 + 4096 桶直方图，
#   一次画完容易中途 OOM 或被 ssh 超时打断。按段独立执行，已画过的段直接跳过，
#   这样「8h 画到一半失败」不会连累 15m/1h 的图。
#
# 用法: bash plot_soak_segments__分段出图.sh <soak根目录> [输出目录]
#   soak 根目录结构：<root>/15m/run.log、<root>/1h/run.log、<root>/8h/run.log
# ============================================================================
set -u
ROOT="${1:?需要 soak 根目录}"
OUT="${2:-/home/pi/kfusion_results/figs}"
KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DUAL="$KF/scripts/paper/plot_dual_from_artifact__双图出图.sh"

mkdir -p "$OUT"
for d in "$ROOT"/*/; do
    seg="$(basename "$d")"
    log="$d/run.log"
    [ -f "$log" ] || { echo "跳过 $seg（无 run.log）"; continue; }
    if [ -f "$OUT/$(basename "$ROOT")_${seg}.png" ] && [ "${FORCE_PLOT:-0}" != "1" ]; then
        echo "已有 $(basename "$ROOT")_${seg}.png，跳过"
        continue
    fi
    echo "== 出图 $seg（$(du -h "$log" | cut -f1)）"
    bash "$DUAL" "$(basename "$ROOT")" "$seg" "$OUT/$(basename "$ROOT")" "" "$log" 2>&1 | tail -2
done
ls -l "$OUT" | tail -8
