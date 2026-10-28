#!/usr/bin/env bash
# let_decide_selftest__自测.sh — Gate 5：按需 LET
set -euo pipefail

PY="${PYTHON:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KF="$(cd "$HERE/../.." && pwd)"
LET="$KF/scripts/ecos/let_decide.py"
TIME="$KF/contracts/examples/five_stage_let.time.json"
TEIR="$KF/analysis/build/gate1_from_c/task.teir.json"
TMP="${TMPDIR:-/tmp}/let_selftest.$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

[ -f "$TEIR" ] || { echo "缺少 $TEIR"; exit 2; }

echo "=== 1. 全融合无 LET 必须因提前提交被拒；选择性融合合法 ==="
"$PY" "$LET" --teir "$TEIR" --time "$TIME" --c-us 21 --let-event-us 21 \
  -o "$TMP/low.json"
"$PY" - "$TMP/low.json" <<'PY' && ok "全融合非法，selective/LET 合法" || bad "约束判断不对"
import json, sys
d = json.load(open(sys.argv[1]))
by = {c["id"]: c for c in d["candidates"]}
assert by["full_fuse"]["legal"] is False
assert any(x["code"]=="E_PIN_EARLY_COMMIT" for x in by["full_fuse"]["diagnostics"])
assert by["selective_pins"]["legal"] is True
assert by["full_fuse_let"]["legal"] is True
assert by["stretch_period"]["legal"] is False
assert any(x["code"]=="E_PERIOD_STRETCH" for x in by["stretch_period"]["diagnostics"])
print("  full_fuse", by["full_fuse"]["why"])
print("  let events", by["full_fuse_let"]["cost"]["let_events"])
print("  selective G", by["selective_pins"]["cost"]["groups"])
PY

echo "=== 2. LET 事件便宜时可以采用全融合+LET ==="
CH=$("$PY" -c "import json; print(json.load(open('$TMP/low.json'))['chosen'])")
if [ "$CH" = "full_fuse_let" ]; then
  ok "let_event=21 → 选 full_fuse_let（Cost 低于 G=3 选择性融合）"
else
  bad "期望 full_fuse_let，实际 $CH"
fi

echo "=== 3. LET 更贵时主动不用 ==="
"$PY" "$LET" --teir "$TEIR" --time "$TIME" --c-us 21 --let-event-us 200 \
  -o "$TMP/high.json"
"$PY" - "$TMP/high.json" <<'PY' && ok "LET 税 200µs → 选 selective_pins，不用 LET" || bad "贵 LET 仍被选"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["chosen"] == "selective_pins", d["chosen"]
ch = d["chosen_record"]
assert ch["cost"]["uses_let"] is False
by = {c["id"]: c for c in d["candidates"]}
assert by["full_fuse_let"]["legal"]
assert by["full_fuse_let"]["cost"]["total_us"] > ch["cost"]["total_us"]
print("  selective", ch["cost"]["total_us"], "let", by["full_fuse_let"]["cost"]["total_us"])
PY

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" -eq 0 ]
