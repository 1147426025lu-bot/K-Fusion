#!/usr/bin/env bash
# run_let_four_arms__四组对照5轮.sh — 开题四组对照一次跑齐
#
#   A) 用户态线程读写        → kf_let_userspace（run_let_userspace）
#   B) 内核 kthread 读写     → 生成模块 + path=thread（run_let_jitter_exp）
#   C) 手写 hrtimer 硬中断   → kf_hand_let（run_let_handwritten）
#   D) 编译器生成硬中断      → 生成模块 + path=irq（run_let_jitter_exp）
#
# 四组同周期(1000us)、同相位(0/400/500/700)、同计算体、同写桩/读桩语义、
# 同 36 字节日志格式；唯一变量是「读写桩在哪个上下文执行」。
#
# 用法（板端，需 sudo 免密）：
#   REPS=5 DURATION_SEC=60 bash kfusion/scripts/run_let_four_arms__四组对照5轮.sh
#   RUN=/home/pi/kfusion_results/let4_x bash ...
#   WITH_SOFT=1 bash ...      # 额外跑 C 软中断对照臂
#   REUSE_KO_DIR=<已有 build 目录> bash ...   # 跳过 B/D 的 build
#
# 产物：<RUN>/{A,C_hard,C_soft,BD}/ 与每段日志，最后出四组图到 <RUN>/figs
set -u

KF="${KF_ROOT:-/home/pi/K-Fusion}"
R="${RESULT_ROOT:-/home/pi/kfusion_results}"
RUN="${RUN:-$R/let4_$(date +%Y%m%d_%H%M%S)}"
REPS="${REPS:-5}"
DURATION_SEC="${DURATION_SEC:-60}"
WITH_SOFT="${WITH_SOFT:-1}"
mkdir -p "$RUN"

step() { echo "[$(date +%H:%M:%S)] ==== $* ===="; }
rc_of() { echo "[$(date +%H:%M:%S)] $1 rc=$2"; }

step "A 组 用户态线程 ${REPS}x${DURATION_SEC}s"
OUT="$RUN/A" DURATION_SEC="$DURATION_SEC" REPS="$REPS" \
  bash "$KF/kfusion/scripts/run_let_userspace__A组用户态实验.sh" \
  >"$RUN/A.log" 2>&1
rc_of A $?

step "C 组 手写硬中断 ${REPS}x${DURATION_SEC}s"
OUT="$RUN/C_hard" DURATION_SEC="$DURATION_SEC" REPS="$REPS" HRD=1 \
  bash "$KF/kfusion/scripts/run_let_handwritten__C组手写中断实验.sh" \
  >"$RUN/C_hard.log" 2>&1
rc_of C_hard $?

if [ "$WITH_SOFT" = "1" ]; then
  step "C' 手写软中断（单变量对照）${REPS}x${DURATION_SEC}s"
  OUT="$RUN/C_soft" DURATION_SEC="$DURATION_SEC" REPS="$REPS" HRD=0 \
    bash "$KF/kfusion/scripts/run_let_handwritten__C组手写中断实验.sh" \
    >"$RUN/C_soft.log" 2>&1
  rc_of C_soft $?
fi

step "B/D 组 内核 kthread 与生成硬中断 ${REPS}x${DURATION_SEC}s"
if [ -n "${REUSE_KO_DIR:-}" ]; then
  OUT="$RUN/BD" MODE=idle DURATION_SEC="$DURATION_SEC" REPS="$REPS" \
    REUSE_KO_DIR="$REUSE_KO_DIR" \
    bash "$KF/kfusion/scripts/run_let_jitter_exp__读写抖动实验.sh" \
    >"$RUN/BD.log" 2>&1
else
  OUT="$RUN/BD" MODE=idle DURATION_SEC="$DURATION_SEC" REPS="$REPS" \
    bash "$KF/kfusion/scripts/run_let_jitter_exp__读写抖动实验.sh" \
    >"$RUN/BD.log" 2>&1
fi
rc_of BD $?

step "出图"
PYTHON="${PYTHON:-python3}" "$PYTHON" \
  "$KF/kfusion/scripts/plot_let_four_arms__四组对照图.py" --run "$RUN" \
  >"$RUN/plot.log" 2>&1
rc_of plot $?

step "ALL DONE → $RUN"
