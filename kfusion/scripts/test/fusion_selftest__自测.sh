#!/usr/bin/env bash
# fusion_selftest__自测.sh — 融合规划器的回归测试
#
# 用法：
#   bash kfusion/scripts/test/fusion_selftest__自测.sh [PYTHON]
#
# 重点不是"能不能算出方案"，而是**能不能拒绝非法方案并说清原因**：
#   · 平台不允许 → 拒绝（mmio 进内核域）
#   · 组跨越了必须插在中间的节点 → 拒绝（非连续组）
#   · 覆盖不完整 / 有环 → 拒绝
# 这三类拒绝如果静默通过，产物会在板上跑出错误结果，而图上什么都看不出来。

set -u -o pipefail

PY="${1:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAN="$HERE/../fusion_plan__融合规划.py"
FIX="$HERE/fusion"
CAPS="$HERE/../../contracts/examples/platform_caps.json"
PLC="$HERE/../../docs/figures/dag/plc_chain.dag.json"
TMP="$(mktemp -d 2>/dev/null || echo /tmp/fusion_selftest.$$)"
mkdir -p "$TMP"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

expect_grep() {
    local desc="$1" text="$2"; shift 2
    local k
    for k in "$@"; do
        if ! printf '%s' "$text" | grep -qF -- "$k"; then
            bad "$desc（缺少「$k」）"
            return
        fi
    done
    ok "$desc"
}

expect_rc() {
    local desc="$1" want="$2"; shift 2
    "$@" >"$TMP/out.txt" 2>&1
    local got=$?
    if [ "$got" = "$want" ]; then
        ok "$desc（rc=$got）"
    else
        bad "$desc（期望 rc=$want，实际 rc=$got）"
        sed -n '1,10p' "$TMP/out.txt" | sed 's/^/      /'
    fi
}

echo "=== 1. 四阶段链：应合成 1 组，且 R̂ = c + ΣE ==="
OUT="$("$PY" "$PLAN" "$FIX/four_stage.dag.json" --platforms "$CAPS" \
      --platform linux_kernel --release-cost-us 20 \
      --exec-us '{"stage_sample":0.6,"stage_fir":0.67,"stage_pid":0.28,"stage_clamp":0.28}' 2>&1)"
expect_grep "方案 1 组" "$OUT" "方案：1 组"
expect_grep "R̂ = 20 + 1.83 ≈ 21.8" "$OUT" "R̂ ≈ 21.8"
expect_grep "组内上下文取交集" "$OUT" "ctx=softirq,thread"

echo "=== 2. 域不允许：mmio 阶段不能进内核域 ==="
expect_rc "linux_kernel 拒绝 plc_chain（sensor_prepare 碰 MMIO）" 3 \
    "$PY" "$PLAN" "$PLC" --platforms "$CAPS" --platform linux_kernel
expect_grep "指认到具体阶段与原因" "$(cat "$TMP/out.txt")" \
    "sensor_prepare" "allow_mmio=false" "(a)"
expect_rc "同一张图在 nuttx 通过（allow_mmio=true）" 0 \
    "$PY" "$PLAN" "$PLC" --platforms "$CAPS" --platform nuttx

echo "=== 3. 复检：合法方案 ==="
expect_rc "plan.json 复检通过" 0 \
    "$PY" "$PLAN" "$FIX/four_stage.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --verify "$HERE/../../docs/figures/fusion/four_stage.plan.json"

echo "=== 4. 复检：非连续组必须被不可分性挡住 ==="
expect_rc "非连续组被拒" 3 \
    "$PY" "$PLAN" "$FIX/four_stage.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --verify "$FIX/noncontig.plan.json"
expect_grep "指出是谁挡在中间" "$(cat "$TMP/out.txt")" \
    "不可分性" "stage_fir 必须位于"

echo "=== 5. 复检：覆盖不完整 ==="
expect_rc "漏掉阶段被拒" 3 \
    "$PY" "$PLAN" "$FIX/four_stage.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --verify "$FIX/missing.plan.json"
expect_grep "报出漏了谁" "$(cat "$TMP/out.txt")" "覆盖不完整"

echo "=== 6. 有环 DAG：规划器必须拒绝，而不是硬算 ==="
expect_rc "有环 → rc=2" 2 \
    "$PY" "$PLAN" "$FIX/cyclic.dag.json" --platforms "$CAPS" --platform nuttx
expect_grep "说明原因" "$(cat "$TMP/out.txt")" "有环"

echo "=== 7. 缺平台能力：必须拒绝（不能默认放行）==="
expect_rc "不给 --platforms → rc=2" 2 \
    "$PY" "$PLAN" "$FIX/four_stage.dag.json"

echo "=== 8. 释放代价是显式参数：c 变大，R̂ 必须跟着变 ==="
A="$("$PY" "$PLAN" "$FIX/four_stage.dag.json" --platforms "$CAPS" \
     --platform linux_kernel --release-cost-us 6 2>&1 | grep 'R̂ ≈')"
B="$("$PY" "$PLAN" "$FIX/four_stage.dag.json" --platforms "$CAPS" \
     --platform linux_kernel --release-cost-us 60 2>&1 | grep 'R̂ ≈')"
if [ "$A" != "$B" ]; then
    ok "c 从 6 改到 60 时 R̂ 变化（$A vs $B）"
else
    bad "R̂ 没跟随 c 变化（说明代价模型没接上）"
fi

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" = 0 ] || exit 1
