#!/usr/bin/env bash
# 实验四：中断影响评价 —— victim cyclictest 三条件对照
# 唯一变量 = LET 模块状态 / 共享数据读写的执行位置：
#   baseline : 无 kf_task 模块，victim 独占隔离核（系统底噪）
#   thread   : LET 模块 hrtimer_hard=0，读写在 FIFO80 RT 任务上下文
#   irq      : LET 模块 hrtimer_hard=1，读写在 hrtimer 硬中断上下文
# victim cyclictest：优先级 90（高于 LET 任务 80）、绑同一隔离核 CPU3、1ms 周期
#   → thread 臂 victim(90) 不被 LET 任务(80) 抢占；irq 臂硬中断读写会抢占 victim
#   → 据此评价"把读写放进硬中断"对其他高优先级实时任务的延迟外溢
# 三条件窗口一致（每条件 DURATION_SEC×REPS），可比；按纪律不报均值下定论。
set -euo pipefail

OUT="${OUT:-/home/pi/kfusion_results/let_victim_cyclictest_$(date +%Y%m%d_%H%M%S)}"
DURATION="${DURATION_SEC:-60}"
REPS="${REPS:-3}"
CPU="${KF_CPU:-3}"
PRIO="${VIC_PRIO:-90}"
INTERVAL="${VIC_INTERVAL:-1000}"
KO_SRC="${KO_SRC:-/home/pi/kfusion_results/let_jitter_idle_60s_3x60s}"
KO_T="$KO_SRC/build_thread/kmod_hrtimer/kf_task.ko"
KO_I="$KO_SRC/build_irq/kmod_hrtimer/kf_task.ko"

command -v cyclictest >/dev/null || { echo "cyclictest missing" >&2; exit 1; }
[ -f "$KO_T" ] && [ -f "$KO_I" ] || { echo "ko missing: $KO_T / $KO_I" >&2; exit 1; }
mkdir -p "$OUT"
echo "OUT=$OUT DUR=${DURATION}s REPS=$REPS CPU=$CPU PRIO=$PRIO INTERVAL=${INTERVAL}us" | tee "$OUT/run.cfg"

unload(){ lsmod | awk '{print $1}' | grep -qx kf_task && sudo rmmod kf_task || true; sleep 0.3; }
trap unload EXIT

dump_params(){
  local f="$1"
  {
    for p in hrtimer_hard periods let_ctx_hardirq let_ctx_softirq let_ctx_task \
             let_rd_jit_max_ns let_wr_jit_max_ns let_rd_ok let_wr_ok \
             let_rw_irq let_skip_commit let_log_drops deadline_miss; do
      [ -f /sys/module/kf_task/parameters/$p ] && echo "$p=$(cat /sys/module/kf_task/parameters/$p)"
    done
  } > "$f"
}

run_cyclic(){
  local dest="$1" cond="$2" rep="$3" line cnt mn act av mx
  sudo cyclictest -a "$CPU" -t1 -p "$PRIO" -i "$INTERVAL" -D "${DURATION}s" -q \
    > "$dest/cyclictest.log" 2>&1 || true
  line="$(grep '^T:' "$dest/cyclictest.log" | tail -1 || true)"
  cnt=$(printf '%s\n' "$line" | sed -n 's/.*C: *\([0-9][0-9]*\).*/\1/p')
  mn=$(printf '%s\n' "$line" | sed -n 's/.*Min: *\([0-9][0-9]*\).*/\1/p')
  act=$(printf '%s\n' "$line" | sed -n 's/.*Act: *\([0-9][0-9]*\).*/\1/p')
  av=$(printf '%s\n' "$line" | sed -n 's/.*Avg: *\([0-9][0-9]*\).*/\1/p')
  mx=$(printf '%s\n' "$line" | sed -n 's/.*Max: *\([0-9][0-9]*\).*/\1/p')
  printf '{"cond":"%s","rep":%s,"count":%s,"min_us":%s,"act_us":%s,"avg_us":%s,"max_us":%s}\n' \
    "$cond" "$rep" "${cnt:-0}" "${mn:-0}" "${act:-0}" "${av:-0}" "${mx:-0}" > "$dest/cyclic.json"
  [ "${cnt:-0}" -gt 0 ] || { echo "  !! cyclictest parse failed: [$line]" >&2; return 1; }
  echo "  max=${mx}us act=${act}us min=${mn}us n=${cnt}"
}

for rep in $(seq 1 "$REPS"); do
  for cond in baseline thread irq; do
    dest="$OUT/$cond/rep$rep"; mkdir -p "$dest"
    unload
    case "$cond" in
      baseline) echo "[$cond/$rep] no module (system floor)";;
      thread)
        sudo insmod "$KO_T" "cpu=$CPU" period_us=1000 rt_priority=80 hrtimer_hard=0 let_log_cap=1000
        sleep 1; dump_params "$dest/module_params.txt";;
      irq)
        sudo insmod "$KO_I" "cpu=$CPU" period_us=1000 rt_priority=80 hrtimer_hard=1 let_log_cap=1000
        sleep 1; dump_params "$dest/module_params.txt";;
    esac
    echo "[$cond/$rep] cyclictest ${DURATION}s cpu$CPU prio$PRIO"
    run_cyclic "$dest" "$cond" "$rep"
    [ "$cond" = baseline ] || dump_params "$dest/module_params_end.txt"
    unload
    sync
  done
done

echo "done -> $OUT" | tee -a "$OUT/run.cfg"
