#!/usr/bin/env bash
# run_four_arms_v2__四组重跑.sh — 计算链统一后的四组正式重跑（A/B/C/D × 5 轮 × 60s）
#
# 与 2026-11-19 那批的区别（那批数据已作废，见 PAPER_WALKTHROUGH §5.7 取代声明）：
#   1. 四组共用同一份计算体（kfusion/src/let_pc_compute__产消计算体.h），
#      相位统一 prod@0 / commit@400 / fetch@500 / act@700，消费者吃同拍快照；
#   2. C 组不再用独立手写模块，而是**同一份宿主**，只把读写实现 let_impl.c
#      换成手写版（build_hand_host 产出），C vs D 因此是单变量；
#   3. 采集统一走 run_let_arm（同参数、同预热截断 10s）；
#   4. 内核三臂按轮次轮换执行顺序（B→C→D / C→D→B / …），避免单臂连续跑把
#      热漂移记到臂头上；A 组（用户态）另起一段，因为它的噪声结构与内核臂不同。
#
# 用法：
#   OUT=/home/pi/kfusion_results/four_arms_v2 bash run_four_arms_v2__四组重跑.sh
#   # 复用已有构建（换平台条件重跑，例如把 cpufreq governor 改成 performance）：
#   OUT=... REUSE_BUILD=/home/pi/kfusion_results/four_arms_v2 bash run_four_arms_v2__四组重跑.sh
# 环境：REPS(5) DURATION(60) KF_WARMUP(10) REUSE_BUILD(空)
set -uo pipefail

KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$KF/.." && pwd)"
SRC="$KF/examples/ecos/let_pc_shared__LET产消共享.c"
CONTRACT="$KF/contracts/examples/let_pc_shared.time.json"
OUT="${OUT:?需要 OUT=<产物目录>}"
REPS="${REPS:-5}"
DURATION="${DURATION:-60}"
WARMUP="${KF_WARMUP:-10}"

mkdir -p "$OUT"
echo "══ 四组重跑 → $OUT（$REPS × ${DURATION}s）══"

# 平台快照：governor 与当前频率必须记下来。ondemand 在隔离核上空转时会把频率降到
# 600 MHz，同样的代码路径会因此变频 → 硬中断入口延迟差 2–3 倍（本文件 2026-11-21
# 的 ondemand/performance 两批就是这个对照）。不记频率的时延数字不可比。
{
  echo "date=$(date -Is)"
  echo "kernel=$(uname -r)"
  echo "cmdline=$(cat /proc/cmdline)"
  for c in 3; do
    echo "cpu${c}_governor=$(cat /sys/devices/system/cpu/cpu${c}/cpufreq/scaling_governor 2>/dev/null)"
    echo "cpu${c}_cur_freq=$(cat /sys/devices/system/cpu/cpu${c}/cpufreq/scaling_cur_freq 2>/dev/null)"
    echo "cpu${c}_freqs=$(cat /sys/devices/system/cpu/cpu${c}/cpufreq/scaling_available_frequencies 2>/dev/null)"
  done
} >"$OUT/platform_start.txt" 2>&1
cat "$OUT/platform_start.txt"

REUSE="${REUSE_BUILD:-}"
if [ -n "$REUSE" ]; then
  # 复用已有构建：只换平台条件（governor / 负载 / 采样窗口）重跑，不重编。
  # 前提：$REUSE 下已有 build_c、build_d 的 .ko，且两份宿主 sha256 相同。
  echo "[$(date +%H:%M:%S)] 0/3 复用构建 $REUSE（跳过重编）"
  BUILDD="$REUSE/build_d"
  BUILDC="$REUSE/build_c"
else
  BUILDD="$OUT/build_d"
  BUILDC="$OUT/build_c"
  echo "[$(date +%H:%M:%S)] 1/3 构建 D 组（seam：宿主 + 生成读写实现）"
  KF_LET_STUB_SEAM=1 FORCE_CHOSEN=D@linux_kernel TIME_CONTRACT="$CONTRACT" \
    OUT_DIR="$BUILDD" OUT="$BUILDD" \
    bash "$KF/scripts/kf_unify__统一流水线.sh" "$SRC" >"$BUILDD.log" 2>&1 || {
      tail -15 "$BUILDD.log" >&2; echo "❌ D 组构建失败"; exit 1; }

  echo "[$(date +%H:%M:%S)] 2/3 构建 C 组（同一宿主，替换 let_impl.c 为手写版）"
  FORCE_CHOSEN=D@linux_kernel TIME_CONTRACT="$CONTRACT" OUT_DIR="$BUILDC" \
    bash "$KF/scripts/build_hand_host__C组共用宿主.sh" "$SRC" >"$BUILDC.log" 2>&1 || {
      tail -15 "$BUILDC.log" >&2; echo "❌ C 组构建失败"; exit 1; }
fi

KO_D="$BUILDD/kmod_hrtimer/kf_task.ko"
KO_C="$BUILDC/kmod_hrtimer/kf_task.ko"
[ -f "$KO_D" ] && [ -f "$KO_C" ] || { echo "❌ 缺 .ko"; exit 1; }
echo "  D ko $(sha256sum "$KO_D" | cut -c1-16)…"
echo "  C ko $(sha256sum "$KO_C" | cut -c1-16)…"
echo "  宿主 sha $(sha256sum "$BUILDD/kmod_hrtimer/ecos_fused_linux.c" | cut -c1-16)… / $(sha256sum "$BUILDC/kmod_hrtimer/ecos_fused_linux.c" | cut -c1-16)…"

echo "[$(date +%H:%M:%S)] 3/3 A 组（用户态，5 轮）"
OUT="$OUT/A" DURATION_SEC="$DURATION" REPS="$REPS" WARMUP_SEC="$WARMUP" \
  bash "$KF/scripts/run_let_userspace__A组用户态实验.sh" >"$OUT/A.log" 2>&1 || {
    tail -15 "$OUT/A.log" >&2; echo "❌ A 组失败"; exit 1; }

# 内核三臂按轮次轮换顺序
ARM_ORDER=(B C D)
for r in $(seq 1 "$REPS"); do
  off=$(( (r - 1) % 3 ))
  echo "[$(date +%H:%M:%S)] 轮 $r：$(for i in 0 1 2; do echo -n "${ARM_ORDER[$(((off+i)%3))]} "; done)"
  for i in 0 1 2; do
    arm="${ARM_ORDER[$(((off+i)%3))]}"
    case "$arm" in
      B) ko="$KO_D"; hrd=0 ;;   # 同一宿主，读写走工作线程
      C) ko="$KO_C"; hrd=1 ;;   # 同一宿主，手写读写实现
      D) ko="$KO_D"; hrd=1 ;;   # 同一宿主，生成读写实现
    esac
    KO="$ko" TAG="$arm" OUT="$OUT/$arm" REPS=1 DURATION="$DURATION" \
      KF_HRD="$hrd" KF_REP_TAG="rep$r" KF_WARMUP="$WARMUP" \
      bash "$KF/scripts/run_let_arm__单臂N轮.sh" >>"$OUT/arm_${arm}.log" 2>&1 || {
        echo "❌ 轮 $r 的 $arm 失败（见 $OUT/arm_${arm}.log）"; exit 1; }
    echo "    $arm rep$r ok"
  done
done

echo
echo "══ ALL DONE $(date +%H:%M:%S) ══"
for arm in A B C D; do
  for f in "$OUT/$arm/figs/table_$arm.json" "$OUT/$arm/summary.json"; do
    [ -f "$f" ] || continue
    echo "  $arm: $f"
    break
  done
done
