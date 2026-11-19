#!/usr/bin/env bash
# run_let_userspace__A组用户态实验.sh — A 组：用户态线程执行 LET 读写
#
# 与 B/C/D 组同周期(1000us)、同相位(计算0/写400/读500/收尾700)、同计算体、
# 同读写桩语义、同 36 字节日志格式。唯一变量：读写桩在用户态 SCHED_FIFO 线程
# 里执行，因此抖动含完整用户态唤醒延迟。
#
# 用法（板端）：
#   REPS=5 DURATION_SEC=60 bash kfusion/scripts/run_let_userspace__A组用户态实验.sh
#   OUT=/home/pi/kfusion_results/let_A bash ...          # 指定输出目录
#   CPU=3 PRIO=80 bash ...                              # 绑核/优先级
#   NO_RT=1 bash ...                                    # 退化为 CFS（对照用）
#
# 产物：每轮 raw.bin + samples.csv + meta.json，以及跨轮 summary.json
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KF="$ROOT/kfusion"
SRC="$KF/src/kf_let_userspace__用户态LET读写.c"
DUMP="$KF/scripts/let_log_dump__导出读写日志.py"

DURATION_SEC="${DURATION_SEC:-60}"
REPS="${REPS:-5}"
PERIOD_US="${PERIOD_US:-1000}"
PHASE_WR_US="${PHASE_WR_US:-400}"
PHASE_RD_US="${PHASE_RD_US:-500}"
PHASE_G1_US="${PHASE_G1_US:-700}"
CPU="${KF_CPU:-3}"
PRIO="${PRIO:-80}"
NO_RT="${NO_RT:-0}"
WARMUP_SEC="${WARMUP_SEC:-10}"
OUT="${OUT:-/home/pi/kfusion_results/let_userspace_${REPS}x${DURATION_SEC}s_$(date +%Y%m%d_%H%M%S)}"
BIN="${BIN:-/tmp/kf_let_userspace}"
PY="${PYTHON:-python3}"

CYCLES=$((DURATION_SEC * 1000000 / PERIOD_US))

mkdir -p "$OUT"
echo "OUT=$OUT REPS=$REPS DUR=${DURATION_SEC}s cycles=$CYCLES cpu=$CPU prio=$PRIO no_rt=$NO_RT"

# ---- 编译（板端 gcc；缺 gcc 直接失败，不静默降级）----
if [ ! -x "$BIN" ] || [ "$SRC" -nt "$BIN" ]; then
  echo "==== build A arm ===="
  gcc -O2 -Wall -Wextra -o "$BIN" "$SRC" || { echo "❌ gcc 编译失败" >&2; exit 1; }
fi
echo "bin=$BIN"

RUNTIME_ARGS=(
  --period-us "$PERIOD_US" --cycles "$CYCLES"
  --phase-write-us "$PHASE_WR_US" --phase-read-us "$PHASE_RD_US"
  --phase-g1-us "$PHASE_G1_US" --cpu "$CPU" --prio "$PRIO"
)
[ "$NO_RT" = "1" ] && RUNTIME_ARGS+=(--no-rt)

# 需要 SCHED_FIFO + mlockall 时用 sudo；此时 HOME 等环境保留
run_arm() {
  local dest="$1"
  if [ "$(id -u)" -eq 0 ]; then
    "$BIN" "${RUNTIME_ARGS[@]}" --out "$dest/raw.bin"
  else
    sudo "$BIN" "${RUNTIME_ARGS[@]}" --out "$dest/raw.bin"
  fi
  # sudo 写出的文件属主是 root，交给当前用户以便后续读取
  sudo chown "$(id -u):$(id -g)" "$dest/raw.bin" 2>/dev/null || true
}

for rep in $(seq 1 "$REPS"); do
  dest="$OUT/rep$rep"
  mkdir -p "$dest"
  echo "==== A 组 rep$rep (${DURATION_SEC}s) ===="
  run_arm "$dest" | tee "$dest/run.json"
  "$PY" "$DUMP" --bin "$dest/raw.bin" -o "$dest" --tag "userspace_r${rep}" \
    --warmup-sec "$WARMUP_SEC" \
    >"$dest/dump.log" 2>&1 || { echo "❌ 导出失败 rep$rep（详见 $dest/dump.log）" >&2; exit 1; }
  "$PY" -c "
import json,sys
m=json.load(open('$dest/userspace_r${rep}.meta.json',encoding='utf-8'))
s=m['summary']
print('  read  p50=%s p99=%s max=%s ns' % (s['dist_read']['p50_ns'], s['dist_read']['p99_ns'], s['dist_read']['max_ns']))
print('  write p50=%s p99=%s max=%s ns' % (s['dist_write']['p50_ns'], s['dist_write']['p99_ns'], s['dist_write']['max_ns']))
"
done

# ---- 跨轮汇总：报中位与极差，不报跨轮均值 ----
"$PY" - "$OUT" "$REPS" "$DURATION_SEC" "$PERIOD_US" "$PHASE_WR_US" "$PHASE_RD_US" "$CPU" "$PRIO" "$NO_RT" <<'PY' | tee "$OUT/summary.txt"
import json, os, statistics as st, sys
out, reps, dur, per, pwr, prd, cpu, prio, no_rt = sys.argv[1:10]
reps = int(reps)

def load(rep):
    p = os.path.join(out, "rep%d" % rep, "userspace_r%d.meta.json" % rep)
    return json.load(open(p, encoding="utf-8"))

per_rep = []
for r in range(1, reps + 1):
    m = load(r)
    s = m["summary"]
    per_rep.append({
        "rep": r,
        "hdr": m["hdr"],
        "read": s["dist_read"],
        "write": s["dist_write"],
        "n_skip": s["n_skip"],
        "n_err": s["n_err"],
        "ctx": s["ctx_counts"],
        "ver_mismatch": s["version_mismatch_cycles"],
        "ver_nonmono": s["version_nonmono_writes"],
        "warmup_sec": s.get("warmup_sec", 0),
        "n_trimmed": s.get("n_trimmed", 0),
    })

def across(op, key):
    vals = [x[op][key] for x in per_rep if x[op][key] is not None]
    if not vals:
        return None
    return {
        "median": int(st.median(vals)),
        "min": min(vals),
        "max": max(vals),
        "spread": max(vals) - min(vals),
        "values": vals,
    }

doc = {
    "arm": "A_userspace",
    "config": {
        "reps": reps, "duration_sec": int(dur), "period_us": int(per),
        "phase_write_us": int(pwr), "phase_read_us": int(prd),
        "cpu": int(cpu), "prio": int(prio), "no_rt": no_rt == "1",
        "warmup_sec": per_rep[0]["warmup_sec"] if per_rep else 0,
    },
    "per_rep": per_rep,
    "across_reps": {
        "read": {k: across("read", k) for k in
                 ("p50_ns", "p90_ns", "p99_ns", "p999_ns", "max_ns")},
        "write": {k: across("write", k) for k in
                  ("p50_ns", "p90_ns", "p99_ns", "p999_ns", "max_ns")},
    },
}
json.dump(doc, open(os.path.join(out, "summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
open(os.path.join(out, "summary.json"), "a", encoding="utf-8").write("\n")

print()
print("A 组（用户态）跨 %d 轮，单位 us，报中位[极小-极大]" % reps)
for op in ("read", "write"):
    row = []
    for k in ("p50_ns", "p99_ns", "max_ns"):
        a = doc["across_reps"][op][k]
        row.append("%s=%.2f[%.2f-%.2f]" % (k[:-3], a["median"]/1000,
                                           a["min"]/1000, a["max"]/1000))
    print("  %-5s %s" % (op, "  ".join(row)))
skips = [x["n_skip"] for x in per_rep]
errs = [x["n_err"] for x in per_rep]
print("  skip=%s err=%s ver_mismatch=%s" %
      (skips, errs, [x["ver_mismatch"] for x in per_rep]))
PY

echo "✅ A 组完成 → $OUT/summary.json"
