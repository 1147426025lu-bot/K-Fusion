#!/usr/bin/env bash
# run_let_compare3__LET三组对照.sh — LET 三组短测对照骨架
#
# 组：
#   A) userspace   — plc_let 用户态 STRICT（若本轮跳过则记 skipped）
#   B) kthread     — 内核单工人 + path=thread（契约强制 path）
#   C) irq         — 内核单工人 + path=irq（默认契约）
#
# 用法：
#   DURATION_SEC=5 bash kfusion/scripts/run_let_compare3__LET三组对照.sh
#   OUT=/home/pi/kfusion_results/let_compare3 bash ...
#
# 不替代长测；只收集 periods / let_rw_* / let_skip_commit / deadline_miss。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KF="$ROOT/kfusion"
SRC="$KF/examples/ecos/let_pc_shared__LET产消共享.c"
CONTRACT_IRQ="$KF/contracts/examples/let_pc_shared.time.json"
DURATION_SEC="${DURATION_SEC:-5}"
OUT="${OUT:-/home/pi/kfusion_results/let_compare3_$(date +%Y%m%d_%H%M%S)}"
PY="${PYTHON:-python3}"
CPU="${KF_CPU:-3}"

# 与 unify 烟测对齐的默认测量旋钮
export FORCE_CHOSEN="${FORCE_CHOSEN:-D@linux_kernel}"
export HOST_MODEL="${HOST_MODEL:-single_worker}"
export PLAN_SCHEMA_GATE="${PLAN_SCHEMA_GATE:-1}"
export SKIP_HOST="${SKIP_HOST:-1}"
export KERNEL_STATE_SYMS="${KERNEL_STATE_SYMS:-g_state,g_cmd_pending,g_cmd,g_cmd_ver,g_cmd_snap,g_cmd_snap_ver,g_out,g_seed}"
export PLATFORM="${PLATFORM:-linux_kernel}"

mkdir -p "$OUT"
echo "OUT=$OUT duration=${DURATION_SEC}s FORCE=$FORCE_CHOSEN"

die() { echo "❌ $*" >&2; exit 1; }

# 写一份强制 path=thread 的临时契约
CONTRACT_THREAD="$OUT/let_pc_shared.thread.time.json"
"$PY" - "$CONTRACT_IRQ" "$CONTRACT_THREAD" <<'PY'
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
for pin in (c.get("pins") or {}).values():
    if isinstance(pin, dict) and pin.get("kind") in ("output_commit", "input_snapshot"):
        pin["path"] = "thread"
json.dump(c, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(sys.argv[2], "a", encoding="utf-8").write("\n")
print("wrote", sys.argv[2])
PY

read_params() {
  local mod="$1"
  local base="/sys/module/$mod/parameters"
  [ -d "$base" ] || return 1
  for p in periods let_rw_fires let_rw_irq let_rw_lat_max_ns \
           let_skip_commit let_hold_last deadline_miss late_start \
           release_overflow queue_backlog_max; do
    [ -f "$base/$p" ] && echo -n "$p=$(cat "$base/$p") "
  done
  echo
}

run_kernel_arm() {
  local tag="$1"
  local contract="$2"
  local arm_out="$OUT/arm_$tag"
  mkdir -p "$arm_out"
  echo "==== build+run [$tag] ===="
  TIME_CONTRACT="$contract" \
  OUT_DIR="$arm_out" OUT="$arm_out" \
  bash "$KF/scripts/kf_unify__统一流水线.sh" "$SRC" \
    || die "build $tag failed"

  local ko
  ko="$(find "$arm_out" -name 'kf_task.ko' -o -name '*.ko' | head -n1)"
  [ -n "$ko" ] || die "no .ko for $tag"
  echo "ko=$ko"

  local mod
  for mod in kf_task ecos_fused ecos_sw; do
    lsmod | awk '{print $1}' | grep -qx "$mod" && sudo rmmod "$mod" || true
  done
  mod="$(modinfo -F name "$ko" 2>/dev/null || basename "$ko" .ko)"
  sudo insmod "$ko" "cpu=$CPU" period_us=1000 rt_priority=80 || die "insmod $tag"
  sleep "$DURATION_SEC"
  read_params "$mod" | tee "$OUT/params_${tag}.txt"
  sudo rmmod "$mod" || die "rmmod $tag"
  dmesg | tail -n 80 | grep -E 'ecos-sw:' | tail -n 8 >"$OUT/dmesg_${tag}.txt" || true
}

# B + C（内核）
run_kernel_arm "thread" "$CONTRACT_THREAD"
run_kernel_arm "irq" "$CONTRACT_IRQ"

# A：用户态 LET（可选；失败不阻断对照）
US_STATUS="skipped"
if [ "${SKIP_USERSPACE:-0}" != "1" ]; then
  echo "==== userspace plc_let (best-effort) ===="
  US_OUT="$OUT/arm_userspace"
  mkdir -p "$US_OUT"
  if TIME_CONTRACT="$CONTRACT_IRQ" OUT="$US_OUT" PLATFORM=linux_kernel \
       SKIP_KMOD=1 bash "$KF/scripts/ecos_from_c__从C编译.sh" "$SRC" \
       >"$OUT/userspace_build.log" 2>&1; then
    if [ -x "$US_OUT/let_host/let_app" ] || [ -x "$US_OUT/let_app" ]; then
      APP=$(find "$US_OUT" -type f -name '*let*app*' -executable | head -n1)
      if [ -n "$APP" ]; then
        LET_RUN_LOOPS=$((DURATION_SEC * 1000)) timeout $((DURATION_SEC + 3)) \
          "$APP" >"$OUT/userspace_run.log" 2>&1 || true
        US_STATUS="ran"
      fi
    else
      US_STATUS="no_app"
    fi
  else
    US_STATUS="build_fail"
  fi
fi

"$PY" - "$OUT" "$DURATION_SEC" "$US_STATUS" <<'PY'
import json, os, sys, re
out, dur, us = sys.argv[1], int(sys.argv[2]), sys.argv[3]
arms = {}
for tag in ("thread", "irq"):
    p = os.path.join(out, "params_%s.txt" % tag)
    rec = {"tag": tag, "params_raw": ""}
    if os.path.isfile(p):
        raw = open(p, encoding="utf-8").read().strip()
        rec["params_raw"] = raw
        for m in re.finditer(r"(\w+)=(\-?\d+)", raw):
            rec[m.group(1)] = int(m.group(2))
    arms[tag] = rec
doc = {
    "duration_sec": dur,
    "userspace": us,
    "arms": arms,
    "checks": {
        "irq_path_used": (arms.get("irq") or {}).get("let_rw_irq", 0) > 0,
        "thread_irq_zero": (arms.get("thread") or {}).get("let_rw_irq", 0) == 0,
        "irq_rw_ge_periods": (
            (arms.get("irq") or {}).get("let_rw_fires", 0)
            >= 2 * (arms.get("irq") or {}).get("periods", 0) * 0.9
            if (arms.get("irq") or {}).get("periods") else False
        ),
    },
}
path = os.path.join(out, "summary.json")
json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(path, "a", encoding="utf-8").write("\n")
print(json.dumps(doc["checks"], ensure_ascii=False, indent=2))
ok = doc["checks"]["irq_path_used"] and doc["checks"]["thread_irq_zero"]
sys.exit(0 if ok else 2)
PY

echo "✅ LET 三组对照写完 → $OUT/summary.json"
