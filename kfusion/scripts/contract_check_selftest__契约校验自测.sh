#!/bin/bash
# contract_check_selftest__契约校验自测.sh — 跑「有效契约 + 6 个 fail-closed 负例」
#
# 用法： bash contract_check_selftest__契约校验自测.sh [python]
# 退出： 0 全过 / 1 有用例不符合预期
#
# 判据：
#   - 有效契约在允许 MMIO 的域（nuttx）→ 必须 PASS（exit 0）
#   - 同一份契约在不允许 MMIO 的域（linux_kernel）→ 必须 REJECT 且报 E_MMIO_FORBIDDEN
#     （这不是 bug，而是"MMIO 阶段必须留在 NuttX 域"的自动推导证据）
#   - 6 个负例各自必须 REJECT，且诊断里**出现**预期码（允许同时报出别的码）
set -uo pipefail

PY="${1:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
KF="$(cd "$HERE/.." && pwd)"
EX="$KF/contracts/examples"
CHK="$HERE/contract_check__契约校验.py"
CAPS="$EX/platform_caps.json"

[ -f "$CHK" ] || { echo "FAIL: 找不到 $CHK" >&2; exit 1; }
[ -f "$CAPS" ] || { echo "FAIL: 找不到 $CAPS" >&2; exit 1; }

FAIL=0
N=0

run_case() {   # $1=contract  $2=platform  $3=期望(OK|错误码)  $4=说明
    local c="$1" p="$2" want="$3" desc="$4"
    N=$((N + 1))
    local out rc=0
    out="$("$PY" "$CHK" "$c" --platforms "$CAPS" --platform "$p" 2>&1)" || rc=$?
    if [ "$want" = "OK" ]; then
        if [ "$rc" -eq 0 ]; then
            printf "  ✅ %-42s %s → PASS\n" "$(basename "$c")@$p" "$desc"
        else
            printf "  ❌ %-42s 期望 PASS，实际 rc=%s\n" "$(basename "$c")@$p" "$rc"
            echo "$out" | sed 's/^/       /'
            FAIL=1
        fi
    else
        if [ "$rc" -eq 0 ]; then
            printf "  ❌ %-42s 期望 REJECT(%s)，实际 PASS\n" "$(basename "$c")@$p" "$want"
            FAIL=1
        elif ! echo "$out" | grep -q "$want"; then
            printf "  ❌ %-42s REJECT 了但没报 %s\n" "$(basename "$c")@$p" "$want"
            echo "$out" | sed 's/^/       /'
            FAIL=1
        else
            printf "  ✅ %-42s %s → REJECT[%s]\n" "$(basename "$c")@$p" "$desc" "$want"
        fi
    fi
}

echo "=== 契约校验自测（$PY）==="
echo "-- 正例 --"
run_case "$EX/plc_chain.contract.json" nuttx OK "三阶段链在允许 MMIO 的域"
echo "-- 同一份契约在不允许 MMIO 的域：必须被拒（这是特性）--"
run_case "$EX/plc_chain.contract.json" linux_kernel E_MMIO_FORBIDDEN "MMIO 阶段不能进内核域 → 迫使 A|BC 拆分"

echo "-- 6 个 fail-closed 负例 --"
while IFS='|' read -r file plat want desc; do
    [ -z "$file" ] && continue
    run_case "$EX/negative/$file" "$plat" "$want" "$desc"
done <<'TABLE'
n1_sleep_in_irq.json|linux_kernel|E_SLEEP_IN_IRQ|中断上下文里睡眠
n2_effect_unknown.json|nuttx|E_EFFECT_UNKNOWN|未知效果按 UNKNOWN 拒绝
n3_deadline_gt_period.json|nuttx|E_DEADLINE_GT_PERIOD|deadline > period
n4_context_not_allowed.json|nuttx|E_CONTEXT|平台不支持该上下文
n5_dep_unknown.json|nuttx|E_DEP_UNKNOWN|依赖不存在的阶段
n6_dep_cycle.json|nuttx|E_DEP_CYCLE|阶段依赖成环
TABLE

echo
if [ "$FAIL" -ne 0 ]; then
    echo "❌ 契约校验自测失败（$N 个用例）"
    exit 1
fi
echo "✅ 契约校验自测通过（$N 个用例：1 正例 + 1 域约束 + 6 负例）"
