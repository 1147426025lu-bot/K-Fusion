#!/usr/bin/env bash
# candidate_abcd_selftest__自测.sh — Gate 4：同一 Linux 上 A/B/C/D
set -euo pipefail

PY="${PYTHON:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KF="$(cd "$HERE/../.." && pwd)"
GEN="$KF/scripts/ecos/candidate_generate.py"
CAPS="$KF/contracts/examples/platform_caps.json"
TEIR="$KF/ir/temporal_effect_ir.py"
TMP="${TMPDIR:-/tmp}/abcd_selftest.$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

FIVE="$KF/analysis/build/gate1_from_c/task.teir.json"
PIPE_SRC="$KF/examples/ecos/teir_stateless_pipe__无状态流水线.c"

echo "=== 1. 五阶段：内核无附加税时 D（内核+融合）最便宜；有税则选用户态 ==="
if [ ! -f "$FIVE" ]; then
  echo "缺少 $FIVE"; exit 2
fi
"$PY" "$GEN" --teir "$FIVE" --c-user-us 21 --c-kernel-us 10 --kernel-extra-us 0 \
  -o "$TMP/five_kern.json"
CH=$("$PY" -c "import json; print(json.load(open('$TMP/five_kern.json'))['chosen'])")
N=$("$PY" -c "import json; print(json.load(open('$TMP/five_kern.json'))['n_legal'])")
if [ "$CH" = "D" ] && [ "$N" = 4 ]; then
  ok "c_kernel 更低 → 选 D（4 个合法）"
else
  bad "期望 chosen=D n_legal=4，实际 $CH / $N"
fi

"$PY" "$GEN" --teir "$FIVE" --c-user-us 21 --c-kernel-us 21 --kernel-extra-us 80 \
  -o "$TMP/five_user.json"
CH=$("$PY" -c "import json; print(json.load(open('$TMP/five_user.json'))['chosen'])")
WHY=$("$PY" -c "import json; print(json.load(open('$TMP/five_user.json'))['chosen_record']['why'])")
if [ "$CH" = "B" ]; then
  ok "内核附加税 80µs → 选 B（不内核化更优）"
else
  bad "反例失败：期望 B，实际 $CH ($WHY)"
fi

echo "=== 2. 无状态流水线：能生成多个合法候选 ==="
if [ -f "$KF/analysis/build/PLCEffectSummary.so" ]; then
  OUT="$TMP/pipe"
  mkdir -p "$OUT"
  "$PY" "$KF/frontend/stage_extract/stage_extract.py" "$PIPE_SRC" -o "$OUT/stages.json"
  clang-16 -S -emit-llvm -O2 -fno-inline -fno-inline-functions -fno-builtin \
    "$PIPE_SRC" -o "$OUT/t.ll"
  PLC_EFFECT_SUMMARY_OUT="$OUT/sum.json" \
    opt-16 -load-pass-plugin "$KF/analysis/build/PLCEffectSummary.so" \
    -passes=plc-effect-summary "$OUT/t.ll" -S -o /dev/null
  "$PY" "$KF/scripts/dag_build__任务DAG生成.py" "$OUT/sum.json" \
    --stages "$OUT/stages.json" -o "$OUT/dag.json" >/dev/null
  "$PY" "$TEIR" --dag "$OUT/dag.json" --summary "$OUT/sum.json" \
    --stages "$OUT/stages.json" --platforms "$CAPS" -o "$OUT/teir.json" >/dev/null
  "$PY" "$GEN" --teir "$OUT/teir.json" --c-user-us 21 --c-kernel-us 10 \
    -o "$OUT/cand.json"
  N=$("$PY" -c "import json; print(json.load(open('$OUT/cand.json'))['n_legal'])")
  CH=$("$PY" -c "import json; print(json.load(open('$OUT/cand.json'))['chosen'])")
  if [ "$N" -ge 2 ]; then
    ok "无状态流水线 n_legal=$N chosen=$CH"
  else
    bad "无状态流水线合法候选太少: $N"
  fi
else
  echo "  （跳过流水线：无 EffectSummary.so）"
fi

echo "=== 3. MMIO：C/D 必须拒绝，选择留在用户态 ==="
"$PY" "$TEIR" --dag "$KF/ir/test/mmio_sensor.dag.json" \
  --summary "$KF/ir/test/mmio_sensor.summary.json" \
  --platforms "$CAPS" -o "$TMP/mmio.teir.json" >/dev/null
"$PY" "$GEN" --teir "$TMP/mmio.teir.json" -o "$TMP/mmio.cand.json"
"$PY" - "$TMP/mmio.cand.json" <<'PY' && ok "MMIO：C/D 非法，chosen 在 A/B" || bad "MMIO 选型不对"
import json, sys
d = json.load(open(sys.argv[1]))
by = {c["id"]: c for c in d["candidates"]}
assert by["C"]["legal"] is False and by["D"]["legal"] is False, by
assert d["chosen"] in ("A", "B")
assert d["n_legal"] >= 1
print("  chosen", d["chosen"], "n_legal", d["n_legal"])
PY

echo "=== 4. 部分内核化：MMIO 留用户态、计算可进内核 → E 合法，C/D 非法 ==="
"$PY" "$GEN" --teir "$KF/ir/test/partial_mmio.teir.json" \
  --c-user-us 21 --c-kernel-us 10 --kernel-extra-us 0 --boundary-us 50 \
  -o "$TMP/partial.json"
"$PY" - "$TMP/partial.json" <<'PY' && ok "E/F 合法且 C/D 非法；不强制选内核" || bad "部分内核化候选不对"
import json, sys
d = json.load(open(sys.argv[1]))
by = {c["id"]: c for c in d["candidates"]}
assert "E" in by and "F" in by, sorted(by)
assert by["C"]["legal"] is False and by["D"]["legal"] is False
assert by["E"]["legal"] is True and by["F"]["legal"] is True
assert by["E"]["domain"] == "mixed"
assert by["E"]["plan"]["domains"]["sensor_prepare"] == "linux_userspace"
assert by["E"]["plan"]["domains"]["stage_pid"] == "linux_kernel"
assert d["chosen"] in ("A", "B", "E", "F")
# 成本模型里 G_E≥2，B 若合法通常更便宜——这正是「不强制内核化」
print("  chosen", d["chosen"], "E.total", by["E"]["cost"]["total_us"],
      "B.legal", by["B"]["legal"])
PY

echo "=== 5. 入选方案必须改变生成物（用户态 vs 内核宿主） ==="
PIPE="$KF/scripts/ecos_from_c__从C编译.sh"
SRC="$KF/examples/ecos/five_stage_loop__闭环五阶段示例.c"
SO="$KF/analysis/build/PLCEffectSummary.so"
if [ ! -f "$SO" ]; then
  echo "  （跳过：无 EffectSummary.so）"
else
  mkdir -p "$TMP/user" "$TMP/kern"
  "$PY" - "$TMP/stage_cost.json" <<'PY'
import json, sys
names = ["stage_sample", "stage_fir", "stage_pid", "stage_clamp", "stage_plant"]
one = {n: 1.0 for n in names}
json.dump({"linux_userspace": one, "linux_kernel": one}, open(sys.argv[1], "w"))
PY
  SKIP_HOST=1 SKIP_KMOD=1 C_USER_US=21 C_KERNEL_US=21 KERNEL_EXTRA_US=80 \
    OUT_DIR="$TMP/user" bash "$PIPE" "$SRC" >/dev/null
  SKIP_HOST=1 SKIP_KMOD=1 C_USER_US=21 C_KERNEL_US=10 KERNEL_EXTRA_US=0 \
    STAGE_COST="$TMP/stage_cost.json" \
    OUT_DIR="$TMP/kern" bash "$PIPE" "$SRC" >/dev/null
  "$PY" - "$TMP/user/deploy.json" "$TMP/kern/deploy.json" <<'PY' && ok "成本翻转 → 选型与产物一起变" || bad "选型未驱动生成"
import json, os, sys
u = json.load(open(sys.argv[1]))
k = json.load(open(sys.argv[2]))
ud = os.path.dirname(sys.argv[1])
kd = os.path.dirname(sys.argv[2])
assert u["domain"] == "linux_userspace", u
assert k["domain"] == "linux_kernel", k
assert u["chosen"] in ("A", "B"), u
assert k["chosen"] in ("C", "D"), k
assert os.path.isfile(os.path.join(ud, "driver.c"))
assert not os.path.isfile(os.path.join(ud, "kmod_hrtimer", "Kbuild")), "用户态不该有内核宿主"
assert os.path.isfile(os.path.join(kd, "kmod_hrtimer", "Kbuild")), "内核入选必须有 Kbuild"
assert os.path.isfile(os.path.join(kd, "kmod_hrtimer", "ecos_fused_linux.c"))
print("  user", u["chosen"], "kern", k["chosen"])
PY
fi

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" -eq 0 ]
