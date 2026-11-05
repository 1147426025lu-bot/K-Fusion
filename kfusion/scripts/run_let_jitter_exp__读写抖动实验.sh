#!/usr/bin/env bash
# run_let_jitter_exp__读写抖动实验.sh
# 短测冒烟 →（可选）3×N 分钟 thread/irq 对照 → 出图
#
#   MODE=smoke DURATION_SEC=8 bash kfusion/scripts/run_let_jitter_exp__读写抖动实验.sh
#   MODE=idle  DURATION_SEC=300 REPS=3 bash ...
#   MODE=load  DURATION_SEC=300 REPS=3 bash ...   # 需 stress-ng
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KF="$ROOT/kfusion"
SRC="$KF/examples/ecos/let_pc_shared__LET产消共享.c"
CONTRACT_IRQ="$KF/contracts/examples/let_pc_shared.time.json"
MODE="${MODE:-smoke}"
DURATION_SEC="${DURATION_SEC:-8}"
REPS="${REPS:-1}"
OUT="${OUT:-/home/pi/kfusion_results/let_jitter_${MODE}_$(date +%Y%m%d_%H%M%S)}"
CPU="${KF_CPU:-3}"
PY="${PYTHON:-python3}"
export FORCE_CHOSEN="${FORCE_CHOSEN:-D@linux_kernel}"
export HOST_MODEL=single_worker
export PLAN_SCHEMA_GATE=1
export SKIP_HOST=1
export PLATFORM=linux_kernel
export KERNEL_STATE_SYMS="${KERNEL_STATE_SYMS:-g_state,g_cmd_pending,g_cmd,g_cmd_ver,g_cmd_snap,g_cmd_snap_ver,g_out,g_seed}"
# 5min@1ms×2ops ≈ 600k；冒烟用小缓冲
if [ "$MODE" = "smoke" ]; then
  export KF_LET_LOG_CAP="${KF_LET_LOG_CAP:-20000}"
else
  export KF_LET_LOG_CAP="${KF_LET_LOG_CAP:-700000}"
fi

mkdir -p "$OUT"
echo "OUT=$OUT MODE=$MODE DUR=${DURATION_SEC}s REPS=$REPS LOG_CAP=$KF_LET_LOG_CAP"

CONTRACT_THREAD="$OUT/let_pc_shared.thread.time.json"
"$PY" - "$CONTRACT_IRQ" "$CONTRACT_THREAD" <<'PY'
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
for pin in (c.get("pins") or {}).values():
    if isinstance(pin, dict) and pin.get("kind") in ("output_commit", "input_snapshot"):
        pin["path"] = "thread"
json.dump(c, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(sys.argv[2], "a").write("\n")
PY

die() { echo "❌ $*" >&2; exit 1; }

build_arm() {
  local tag="$1" contract="$2"
  local arm_out="$OUT/build_$tag"
  local log="$OUT/build_${tag}.log"
  mkdir -p "$arm_out"
  # 构建日志进文件；stdout 只回传 .ko 路径（供 KO_T=$(build_arm …)）
  if ! TIME_CONTRACT="$contract" OUT_DIR="$arm_out" OUT="$arm_out" \
       bash "$KF/scripts/kf_unify__统一流水线.sh" "$SRC" >"$log" 2>&1; then
    tail -40 "$log" >&2
    die "build $tag（详见 $log）"
  fi
  local ko
  ko="$(find "$arm_out" -name 'kf_task.ko' | head -n1)"
  [ -n "$ko" ] || die "no ko $tag"
  printf '%s\n' "$ko"
}

run_one() {
  local tag="$1" ko="$2" rep="$3"
  local dest="$OUT/$tag/rep$rep"
  mkdir -p "$dest"
  for m in kf_task; do
    lsmod | awk '{print $1}' | grep -qx "$m" && sudo rmmod "$m" || true
  done
  # HARD 对 irq 臂：模块参数覆盖（生成时已默认）；thread 臂强制 0
  local hard=0
  [ "$tag" = "irq" ] && hard=1
  sudo insmod "$ko" "cpu=$CPU" period_us=1000 rt_priority=80 "hrtimer_hard=$hard" \
    "let_log_cap=$KF_LET_LOG_CAP" || die "insmod $tag/$rep"
  sleep 1
  # 上下文快检
  {
    echo "hrtimer_hard=$(cat /sys/module/kf_task/parameters/hrtimer_hard)"
    for p in let_ctx_hardirq let_ctx_softirq let_ctx_task periods \
             let_rd_jit_max_ns let_wr_jit_max_ns let_rd_ok let_wr_ok \
             let_skip_commit let_log_drops; do
      [ -f /sys/module/kf_task/parameters/$p ] && echo "$p=$(cat /sys/module/kf_task/parameters/$p)"
    done
  } | tee "$dest/params_t1.txt"
  sleep "$DURATION_SEC"
  {
    for p in periods let_rd_jit_max_ns let_wr_jit_max_ns let_rd_ok let_wr_ok \
             let_rw_fires let_rw_irq let_skip_commit let_log_drops \
             let_ctx_hardirq let_ctx_softirq let_ctx_task \
             deadline_miss missing_groups release_overflow; do
      [ -f /sys/module/kf_task/parameters/$p ] && echo "$p=$(cat /sys/module/kf_task/parameters/$p)"
    done
  } | tee "$dest/params_end.txt"
  # 卸模块前导出日志
  if [ -c /dev/kf_let_log ]; then
    sudo dd if=/dev/kf_let_log of="$dest/raw.bin" bs=1M status=none || \
      sudo cat /dev/kf_let_log >"$dest/raw.bin"
  fi
  sudo rmmod kf_task || die "rmmod"
  "$PY" "$KF/scripts/let_log_dump__导出读写日志.py" \
    --bin "$dest/raw.bin" -o "$dest" --tag "${tag}_r${rep}" \
    | tee "$dest/dump_summary.json"
}

LOAD_PID=""
start_load() {
  if [ "$MODE" != "load" ]; then return; fi
  if command -v stress-ng >/dev/null; then
    # 在非测量核上打负载，避免直接饿死绑核工人（记录实际命令）
    stress-ng --cpu $(($(nproc)-1)) --timeout "${DURATION_SEC}s" --quiet &
    LOAD_PID=$!
    echo "load: stress-ng pid=$LOAD_PID" | tee "$OUT/load.txt"
  else
    echo "load: stress-ng 缺失，退化为 idle" | tee "$OUT/load.txt"
  fi
}
stop_load() {
  if [ -n "$LOAD_PID" ] && kill -0 "$LOAD_PID" 2>/dev/null; then
    kill "$LOAD_PID" 2>/dev/null || true
    wait "$LOAD_PID" 2>/dev/null || true
  fi
}

echo "==== build thread ===="
KO_T=$(build_arm thread "$CONTRACT_THREAD")
echo "==== build irq ===="
KO_I=$(build_arm irq "$CONTRACT_IRQ")

start_load
trap stop_load EXIT

for rep in $(seq 1 "$REPS"); do
  echo "==== thread rep$rep ===="
  run_one thread "$KO_T" "$rep"
  echo "==== irq rep$rep ===="
  run_one irq "$KO_I" "$rep"
done

stop_load
trap - EXIT

"$PY" "$KF/scripts/plot_let_jitter__读写抖动图.py" --root "$OUT" -o "$OUT/figs"
echo "✅ 完成 → $OUT"
ls -la "$OUT/figs" || true
