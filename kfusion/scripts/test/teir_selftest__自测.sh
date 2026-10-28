#!/usr/bin/env bash
# teir_selftest__自测.sh — Gate 2：TEIR 与跨周期状态
#
# 1) PID 输出本拍无人读 ≠ 可删除（积分是 self_carry）
# 2) 五阶段闭环：plant→sample 是 previous-job
# 3) 无状态流水线：零 persistent、零 previous-job
# 4) 别名未证：policy=keep_deps，不得 may_delete
set -euo pipefail

PY="${PYTHON:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KF="$(cd "$HERE/../.." && pwd)"
TEIR="$KF/ir/temporal_effect_ir.py"
CAPS="$KF/contracts/examples/platform_caps.json"
TMP="${TMPDIR:-/tmp}/teir_selftest.$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

echo "=== 1. PID：未读输出不能当死代码 ==="
"$PY" "$TEIR" \
  --dag "$KF/ir/test/pid_unread_output.dag.json" \
  --summary "$KF/ir/test/pid_unread_output.summary.json" \
  --platforms "$CAPS" \
  -o "$TMP/pid.teir.json"
"$PY" - "$TMP/pid.teir.json" <<'PY' && ok "积分 self_carry ⇒ must_preserve / 不可删" || bad "PID 被当成可删"
import json, sys
t = json.load(open(sys.argv[1]))
n = t["nodes"][0]
assert n["name"] == "stage_pid"
assert n["must_preserve"] and not n["may_delete"], n
assert "@g_integ" in n["persistent"], n
assert "@g_act" in n["unread_this_job"], n
assert any(x.startswith("self_carry:@g_integ") for x in n["why_preserve"]), n["why_preserve"]
assert any(x.startswith("unobserved_write:@g_act") for x in n["why_preserve"]), n["why_preserve"]
print("  persistent", n["persistent"], "unread", n["unread_this_job"])
PY

echo "=== 2. 五阶段闭环：previous-job 边 ==="
FIVE_DAG="$KF/analysis/build/gate1_from_c/task.dag.json"
FIVE_SUM="$KF/analysis/build/gate1_from_c/effect_summary.json"
FIVE_ST="$KF/analysis/build/gate1_from_c/stages.json"
if [ -f "$FIVE_DAG" ]; then
  "$PY" "$TEIR" --dag "$FIVE_DAG" --summary "$FIVE_SUM" --stages "$FIVE_ST" \
    --platforms "$CAPS" -o "$TMP/five.teir.json"
  "$PY" - "$TMP/five.teir.json" <<'PY' && ok "plant→sample = previous-job；pid 保积分" || bad "五阶段 TEIR 不对"
import json, sys
t = json.load(open(sys.argv[1]))
prev = [(e["from"], e["to"]) for e in t["edges"] if e["version"]=="previous-job"]
assert ("stage_plant", "stage_sample") in prev, prev
same = [(e["from"], e["to"]) for e in t["edges"] if e["version"]=="same-job"]
assert ("stage_pid", "stage_clamp") in same, same
pid = next(n for n in t["nodes"] if n["name"]=="stage_pid")
assert "@g_integ" in pid["persistent"]
assert not pid["may_delete"]
assert "linux_kernel" in pid["allowed_domains"]
sample = next(n for n in t["nodes"] if n["name"]=="stage_sample")
assert any(ev["kind"]=="input_commit" and ev["stage"]=="stage_sample" for ev in t["events"])
print("  previous-job", prev)
PY
else
  echo "  （跳过：先跑 ecos_from_c 生成 gate1_from_c）"
fi

echo "=== 3. 无状态流水线 ==="
SO="${PLCEFFECTSUMMARY_SO:-$KF/analysis/build/PLCEffectSummary.so}"
CLANG="${CLANG:-clang-16}"
OPT="${OPT:-opt-16}"
SRC="$KF/examples/ecos/teir_stateless_pipe__无状态流水线.c"
if [ -f "$SO" ]; then
  "$PY" "$KF/frontend/stage_extract/stage_extract.py" "$SRC" -o "$TMP/st.stages.json"
  "$CLANG" -S -emit-llvm -O2 -fno-inline -fno-inline-functions -fno-builtin \
    "$SRC" -o "$TMP/st.ll"
  PLC_EFFECT_SUMMARY_OUT="$TMP/st.summary.json" \
    "$OPT" -load-pass-plugin "$SO" -passes=plc-effect-summary "$TMP/st.ll" -S -o /dev/null
  "$PY" "$KF/scripts/dag_build__任务DAG生成.py" "$TMP/st.summary.json" \
    --stages "$TMP/st.stages.json" -o "$TMP/st.dag.json" >/dev/null
  "$PY" "$TEIR" --dag "$TMP/st.dag.json" --summary "$TMP/st.summary.json" \
    --stages "$TMP/st.stages.json" --platforms "$CAPS" -o "$TMP/st.teir.json"
  "$PY" - "$TMP/st.teir.json" <<'PY' && ok "无状态：0 persistent，0 previous-job" || bad "无状态流水线 TEIR 不对"
import json, sys
t = json.load(open(sys.argv[1]))
assert t["persistent_state"] == [] or all(False for _ in t["persistent_state"]), t["persistent_state"]
prev = [e for e in t["edges"] if e["version"]=="previous-job"]
assert prev == [], prev
assert any(n["name"]=="stage_add" and not n["may_delete"] for n in t["nodes"])
print("  edges", [(e["from"], e["to"], e["version"]) for e in t["edges"]])
PY
else
  echo "  （跳过：无 PLCEffectSummary.so）"
fi

echo "=== 4. 别名不确定：keep_deps ==="
"$PY" "$TEIR" --dag "$KF/ir/test/alias_uncertain.dag.json" \
  --platforms "$CAPS" -o "$TMP/alias.teir.json"
"$PY" - "$TMP/alias.teir.json" <<'PY' && ok "未证别名 ⇒ keep_deps，禁止当无依赖重排" || bad "别名策略不对"
import json, sys
t = json.load(open(sys.argv[1]))
assert t["transform"]["alias_uncertain"] is True
assert t["transform"]["policy"] == "keep_deps", t["transform"]
assert t["transform"]["reorder_forbidden_if_alias"] is True
kinds = [e["kind"] for e in t["edges"]]
assert "candidate" in kinds, t["edges"]
PY

echo "=== 5. MMIO 阶段不得进 linux_kernel ==="
"$PY" "$TEIR" --dag "$KF/ir/test/mmio_sensor.dag.json" \
  --summary "$KF/ir/test/mmio_sensor.summary.json" \
  --platforms "$CAPS" -o "$TMP/mmio.teir.json"
"$PY" - "$TMP/mmio.teir.json" <<'PY' && ok "sensor_prepare 禁 linux_kernel、允 nuttx/用户态" || bad "MMIO 域划分不对"
import json, sys
t = json.load(open(sys.argv[1]))
n = t["nodes"][0]
assert "linux_kernel" not in n["allowed_domains"], n
assert "nuttx" in n["allowed_domains"]
assert "linux_userspace" in n["allowed_domains"]
assert not n["may_delete"]
PY

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" -eq 0 ]
