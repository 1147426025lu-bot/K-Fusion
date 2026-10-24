#!/usr/bin/env bash
# fusion_plan_selftest__自测.sh — 融合规划器回归（含延迟边反证）
#
# 覆盖：
#   1) 闭环五阶段摘掉 z⁻¹ 后应枚举 16 种合法分段（2^(5-1)）
#   2) DP 最优必须是全融合 G=1
#   3) 不摘延迟边时（用 --verify 非连续组）不可分性仍能挡住跳段
#   4) mmio 阶段在 linux_kernel 上 fail-closed
set -u -o pipefail

PY="${1:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAN="$HERE/../fusion_plan__融合规划.py"
CAPS="$HERE/../../contracts/examples/platform_caps.json"
DAG="$HERE/fusion/five_stage_loop.dag.json"
CHAIN="$HERE/../../analysis/build/ecos_demo/plc_chain.dag.json"
TMP="$(mktemp -d 2>/dev/null || echo /tmp/fusion_plan_selftest.$$)"
mkdir -p "$TMP"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

echo "=== 1. 闭环五阶段：延迟边不得吞掉分段空间 ==="
"$PY" "$PLAN" "$DAG" --platforms "$CAPS" --platform linux_kernel \
  --release-cost-us 21 --enumerate --quiet \
  --exec-us '{"stage_sample":0.5,"stage_fir":0.7,"stage_pid":0.35,"stage_clamp":0.24,"stage_plant":0.24}' \
  -o "$TMP/enum.json"
N=$("$PY" -c "import json; print(json.load(open('$TMP/enum.json'))['meta']['n_candidates'])")
if [ "$N" = 16 ]; then
  ok "枚举 16 种合法分段"
else
  bad "期望 16 种，实际 $N（延迟边可能又进了可达闭包）"
fi
BEST=$("$PY" -c "import json; c=json.load(open('$TMP/enum.json'))['candidates'][0]; print(c['cost']['groups'], c['label'])")
if [ "$BEST" = "1 sample+fir+pid+clamp+plant" ]; then
  ok "DP/枚举最优为全融合 G=1"
else
  bad "最优应为 G=1 全融合，实际「$BEST」"
fi

echo "=== 2. --verify：全融合方案合法 ==="
"$PY" "$PLAN" "$DAG" --platforms "$CAPS" --platform linux_kernel \
  --release-cost-us 21 -o "$TMP/plan.json" --quiet
if "$PY" "$PLAN" "$DAG" --platforms "$CAPS" --platform linux_kernel \
     --verify "$TMP/plan.json" >/dev/null; then
  ok "最优 plan 复检通过"
else
  bad "最优 plan 复检失败"
fi

echo "=== 3. --verify：跳段 {sample, plant} 必须被不可分性拒绝 ==="
"$PY" - <<PY
import json, io
p = json.load(open("$TMP/plan.json"))
p["groups"] = [{
    "id": "bad",
    "members": ["stage_sample", "stage_plant"],
    "effects": [], "contexts": ["thread"], "exec_us": 0,
}]
json.dump(p, open("$TMP/badplan.json","w"), ensure_ascii=False, indent=2)
PY
if "$PY" "$PLAN" "$DAG" --platforms "$CAPS" --platform linux_kernel \
     --verify "$TMP/badplan.json" >/dev/null 2>"$TMP/bad.err"; then
  bad "跳段方案不应通过"
else
  if grep -q "不可分性" "$TMP/bad.err"; then
    ok "跳段被不可分性拒绝"
  else
    bad "跳段被拒但原因不是不可分性"
    sed -n '1,8p' "$TMP/bad.err" | sed 's/^/      /'
  fi
fi

echo "=== 4. plc_chain + mmio：linux_kernel fail-closed / nuttx 可规划 ==="
if [ ! -f "$CHAIN" ]; then
  echo "  （跳过：尚未生成 $CHAIN）"
else
  "$PY" "$PLAN" "$CHAIN" --platforms "$CAPS" --platform linux_kernel \
    --enumerate --quiet -o "$TMP/chain_linux.json" || true
  NL=$("$PY" -c "import json; print(json.load(open('$TMP/chain_linux.json')).get('meta',{}).get('n_candidates',0))" 2>/dev/null || echo 0)
  if [ "$NL" = 0 ]; then
    ok "linux_kernel 对 mmio 链枚举为 0"
  else
    bad "linux_kernel 不应接受 mmio 链，实际 $NL 种"
  fi
  "$PY" "$PLAN" "$CHAIN" --platforms "$CAPS" --platform nuttx \
    --enumerate --quiet -o "$TMP/chain_nuttx.json"
  NN=$("$PY" -c "import json; print(json.load(open('$TMP/chain_nuttx.json'))['meta']['n_candidates'])")
  if [ "$NN" = 4 ]; then
    ok "nuttx 三阶段枚举 4 种（ABC / A|BC / AB|C / A|B|C）"
  else
    bad "nuttx 期望 4 种，实际 $NN"
  fi
fi

echo
echo "通过 $PASS / 失败 $FAIL"
rm -rf "$TMP"
[ "$FAIL" = 0 ]
