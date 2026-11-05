#!/usr/bin/env bash
# 实验一：功能和时间语义正确性短测（1～5 分钟）
# 检查：上下文、版本单调/同周期一致、skip=0、drops=0、读/写次数≈periods
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KF="$ROOT/kfusion"
OUT="${OUT:-/home/pi/kfusion_results/let_correctness_$(date +%Y%m%d_%H%M%S)}"
DURATION_SEC="${DURATION_SEC:-120}"
REPS=1
MODE=smoke
export KF_LET_LOG_CAP="${KF_LET_LOG_CAP:-500000}"
mkdir -p "$OUT"
echo "正确性短测 OUT=$OUT DUR=${DURATION_SEC}s"
MODE=smoke DURATION_SEC="$DURATION_SEC" REPS=1 OUT="$OUT" \
  bash "$KF/scripts/run_let_jitter_exp__读写抖动实验.sh"

PY="${PYTHON:-python3}"
"$PY" - "$OUT" <<'PY'
import json, os, sys
root = sys.argv[1]
ok = True
print("==== 正确性门禁 ====")
metas = []
for dp, _, fs in os.walk(root):
    for f in fs:
        if f.endswith(".meta.json"):
            metas.append(os.path.join(dp, f))
if not metas:
    print("无 meta.json"); sys.exit(2)
for mp in sorted(metas):
    m = json.load(open(mp, encoding="utf-8"))
    s, h = m.get("summary") or {}, m.get("hdr") or {}
    arm = "irq" if "/irq/" in mp else ("thread" if "/thread/" in mp else "?")
    ctx = s.get("ctx_counts") or {}
    only_hard = bool(ctx) and all("hardirq" in k for k in ctx)
    only_task = set(ctx.keys()) == {"task"}
    checks = {
        "skip0": s.get("n_skip", 0) == 0,
        "err0": s.get("n_err", 0) == 0,
        "drops0": (h.get("drops") or 0) == 0,
        "ver_mismatch0": s.get("version_mismatch_cycles", 0) == 0,
        "ver_mono": s.get("version_nonmono_writes", 0) == 0,
        "rw_balanced": abs((s.get("n_read_ok") or 0) - (s.get("n_write_ok") or 0)) <= 2,
        "ctx_ok": (arm == "irq" and only_hard) or (arm == "thread" and only_task),
    }
    passed = all(checks.values())
    ok = ok and passed
    print(arm, "PASS" if passed else "FAIL", checks,
          "rd_jit_us=%.2f" % ((s.get("max_read_jitter_ns") or 0) / 1000.0),
          "wr_jit_us=%.2f" % ((s.get("max_write_jitter_ns") or 0) / 1000.0),
          "ctx", ctx)
json.dump({"ok": ok}, open(os.path.join(root, "correctness_gate.json"), "w"),
          indent=2)
print("GATE", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 2)
PY
