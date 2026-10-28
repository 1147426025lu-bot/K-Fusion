#!/usr/bin/env bash
# plan_verify_selftest__自测.sh — Gate 3：独立验证器 + 变异必须被抓住
set -euo pipefail

PY="${PYTHON:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KF="$(cd "$HERE/../.." && pwd)"
V="$KF/verify/plan_verify.py"
TEIR="$KF/analysis/build/gate1_from_c/task.teir.json"
PLAN="$KF/analysis/build/gate1_from_c/plan.json"
TMP="${TMPDIR:-/tmp}/plan_verify_selftest.$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

echo "=== 0. 验证器不得 import 规划器 ==="
if grep -nE '^\s*(import|from)\s+.*fusion_plan' "$V"; then
  bad "plan_verify.py 引用了 fusion_plan"
else
  ok "源码不 import fusion_plan"
fi

if [ ! -f "$TEIR" ] || [ ! -f "$PLAN" ]; then
  echo "缺少 $TEIR / $PLAN ，先跑 ecos_from_c"
  exit 2
fi

echo "=== 1. 规划器产出的合法方案必须通过 ==="
if "$PY" "$V" --teir "$TEIR" --plan "$PLAN" -o "$TMP/ok.json"; then
  ok "合法全融合方案 PASS"
else
  bad "合法方案被拒"
  cat "$TMP/ok.json" 2>/dev/null || true
fi

echo "=== 2. 变异：丢掉 PID（漏状态）==="
"$PY" - "$V" "$PLAN" "$TMP/drop.json" <<'PY'
import json, sys, os
sys.path.insert(0, os.path.dirname(sys.argv[1]))
import plan_verify as v
p = json.load(open(sys.argv[2]))
json.dump(v.mutate_drop(p, "stage_pid"), open(sys.argv[3], "w"), indent=2)
PY
if "$PY" "$V" --teir "$TEIR" --plan "$TMP/drop.json" -o "$TMP/drop.rep.json"; then
  bad "丢掉 PID 竟然通过"
else
  if grep -q E_DROP_STATE "$TMP/drop.rep.json"; then
    ok "丢掉 PID → E_DROP_STATE"
  else
    bad "拒绝了但不是 E_DROP_STATE"
    cat "$TMP/drop.rep.json"
  fi
fi

echo "=== 3. 变异：跨拍年龄改成 k-2 ==="
"$PY" - "$V" "$PLAN" "$TMP/age.json" <<'PY'
import json, sys, os
sys.path.insert(0, os.path.dirname(sys.argv[1]))
import plan_verify as v
p = json.load(open(sys.argv[2]))
json.dump(v.mutate_age(p, "stage_plant", "stage_sample", 2),
          open(sys.argv[3], "w"), indent=2)
PY
if "$PY" "$V" --teir "$TEIR" --plan "$TMP/age.json" -o "$TMP/age.rep.json"; then
  bad "k-2 竟然通过"
else
  if grep -q E_AGE "$TMP/age.rep.json"; then
    ok "年龄 k-2 → E_AGE"
  else
    bad "拒绝了但不是 E_AGE"
    cat "$TMP/age.rep.json"
  fi
fi

echo "=== 4. 变异：plant 提前到 sample 之前（本拍先写后读）==="
"$PY" - "$V" "$PLAN" "$TMP/swap.json" <<'PY'
import json, sys, os
sys.path.insert(0, os.path.dirname(sys.argv[1]))
import plan_verify as v
p = json.load(open(sys.argv[2]))
json.dump(v.mutate_swap(p, "stage_sample", "stage_plant"),
          open(sys.argv[3], "w"), indent=2)
PY
if "$PY" "$V" --teir "$TEIR" --plan "$TMP/swap.json" -o "$TMP/swap.rep.json"; then
  bad "先写后读竟然通过"
else
  if grep -q E_PREV_JOB_BECAME_SAME "$TMP/swap.rep.json"; then
    ok "plant 提前 → E_PREV_JOB_BECAME_SAME"
  else
    bad "拒绝了但不是 E_PREV_JOB_BECAME_SAME"
    cat "$TMP/swap.rep.json"
  fi
fi

echo "=== 5. 变异：MMIO 阶段塞进 linux_kernel ==="
"$PY" "$KF/ir/temporal_effect_ir.py" \
  --dag "$KF/ir/test/mmio_sensor.dag.json" \
  --summary "$KF/ir/test/mmio_sensor.summary.json" \
  --platforms "$KF/contracts/examples/platform_caps.json" \
  -o "$TMP/mmio.teir.json" >/dev/null
cat > "$TMP/mmio.plan.json" <<'JSON'
{"version":2,"meta":{"platform":"linux_kernel"},"groups":[{"id":"g0","members":["sensor_prepare"]}]}
JSON
if "$PY" "$V" --teir "$TMP/mmio.teir.json" --plan "$TMP/mmio.plan.json" -o "$TMP/mmio.rep.json"; then
  bad "MMIO 进内核竟然通过"
else
  if grep -q E_DOMAIN "$TMP/mmio.rep.json"; then
    ok "MMIO→linux_kernel → E_DOMAIN"
  else
    bad "拒绝了但不是 E_DOMAIN"
    cat "$TMP/mmio.rep.json"
  fi
fi

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" -eq 0 ]
