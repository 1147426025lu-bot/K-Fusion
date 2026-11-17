#!/usr/bin/env bash
# 实验四脚手架：短测记录 let_op_dur_max_ns / 上下文，并可选跑 cyclictest victim
set -euo pipefail
OUT="${OUT:-/home/pi/kfusion_results/let_overhead_probe_$(date +%Y%m%d_%H%M%S)}"
DURATION_SEC="${DURATION_SEC:-60}"
CPU="${KF_CPU:-3}"
mkdir -p "$OUT"
echo "overhead probe → $OUT"

# 复用正确性短测构建产物若存在，否则跑 60s irq+thread
DURATION_SEC="$DURATION_SEC" OUT="$OUT" \
  bash /home/pi/K-Fusion/kfusion/scripts/run_let_jitter_exp__读写抖动实验.sh

{
  echo "# overhead probe"
  echo "let_op_dur_max_ns 来自模块参数（单次读写桩 t_after-t_before）"
  echo "完整 hrtimer 回调耗时（入口→出口）尚未单独探针；下一步可加 kf_tick 入口/出口采样"
  echo "victim：若系统有 cyclictest，可在同核/异核对照启用 irq 读写前后的 max latency"
} | tee "$OUT/README_overhead.md"

# best-effort cyclictest on another CPU if present
if command -v cyclictest >/dev/null; then
  VIC_CPU=$((CPU==0?1:0))
  timeout "${DURATION_SEC}" cyclictest -a "$VIC_CPU" -t1 -p 80 -i 1000 -q \
    >"$OUT/cyclictest_parallel.log" 2>&1 || true
  echo "cyclictest log → $OUT/cyclictest_parallel.log"
fi
echo "done $OUT"
