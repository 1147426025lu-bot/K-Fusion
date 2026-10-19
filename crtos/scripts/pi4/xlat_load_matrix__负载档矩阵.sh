#!/usr/bin/env bash
# xlat_load_matrix__负载档矩阵.sh — 对齐 Access'25 的五档负载
#
# 设计要点
#   - 负载施加在 **root cell 的 CPU 0/2/3**（CFS 普通进程），不碰 CPU1（inmate 的核）。
#     这与 cRTOS 论文的做法一致（"stressors were executed on the side-by-side Linux"）。
#   - 采集器 xlat_collect 用 SCHED_FIFO 60 绑 CPU2：RT 优先于 CFS 的 stress worker，
#     所以采集器不会被"调度饿死"，但**缓存/内存带宽/总线的争用仍然存在** ——
#     这正是五档要衡量的东西。
#   - xlat（inmate 侧）由串口在 NSH 里单独启动且长期运行，本脚本只切负载与采集窗口，
#     因此五档之间 inmate 不重启，避免"重启导致地板变化"混入。
#
# 用法: bash xlat_load_matrix__负载档矩阵.sh <mode> [dur_sec] [tag]
#   mode: noload | cpu | cache | vmem | stream
# 输出: /root/kfusion-pi4/results/xlat/load_<tag>.bin
set -u

MODE="${1:?usage: $0 <noload|cpu|cache|vmem|stream> [dur_sec] [tag]}"
DUR="${2:-900}"
TAG="${3:-$MODE}"

OUT="/root/kfusion-pi4/results/xlat/load_${TAG}.bin"
LOG="/root/kfusion-pi4/results/xlat/load_${TAG}.log"
COLL=/root/kfusion-pi4/scripts/xlat_collect
CPUS=0,2,3

mkdir -p /root/kfusion-pi4/results/xlat

case "$MODE" in
  noload) STRESS="" ;;
  cpu)    STRESS="--cpu 3 --cpu-method matrixprod" ;;
  cache)  STRESS="--cache 3" ;;
  vmem)   STRESS="--vm 3 --vm-bytes 256M" ;;
  stream) STRESS="--stream 3" ;;
  *) echo "unknown mode: $MODE"; exit 1 ;;
esac

SPID=""
cleanup() { [ -n "$SPID" ] && kill "$SPID" 2>/dev/null; }

echo "=== mode=$MODE dur=${DUR}s tag=$TAG ===" | tee "$LOG"

if [ -n "$STRESS" ]; then
  echo "stress-ng $STRESS --taskset $CPUS -t ${DUR}s" | tee -a "$LOG"
  # shellcheck disable=SC2086
  stress-ng $STRESS --taskset "$CPUS" -t "${DUR}s" --metrics-brief \
    > "/root/kfusion-pi4/results/xlat/stress_${TAG}.log" 2>&1 &
  SPID=$!
  sleep 3            # 让负载先起来，再开始采集

  echo "--- 负载进程（应看到 stress-ng 在 0/2/3）---" | tee -a "$LOG"
  ps -eo pid,psr,comm | grep -E "stress-ng-(cpu|cache|vm|stream)" | head -4 | tee -a "$LOG"
fi

echo "--- 开始采集 ---" | tee -a "$LOG"
chrt -f 60 "$COLL" -d "$DUR" -o "$OUT" -c 2 2>&1 | tee -a "$LOG"

cleanup
wait 2>/dev/null

echo "--- 完成: $OUT ---" | tee -a "$LOG"
ls -l "$OUT" | tee -a "$LOG"
