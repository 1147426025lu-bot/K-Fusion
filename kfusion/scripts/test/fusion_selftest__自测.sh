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
CODEGEN="$HERE/../fusion_codegen__生成融合代码.py"
FIX="$HERE/fusion"
CAPS="$HERE/../../contracts/examples/platform_caps.json"
PLC="$HERE/../../docs/figures/dag/plc_chain.dag.json"
LOOP_SRC="$HERE/../../examples/ecos/five_stage_loop__闭环五阶段示例.c"
TMP="$(mktemp -d 2>/dev/null || echo /tmp/fusion_selftest.$$)"
mkdir -p "$TMP"

# 执行时间：取 BM2 四阶段的实测量级，闭环多一个 plant
cat >"$TMP/exec.json" <<'EOF'
{"stage_sample":0.60,"stage_fir":0.67,"stage_pid":0.28,
 "stage_clamp":0.28,"stage_plant":0.50}
EOF

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

echo "=== 9. 闭环（跨周期）：环必须能被规划，且死区变化要报出来 ==="
OUT="$("$PY" "$PLAN" "$FIX/five_stage_loop.dag.json" --platforms "$CAPS" \
      --platform linux_kernel --release-cost-us 21 --exec-us "$TMP/exec.json" 2>&1)"
RC=$?
if [ "$RC" = 0 ]; then ok "闭环有解（rc=0）"; else bad "闭环被拒，rc=$RC"; fi
expect_grep "整环并成 1 组" "$OUT" "方案：1 组" "g0   stage_sample + stage_fir"
expect_grep "R̂ = 21 + 2.33 ≈ 23.3" "$OUT" "R̂ ≈ 23.3"
expect_grep "报出死区时间变化（200 µs → 998 µs，×4.99）" "$OUT" \
    "反馈环落位" "间隔 200 µs → 998 µs（×4.99）"
expect_grep "说清环为何是强连通分量、只能整体成组" "$OUT" "不可分性规则下只能整体成组"

echo "=== 10. 死区放大门禁：超过上限必须能让 CI 红 ==="
expect_rc "×5 超过上限 ×2 → rc=3" 3 \
    "$PY" "$PLAN" "$FIX/five_stage_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$TMP/exec.json" \
    --max-delay-inflation 2 --fail-on-warning
expect_grep "warning 指名闭合边与倍数" "$(cat "$TMP/out.txt")" \
    "死区时间 ×4.99" "超过上限 ×2.00"
expect_rc "上限给 ×9 时放行（rc=0）" 0 \
    "$PY" "$PLAN" "$FIX/five_stage_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$TMP/exec.json" \
    --max-delay-inflation 9 --fail-on-warning

echo "=== 11. 代数环：相位已知但环内无边反向 → 必须拒 ==="
expect_rc "代数环 → rc=2" 2 \
    "$PY" "$PLAN" "$FIX/algebraic.dag.json" --platforms "$CAPS" --platform nuttx
expect_grep "说清是代数环、且不是融合算法能绕开的" "$(cat "$TMP/out.txt")" \
    "代数环" "同一拍内闭合" "不是融合算法能绕开的"
expect_rc "无相位信息的环同样拒（fail-closed）" 2 \
    "$PY" "$PLAN" "$FIX/cyclic.dag.json" --platforms "$CAPS" --platform nuttx

echo "=== 12. 相位钉住：读外部输入的阶段不能被前拉 ==="
OUT="$("$PY" "$PLAN" "$FIX/phase_pin.dag.json" --platforms "$CAPS" \
      --platform nuttx --release-cost-us 6 2>&1)"
expect_grep "b 自己当组长（a | b+c，2 组）" "$OUT" "方案：2 组" "g0   a " "g1   b + c"
expect_grep "拒绝理由说清「等于提前采样」" "$OUT" "提前采样" "前拉 200 µs"

echo "=== 13. 代码生成：结构、摘要、两道守卫 ==="
"$PY" "$PLAN" "$FIX/five_stage_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$TMP/exec.json" \
    -o "$TMP/plan.json" --quiet >/dev/null 2>&1
"$PY" "$CODEGEN" "$TMP/plan.json" -o "$TMP/driver.c" \
    --stage-source "$LOOP_SRC" --call "stage_pid=stage_pid(1000)" \
    --period-us 1000 --step-us 200 --cycles 60000 \
    --digest-var g_out --digest-var g_plant \
    --expect-order "stage_sample,stage_fir,stage_pid,stage_clamp,stage_plant" \
    >"$TMP/cg.txt" 2>&1
if [ -s "$TMP/driver.c" ]; then
    ok "驱动生成（$(grep -c '/\* stage_' "$TMP/driver.c") 条 stage 释放项）"
else
    bad "驱动未生成：$(head -2 "$TMP/cg.txt")"
fi
PLAN_ROWS="$(sed -n '/kPlan\[\] = {/,/^};/p' "$TMP/driver.c" | grep -c '/\* g0 \*/')"
FLAT_ROWS="$(sed -n '/kFlat\[\] = {/,/^};/p' "$TMP/driver.c" | grep -c '/\* stage_')"
if [ "$PLAN_ROWS" = 1 ] && [ "$FLAT_ROWS" = 5 ]; then
    ok "plan 1 次释放 / flat 5 次释放，工作量同源"
else
    bad "释放表不符合预期（plan=$PLAN_ROWS flat=$FLAT_ROWS）"
fi
if grep -q 'DIGEST_MIX(g_out)' "$TMP/driver.c" && \
   grep -q '# digest: %08x' "$TMP/driver.c"; then
    ok "摘要已注入（跨周期等价的判据）"
else
    bad "摘要未注入"
fi
expect_rc "执行顺序与相位序不一致 → 拒绝" 1 \
    "$PY" "$CODEGEN" "$TMP/plan.json" -o "$TMP/d2.c" --stage-source "$LOOP_SRC" \
    --expect-order "stage_plant,stage_sample"
expect_grep "说清期望与实际" "$(cat "$TMP/out.txt")" "执行顺序与相位序不一致"
expect_rc "相位铺不下（800 > 700）→ 拒绝" 1 \
    "$PY" "$CODEGEN" "$TMP/plan.json" -o "$TMP/d3.c" --stage-source "$LOOP_SRC" \
    --period-us 700 --step-us 200
expect_grep "算出 N-1×step 并给出改法" "$(cat "$TMP/out.txt")" \
    "相位铺不下" "调小 --step-us"

echo "=== 14. --threads / --entry：真部署模式与 NuttX 入口 ==="
"$PY" "$CODEGEN" "$TMP/plan.json" -o "$TMP/driver_mt.c" --stage-source "$LOOP_SRC" \
    --period-us 1000 --step-us 200 --threads --entry fiveloop_main >/dev/null 2>&1
if grep -q "static int run_multi" "$TMP/driver_mt.c" && \
   grep -q "pthread_create" "$TMP/driver_mt.c" && \
   grep -q "sched_setscheduler" "$TMP/driver_mt.c" && \
   grep -q "int fiveloop_main" "$TMP/driver_mt.c" && \
   ! grep -q "^int main" "$TMP/driver_mt.c"; then
    ok "multi 已生成（pthread+FIFO），入口改名 fiveloop_main"
else
    bad "--threads/--entry 未生效"
fi
"$PY" "$CODEGEN" "$TMP/plan.json" -o "$TMP/d_plain.c" --stage-source "$LOOP_SRC" \
    --period-us 1000 --step-us 200 --threads >/dev/null 2>&1
if ! grep -q "define DIGEST_MIX" "$TMP/d_plain.c" && grep -q "int main" "$TMP/d_plain.c"; then
    ok "不给 --digest-var 时 multi 不掺摘要；--entry 缺省仍是 main"
else
    bad "digest/entry 的缺省行为变了"
fi

echo
echo "=== 15. PR6：不确定依赖门禁（WAW 冲突无已证明顺序 → 全融合被拒）==="
OUT="$("$PY" "$PLAN" "$FIX/uncertain.dag.json" --platforms "$CAPS" --platform nuttx --release-cost-us 6 2>&1)"
rc=$?
if [ "$rc" = 0 ]; then ok "有解（rc=0）"; else bad "被拒，rc=$rc"; fi
expect_grep "不是 1 组（冲突挡住全融合）" "$OUT" "方案：2 组"
expect_grep "指认 WAW 冲突顺序不确定" "$OUT" "WAW 冲突 b/c" "顺序不确定"
expect_rc "全融合方案复检必须被拒（rc=3）" 3 \
    "$PY" "$PLAN" "$FIX/uncertain.dag.json" --platforms "$CAPS" --platform nuttx \
    --verify "$FIX/uncertain.allfused.plan.json"
expect_grep "复检指出不确定" "$(cat "$TMP/out.txt")" "不确定：WAW 冲突 b/c"

echo "=== 16. PR7：相位感知端到端（融合被相位钉住挡住时如实报大延迟）==="
OUT="$("$PY" "$PLAN" "$FIX/loop_phasepin.dag.json" --platforms "$CAPS" --platform linux_kernel \
      --release-cost-us 21 --exec-us "$TMP/exec.json" 2>&1)"
# 挡住全融合的是**相位钉住**（plant 读外部输入，不能被前拉 800 µs），
# 不是"环是 SCC 所以只能全拆"——后者是同一个假阳性造成的旧结论（见 PR10 §8.4）。
expect_grep "相位钉住挡住全融合（plant 独立成组头）" "$OUT" "方案：2 组"
expect_grep "挡住的原因写的是相位，不是不可分性" "$OUT" "相位：stage_plant"
expect_grep "端到端 ≈821（不是旧模型 107 唤醒税求和）" "$OUT" "R̂ ≈ 821"
expect_grep "显式拆出相位跨度" "$OUT" "相位跨度 800"

echo "=== 17. PR8：控制语义硬约束（反馈死区绝对上限 → 全融合被拒，反馈保持 200µs）==="
expect_rc "全融合方案复检必须被拒（rc=3）" 3 \
    "$PY" "$PLAN" "$FIX/loop_fb_limit.dag.json" --platforms "$CAPS" --platform linux_kernel \
    --verify "$FIX/loop_fb_limit.allfused.plan.json"
expect_grep "复检指出控制：反馈边必须跨组" "$(cat "$TMP/out.txt")" "控制：反馈边" "必须跨组"
OUT="$("$PY" "$PLAN" "$FIX/loop_fb_limit.dag.json" --platforms "$CAPS" --platform linux_kernel \
      --release-cost-us 21 --exec-us "$TMP/exec.json" 2>&1)"
rc=$?
if [ "$rc" = 0 ]; then ok "给出可行方案（rc=0）"; else bad "被拒，rc=$rc"; fi
# 死区上限 500 µs 是**硬约束**，不是优化目标：融合把闭合边从 200 拉到 400 µs，
# 只要不越上限就合法。旧代码因为不可分性假阳性只能给出 5 组（死区 200），
# 现在 2 组（死区 400、唤醒税更少）同样合法且 R̂ 更小。
expect_grep "死区被拉到 400µs，仍在 500 上限内" "$OUT" "间隔 200 µs → 400 µs（×2.00）"

echo "=== 18. PR8+边界选择：非环链中间段相位钉住 → 自动选 2 组正确边界 ==="
OUT="$("$PY" "$PLAN" "$FIX/chain_pin.dag.json" --platforms "$CAPS" --platform nuttx \
      --release-cost-us 21 --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}' 2>&1)"
expect_grep "选 2 组（前两段 | 后两段）" "$OUT" "方案：2 组"
expect_grep "端到端 ≈422（相位跨度 400 + 末组唤醒 + 末组执行）" "$OUT" "R̂ ≈ 421.8"
expect_grep "说明全融合为何被拒（提前采样）" "$OUT" "提前采样"

echo
echo "=== 19. P0-1：组间顺序必须被验（倒序方案 → 拒）==="
expect_rc "完全倒序的单节点组复检必须被拒（rc=3）" 3 \
    "$PY" "$PLAN" "$FIX/four_stage.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --verify "$FIX/reversed.plan.json"
expect_grep "指认具体越序边+证据" "$(cat "$TMP/out.txt")" \
    "依赖越序" "stage_sample→stage_fir" "@g_raw"

echo "=== 20. P0-2：绝对周期窗口（前拉越界也必须拒，不靠 phase_pinned）==="
CONTRACT="$HERE/../../contracts/examples/chain_window.contract.json"
OUT="$("$PY" "$PLAN" "$FIX/chain_window.dag.json" --platforms "$CAPS" --platform nuttx \
      --release-cost-us 21 --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}' \
      --contract "$CONTRACT" 2>&1)"
rc=$?
if [ "$rc" = 0 ]; then ok "有解（rc=0）"; else bad "被拒，rc=$rc"; fi
expect_grep "绝对窗口选出 s1+s2 | s3+s4" "$OUT" "方案：2 组" "g1   s3 + s4"
expect_grep "端到端 ≈421.8" "$OUT" "R̂ ≈ 421.8"
expect_grep "全融合被拒的理由是**绝对时刻**越窗" "$OUT" \
    "早于窗口下界 380" "前拉了 357.7"

echo "=== 21. P0-2：期限不可行 → 必须报不可行，不得输出「最优」违约方案 ==="
expect_rc "矛盾契约 → rc=3" 3 \
    "$PY" "$PLAN" "$FIX/chain_window.dag.json" --platforms "$CAPS" --platform nuttx \
    --release-cost-us 21 --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}' \
    --contract "$HERE/../../contracts/examples/chain_window_tight.contract.json"
expect_grep "说是时间不可行（不是域问题）" "$(cat "$TMP/out.txt")" "时间不可行"

echo "=== 22. P0-1：代码生成器必须拒绝未通过验证的方案 ==="
rm -f "$TMP/reversed.c"
"$PY" "$CODEGEN" "$FIX/reversed.plan.json" -o "$TMP/reversed.c" \
    --stage-source "$LOOP_SRC" --dag "$FIX/four_stage.dag.json" \
    --platforms "$CAPS" --platform linux_kernel >"$TMP/out.txt" 2>&1
rc=$?
if [ "$rc" != 0 ]; then ok "生成前被拒（rc=$rc）"; else bad "倒序方案竟然生成了代码"; fi
if [ -f "$TMP/reversed.c" ]; then bad "拒绝后仍留下了产物文件"; else ok "未留下产物（拒绝即停）"; fi
expect_grep "拒绝理由来自独立验证器" "$(cat "$TMP/out.txt")" "拒绝生成代码"

echo "=== 23. P0-3：ECoS 严格入口（缺契约/契约写错/双后端生成）==="
ECOS="$HERE/../ecos_build.sh"
expect_rc "正常链路可跑通" 0 \
    bash "$ECOS" --dag "$FIX/chain_window.dag.json" --contract "$CONTRACT" \
    --platforms "$CAPS" --platform nuttx --release-cost-us 21 \
    --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}' \
    --source "$LOOP_SRC" --out-dir "$TMP/ecos_ok"
expect_rc "缺契约 → rc=2（严格入口不接受缺省）" 2 \
    bash "$ECOS" --dag "$FIX/chain_window.dag.json" --platforms "$CAPS" \
    --platform nuttx --source "$LOOP_SRC" --out-dir "$TMP/ecos_nocontract"
cat >"$TMP/bad.contract.json" <<'EOF'
{"task":{"period_us":1000},
 "timing_constraints":{"actuator":{"stage":"s3_typo","earliest_us":380,"latest_us":520}}}
EOF
expect_rc "契约 stage 名写错 → rc=3（否则窗口静默失效）" 3 \
    bash "$ECOS" --dag "$FIX/chain_window.dag.json" --contract "$TMP/bad.contract.json" \
    --platforms "$CAPS" --platform nuttx --source "$LOOP_SRC" --out-dir "$TMP/ecos_bad"
expect_grep "指出是哪个 stage 写错" "$(cat "$TMP/out.txt")" "s3_typo"
expect_rc "linux-kernel 目标：生成双后端源码（rc=0，本机无内核树故 built=0）" 0 \
    bash "$ECOS" --dag "$FIX/chain_window.dag.json" --contract "$CONTRACT" \
    --platforms "$CAPS" --platform nuttx --release-cost-us 21 \
    --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}' \
    --source "$HERE/../../examples/ecos/chain_window__四段窗口示例.c" \
    --out-dir "$TMP/ecos_k" --target linux-kernel
expect_grep "产物里有 ring-0 宿主与共享周期体" "$(cat "$TMP/out.txt")" \
    "ecos_fused_linux.c" "ecos_fused_body.c"
if grep -q "built     0" "$TMP/ecos_k/manifest.txt" 2>/dev/null; then
    ok "manifest 如实记录 built=0（未构建就不报构建成功）"
else
    bad "manifest 的 built 字段不对"
fi

echo
echo "=== 24. PR9-D：组间积压必须算进端到端（单段合法 ≠ 整方案合法）==="
BCON="$HERE/../../contracts/examples/backlog.contract.json"
expect_rc "a 执行 500、b 等到 540 → 完成 1040 > 期限 1000 → rc=3" 3 \
    "$PY" "$PLAN" "$FIX/backlog.dag.json" --platforms "$CAPS" --platform nuttx \
    --release-cost-us 20 --exec-us '{"a":500,"b":500}' --contract "$BCON"
expect_grep "报出真实完成时刻与积压量" "$(cat "$TMP/out.txt")" \
    "1040.0" "被前组推迟 420.0"
expect_grep "说清单段合法不能替整方案背书" "$(cat "$TMP/out.txt")" \
    "不要拿『单段都合法』当整方案合法的证明"

echo "=== 25. PR9-D：成本模型随方案走（复检不得退化成执行时间=0）==="
expect_rc "不给 --exec-us 也必须从 plan.cost_model 取成本 → rc=3" 3 \
    "$PY" "$PLAN" "$FIX/backlog.dag.json" --platforms "$CAPS" --platform nuttx \
    --release-cost-us 20 --contract "$BCON" --verify "$FIX/backlog.plan.json"
expect_grep "判的是期限而不是别的" "$(cat "$TMP/out.txt")" "DEADLINE" "1040.0"
expect_rc "命令行成本与方案不一致 → rc=2（拒绝两套成本）" 2 \
    "$PY" "$PLAN" "$FIX/backlog.dag.json" --platforms "$CAPS" --platform nuttx \
    --contract "$BCON" --exec-us '{"a":1,"b":1}' --verify "$FIX/backlog.plan.json"
expect_grep "说明是成本摘要不一致" "$(cat "$TMP/out.txt")" "cost_model 不一致"

echo "=== 26. PR9-D：节拍参数必须来自契约，不许用生成器缺省值 ==="
expect_rc "--period-us 与契约冲突 → rc=2" 2 \
    bash "$ECOS" --dag "$FIX/chain_window.dag.json" --contract "$CONTRACT" \
    --platforms "$CAPS" --platform nuttx \
    --source "$HERE/../../examples/ecos/chain_window__四段窗口示例.c" \
    --out-dir "$TMP/ecos_period" --target nuttx --period-us 500
expect_grep "指认两边数值" "$(cat "$TMP/out.txt")" "500" "1000" "两套节拍"

echo
echo "=== 27. PR10-B：跨域不可融合（硬约束，不给开关）==="
XDAG="$FIX/xdom_loop.dag.json"
XCON="$HERE/../../contracts/examples/xdom_loop.contract.json"
EX5='{"stage_sample":0.60,"stage_fir":0.67,"stage_pid":0.28,"stage_clamp":0.28,"stage_plant":0.50}'
expect_rc "跨域夹具规划成功（期限放宽到 2.5 拍）" 0 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --release-cost-us 21 --exec-us "$EX5" --contract "$XCON" \
    -o "$TMP/xdom.plan.json"
# 切割点必须落在域边界上：nuttx 三段 / root 两段
expect_grep "按域切成 2 组" "$(cat "$TMP/out.txt")" "方案：2 组"
expect_grep "拒绝理由说的是跨域，不是别的" "$(cat "$TMP/out.txt")" \
    "跨域：段内成员分属" "同一次释放"

echo
echo "=== 28. PR10-B：跨域代价必须按拍计（不是 µs）==="
expect_grep "端到端额外 1 拍" "$(cat "$TMP/out.txt")" \
    "跨域" "端到端多 1 拍" "含跨域"
# 死区被拉长到 2 拍：1 拍来自 z⁻¹，1 拍来自跨域
expect_grep "反馈闭合边死区 = 2 拍" "$(cat "$TMP/out.txt")" "1400"
# 期限仍按 1 拍 → 必须判不可行（段内 821 µs 看着没问题，加一拍就超）
expect_rc "期限只给 1 拍 → 跨域方案不可行" 3 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --release-cost-us 21 --exec-us "$EX5"
expect_grep "理由点明是跨域期限，不是域能力" "$(cat "$TMP/out.txt")" \
    "跨域期限" "整拍"

echo
echo "=== 29. PR10-B：验证器独立复核跨域（不信规划器）==="
expect_rc "混域方案（全融合）必须被拒" 3 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --contract "$XCON" --verify "$FIX/xdom_mixed.plan.json"
expect_grep "错误码是 DOMAIN_MIXED 且理由说跨域" "$(cat "$TMP/out.txt")" \
    "DOMAIN_MIXED" "跨域融合不存在"
expect_rc "合法方案复检通过" 0 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --contract "$XCON" --verify "$TMP/xdom.plan.json"
expect_grep "复检报告列出跨域边与额外拍数" "$(cat "$TMP/out.txt")" \
    "跨域" "至少多 1 拍"


echo
echo "=== 30. PR10-B：周期性环可以部分融合（同拍序保住 + 闭合边两端不同组）==="
# 旧结论"环是 SCC 只能整体成组"来自不可分性用了全边闭包（含 z⁻¹）的假阳性。
# 正确语义：摘掉延迟边后环是一条链，部分融合合法——只要同拍边顺序不变、
# 且死区上限不被突破。这里同时验规划器与验证器给出同一个结论。
for D in loop_fb_limit loop_phasepin; do
    expect_rc "$D：规划成功（rc=0）" 0 \
        "$PY" "$PLAN" "$FIX/$D.dag.json" --platforms "$CAPS" --platform linux_kernel \
        --release-cost-us 21 --exec-us "$TMP/exec.json" \
        -o "$TMP/$D.plan.json" --quiet
    expect_rc "$D：验证器复核同一份方案也通过（两边一致）" 0 \
        "$PY" "$PLAN" "$FIX/$D.dag.json" --platforms "$CAPS" --platform linux_kernel \
        --verify "$TMP/$D.plan.json" --quiet
done

echo
echo
echo "=== 31. PR10-E：反馈死区统一判据（规划器与验证器必须同一结论）==="
# 背景：跨域边自带一整拍 ⇒ 反馈闭环的死区从 1 拍变 2 拍。PR10-D 在板上量到
# 单域稳定收敛的回路跨域后发散（±90000），而工具链一路绿灯。两处漏判：
#   · 验证器只在"反馈边并入同一组"时判死区，跨组（跨域）直接跳过；
#   · 契约把 feedback_delay_max_us 写在顶层 control 下（非契约位置），
#     消费者只读 timing_constraints ⇒ 上限从未被读到，且没有任何提示。
XSTRICT="$HERE/../../contracts/examples/xdom_loop_strict.contract.json"

# 1) 宽契约（2500 µs）下这个跨域方案仍合法，且把死区与来源如实报出来
expect_rc "宽契约（上限 2500）：跨域方案仍合法" 0 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --release-cost-us 21 --exec-us "$EX5" --contract "$XCON" \
    -o "$TMP/xdom_loose.plan.json"
expect_grep "死区按 2 拍算，并指出跨域 +1 拍" "$(cat "$TMP/out.txt")" \
    "1400" "跨域 +1 拍" "上限 2500"

# 2) 只把反馈上限收紧到 1000 µs（期限仍是 2500）⇒ 必须拒绝
expect_rc "严契约（上限 1000）：跨域方案被拒" 3 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --release-cost-us 21 --exec-us "$EX5" --contract "$XSTRICT" \
    -o "$TMP/xdom_strict.plan.json"
expect_grep "拒绝码是 FEEDBACK_DEADZONE" "$(cat "$TMP/out.txt")" \
    "FEEDBACK_DEADZONE"
expect_grep "拒绝理由把 2 拍拆开说清（z⁻¹ 1 拍 ＋ 跨域 1 拍）" \
    "$(cat "$TMP/out.txt")" "z⁻¹ 1 拍" "跨域 1 拍" "超过上限 1000"
# 放宽**期限**不能绕过**反馈**约束：上面这条契约期限仍是 2500 µs，
# 拒绝理由里不该出现期限——它是被反馈死区挡住的，与期限、域能力无关。
if printf '%s' "$(cat "$TMP/out.txt")" | grep -qF "期限"; then
    bad "拒绝理由是反馈死区，不该扯到期限"
else
    ok "拒绝理由是反馈死区，与期限无关（两把不同的尺子）"
fi

# 3) 同一份方案，只换契约 → 验证器给出与规划器一致的结论。
#    这条是本次修复的核心：以前规划器算 1400、验证器压根不看，两边不一致。
expect_rc "验证器复检（宽契约）：通过（与规划器一致）" 0 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --release-cost-us 21 --exec-us "$EX5" --contract "$XCON" \
    --verify "$TMP/xdom_loose.plan.json"
expect_rc "验证器复检（严契约）：拒绝（与规划器一致）" 3 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --release-cost-us 21 --exec-us "$EX5" --contract "$XSTRICT" \
    --verify "$TMP/xdom_loose.plan.json"
expect_grep "验证器用的也是同一个诊断码" "$(cat "$TMP/out.txt")" \
    "FEEDBACK_DEADZONE"

# 4) 契约字段放错位置 → 必须报错，不能静默忽略
cat >"$TMP/contract_misplaced.json" <<'JSON'
{"version": 2,
 "task": {"period_us": 1000, "deadline_us": 2500},
 "control": {"feedback_delay_max_us": 2500}}
JSON
expect_rc "feedback_delay_max_us 写在 control 下 → 契约校验报错" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$XDAG" "$TMP/contract_misplaced.json"
expect_grep "说清是位置错、放错等于约束不存在" "$(cat "$TMP/out.txt")" \
    "非契约位置" "timing_constraints" "不存在"

# 5) 声称 bounded（硬实时上界）却拿不出 wcet → 拒绝这种证明声明
cat >"$TMP/contract_unproven.json" <<'JSON'
{"version": 2,
 "task": {"period_us": 1000, "deadline_us": 2500},
 "timing_constraints": {"feedback_delay_max_us": 2500},
 "evidence": {"exec": "bounded"}}
JSON
expect_rc "标 bounded 但无 wcet → 契约校验报错" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$XDAG" "$TMP/contract_unproven.json"
expect_grep "指出死区满足上限 ≠ 证明了控制稳定" "$(cat "$TMP/out.txt")" \
    "wcet_us" "≠ 证明了控制稳定"

# 6) 跨域方案但契约完全没给反馈上限 → fail-closed
cat >"$TMP/contract_nofb.json" <<'JSON'
{"version": 2, "task": {"period_us": 1000, "deadline_us": 2500}}
JSON
expect_rc "跨域但无反馈上限 → 验证器拒绝（fail-closed）" 3 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --release-cost-us 21 --exec-us "$EX5" --contract "$TMP/contract_nofb.json" \
    --verify "$TMP/xdom_loose.plan.json"
expect_grep "拒绝码是 FEEDBACK_CONTRACT_MISSING" "$(cat "$TMP/out.txt")" \
    "FEEDBACK_CONTRACT_MISSING"

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" = 0 ] || exit 1
