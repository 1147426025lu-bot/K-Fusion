#!/usr/bin/env bash
# dag_selftest__自测.sh — DAG 生成器的回归测试（含三条陷阱的反证）
#
# 用法：
#   bash kfusion/scripts/test/dag_selftest__自测.sh [PYTHON]
#   默认 PYTHON=python3；Windows 下传解释器绝对路径。
#
# 为什么要有反证
# --------------
# "我们做了三条冗余抑制"这种话，如果不给反例，读者无法判断抑制是否空转。
# 所以每个陷阱都跑两遍：正常模式必须干净，--naive（关掉抑制）必须坏掉。
# 反证不过 = 用例没抓住东西，或者抑制写错了。

set -u -o pipefail

PY="${1:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAG="$HERE/../dag_build__任务DAG生成.py"
FIX="$HERE/dag"
SKEL="$HERE/../../contracts/examples/plc_chain.stages.json"
TMP="$(mktemp -d 2>/dev/null || echo /tmp/dag_selftest.$$)"
mkdir -p "$TMP"

PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

# expect_grep <说明> <文本> <必须包含>...
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

# expect_rc <说明> <期望码> <命令...>
expect_rc() {
    local desc="$1" want="$2"; shift 2
    "$@" >"$TMP/out.txt" 2>&1
    local got=$?
    if [ "$got" = "$want" ]; then
        ok "$desc（rc=$got）"
    else
        bad "$desc（期望 rc=$want，实际 rc=$got）"
        sed -n '1,12p' "$TMP/out.txt" | sed 's/^/      /'
    fi
}

echo "=== 1. 阶段视图：与人工 Sensor→FIR→PID 图对照 ==="
OUT="$("$PY" "$DAG" "$FIX/plc_chain.summary.json" --stages "$SKEL" \
      --expect "sensor_prepare->fir_filter,fir_filter->pid_step" \
      --fail-on-mismatch 2>&1)"
RC=$?
if [ "$RC" = 0 ]; then ok "P/R/F1 全 1（rc=0）"; else bad "预期完全一致，实际 rc=$RC"; fi
expect_grep "与人工拓扑完全一致" "$OUT" "与人工拓扑完全一致" "precision 1.000" "recall 1.000"
expect_grep "只出 2 条 RAW，无假 WAR" "$OUT" "data 2 / anti 0"

echo "=== 2. 函数视图：调用者陷阱（摘要传播不得成环）==="
OUT="$("$PY" "$DAG" "$FIX/caller_trap.summary.json" --fail-on-cycle 2>&1)"
RC=$?
if [ "$RC" = 0 ] && ! printf '%s' "$OUT" | grep -q "环"; then
    ok "无环，且只有 1 条 call 边"
else
    bad "预期无环，实际 rc=$RC"
fi
expect_grep "调用-数据重叠被解释掉" "$OUT" "call 1 / spawn 0 / data 0" "调用已解释 1"

echo "=== 3. 反证：--naive 关掉抑制后必须成环 ==="
expect_rc "--naive 检出环（rc=4）" 4 \
    "$PY" "$DAG" "$FIX/caller_trap.summary.json" --naive --fail-on-cycle
expect_grep "--naive 确实出了双向边" "$(cat "$TMP/out.txt")" "callee -> caller" "caller -> callee"

echo "=== 4. 真互依赖：环是真实存在的，必须报出来 ==="
expect_rc "真环检出（rc=4）" 4 \
    "$PY" "$DAG" "$FIX/true_cycle.summary.json" --fail-on-cycle
# 环的起点取决于遍历顺序，两个方向都算对
if grep -qE "p -> q -> p|q -> p -> q" "$TMP/out.txt"; then
    ok "环路径可读（p↔q 互指）"
else
    bad "环路径未打印"
fi

echo "=== 5. WAW：一条无向 conflict，不是两条有向边 ==="
OUT="$("$PY" "$DAG" "$FIX/waw_conflict.summary.json" --fail-on-cycle 2>&1)"
RC=$?
if [ "$RC" = 0 ]; then ok "WAW 未造出假环（rc=0）"; else bad "WAW 被当成环，rc=$RC"; fi
expect_grep "conflict 无向" "$OUT" "<-> w2" "conflict 1"

echo "=== 6. 区域三级具体化 ==="
OUT="$("$PY" "$DAG" "$FIX/multi_binding.summary.json" 2>&1)"
expect_grep "唯一绑定 → 已证；多绑定 → 候选" "$OUT" "唯一绑定 1" "多绑定 1" "候选 1"
expect_grep "从未调用 → 节点标接口未闭合，不造边" "$OUT" "接口未闭合 1 个节点" "stage_c"
if printf '%s' "$OUT" | grep -q "stage_c .*->"; then
    bad "接口未闭合的节点不该出边"
else
    ok "接口未闭合的节点没有出边（零信息不造边）"
fi

echo "=== 7. 接口闭合：两层相对名（聚合视图做不到）==="
OUT="$("$PY" "$DAG" "$FIX/recursive_arg.summary.json" 2>&1)"
expect_grep "b_core 的 arg:0 递归两跳解到 @g_x" "$OUT" "a_core" "b_core" "[data    ] @g_x"
expect_grep "两处 arg:0 都被具体化" "$OUT" "唯一绑定 2" "完全未解析 0"

echo "=== 8. 线程入口：spawn ≠ call，且 arg:0 被绑定 ==="
OUT="$("$PY" "$DAG" "$FIX/thread_entry.summary.json" --fail-on-cycle 2>&1)"
expect_grep "pthread_create → spawn 边" "$OUT" "[spawn   ] pthread_create(entry_fn)"
expect_grep "线程入口的 arg 绑定到形参 0" "$OUT" "唯一绑定 1"
expect_grep "创建-数据重叠并入 spawn，不出重复 data 边" "$OUT" "spawn 1 / data 0"

echo "=== 9. 退出码契约 ==="
expect_rc "人工图写错 → rc=3" 3 \
    "$PY" "$DAG" "$FIX/plc_chain.summary.json" --stages "$SKEL" \
    --expect "fir_filter->sensor_prepare" --fail-on-mismatch

echo "=== 10. DOT 产物 ==="
"$PY" "$DAG" "$FIX/plc_chain.summary.json" --stages "$SKEL" \
    --dot "$TMP/x.dot" --quiet >/dev/null 2>&1
if [ -s "$TMP/x.dot" ] && grep -q "digraph task_dag" "$TMP/x.dot"; then
    ok "DOT 可生成（$(wc -l <"$TMP/x.dot") 行）"
else
    bad "DOT 未生成"
fi
if grep -q "dir=none" "$TMP/x.dot" 2>/dev/null; then
    ok "无向边在 DOT 里用 dir=none 表达"
else
    echo "  · 本用例无 WAW，跳过 dir=none 检查"
fi

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" = 0 ] || exit 1
