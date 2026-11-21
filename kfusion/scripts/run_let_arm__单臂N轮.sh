#!/usr/bin/env bash
# run_let_arm__单臂N轮.sh — 跑「已编好的」一个 LET 臂 N 轮，导出逐样本日志
#
# 为什么要这个脚本：四组对照要求同一份宿主、同一批参数、同一段预热截断，
# 只有「臂」不同。把采集逻辑收在一处，避免每个臂各自决定 sleep 多久、截多少。
#
# 用法：
#   KO=/path/kf_task.ko TAG=D OUT=/tmp/run_d REPS=5 DURATION=60 \
#     bash run_let_arm__单臂N轮.sh
#
# 环境：KF_CPU(3) KF_PERIOD_US(1000) KF_PRIO(80) KF_HRD(1) KF_WARMUP(10) KF_CAP(400000)
set -uo pipefail

KO="${KO:?需要 KO=/path/to/kf_task.ko}"
TAG="${TAG:?需要 TAG（臂名，用于产物命名）}"
OUT="${OUT:?需要 OUT（产物目录）}"
REPS="${REPS:-5}"
DURATION="${DURATION:-60}"
CPU="${KF_CPU:-3}"
PERIOD="${KF_PERIOD_US:-1000}"
PRIO="${KF_PRIO:-80}"
HRD="${KF_HRD:-1}"
WARMUP="${KF_WARMUP:-10}"
CAP="${KF_CAP:-400000}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DUMP="$ROOT/kfusion/scripts/let_log_dump__导出读写日志.py"
PY="${PYTHON:-python3}"
P=/sys/module/kf_task/parameters

mkdir -p "$OUT"
echo "══ 臂 $TAG：$REPS × ${DURATION}s（warmup 截 ${WARMUP}s，cpu=$CPU hrtimer_hard=$HRD）══"

for rep in $(seq 1 "$REPS"); do
  dest="$OUT/${KF_REP_TAG:-rep$rep}"
  mkdir -p "$dest"
  sudo rmmod kf_task 2>/dev/null

  if ! sudo insmod "$KO" cpu="$CPU" period_us="$PERIOD" rt_priority="$PRIO" \
       let_log_cap="$CAP" hrtimer_hard="$HRD"; then
    echo "❌ rep$rep insmod 失败" >&2
    exit 1
  fi

  sleep "$DURATION"

  {
    for p in periods deadline_miss missing_groups incomplete_cycle unpaired_finish \
             release_overflow queue_backlog_max late_start \
             let_rd_ok let_wr_ok let_rw_fires let_rw_irq let_ctx_hardirq \
             let_ctx_softirq let_ctx_task let_skip_commit let_hold_last \
             let_rd_jit_max_ns let_wr_jit_max_ns let_op_dur_max_ns let_log_drops \
             let_ready_stale let_ready_not_ready let_ready_consumed \
             let_ready_too_old let_ready_group_mismatch let_ready_age_max_ns \
             test_prod_skipped; do
      [ -f "$P/$p" ] && echo "$p=$(cat "$P/$p")"
    done
  } >"$dest/params_end.txt"

  sudo dd if=/dev/kf_let_log of="$dest/raw.bin" bs=1M status=none || \
    sudo cat /dev/kf_let_log >"$dest/raw.bin"
  sudo rmmod kf_task
  sudo chown "$(id -u):$(id -g)" "$dest/raw.bin" 2>/dev/null

  "$PY" "$DUMP" --bin "$dest/raw.bin" -o "$dest" \
      --tag "${TAG}_r${rep}" --warmup-sec "$WARMUP" >"$dest/dump.log" 2>&1 || {
    echo "❌ rep$rep 导出失败（见 $dest/dump.log）" >&2
    exit 1
  }
  echo "  rep$rep done"
done

# 汇总：只读 *.meta.json（dump_summary.json 可能混入文本）
"$PY" - "$OUT" "$TAG" <<'PYEOF'
import glob, json, os, sys

out, tag = sys.argv[1], sys.argv[2]
rows = []
files = glob.glob(os.path.join(out, "*", "*.meta.json"))
files = [f for f in files if os.path.basename(os.path.dirname(f)) != "figs"]
for f in sorted(files,
                key=lambda p: int("".join(c for c in p.split(os.sep)[-2] if c.isdigit()) or 0)):
    m = json.load(open(f, encoding="utf-8"))
    s = m["summary"]
    rep = int("".join(c for c in f.split(os.sep)[-2] if c.isdigit()) or 0)
    rows.append({
        "arm": tag, "rep": rep, "n": s["n"], "warmup_sec": s.get("warmup_sec"),
        "n_trimmed": s.get("n_trimmed"),
        "read": s["dist_read"], "write": s["dist_write"],
        "skip": s["n_skip"], "hold": s["n_hold"], "err": s["n_err"],
        "ver_mismatch": s["version_mismatch_cycles"],
        "ver_nonmono": s["version_nonmono_writes"],
        "hdr": {k: m["hdr"].get(k) for k in ("arm_name", "arm_path", "drops",
                                            "period_us", "n_samples")},
    })
os.makedirs(os.path.join(out, "figs"), exist_ok=True)
dst = os.path.join(out, "figs", "table_%s.json" % tag)
json.dump(rows, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("wrote", dst, "rows =", len(rows))
for r in rows:
    print("  rep%d n=%d read p50=%.2f p99=%.2f max=%.2f | write p50=%.2f p99=%.2f max=%.2f | skip=%d" % (
        r["rep"], r["n"], r["read"]["p50_ns"] / 1000, r["read"]["p99_ns"] / 1000,
        r["read"]["max_ns"] / 1000, r["write"]["p50_ns"] / 1000,
        r["write"]["p99_ns"] / 1000, r["write"]["max_ns"] / 1000, r["skip"]))
PYEOF
