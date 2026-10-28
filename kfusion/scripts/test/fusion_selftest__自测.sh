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
# 1:1 跨域：消费者一拍 1000 ＋ 跨域生产一拍 1000 ＋ 相位差(−600) = 1400 µs
expect_grep "死区 1400 µs，并指出跨域那一拍来自**生产者周期**" "$(cat "$TMP/out.txt")" \
    "1400" "跨域 +1000 µs（生产者一拍）" "上限 2500"

# 2) 只把反馈上限收紧到 1000 µs（期限仍是 2500）⇒ 必须拒绝
expect_rc "严契约（上限 1000）：跨域方案被拒" 3 \
    "$PY" "$PLAN" "$XDAG" --platforms "$CAPS" --platform linux_kernel \
    --release-cost-us 21 --exec-us "$EX5" --contract "$XSTRICT" \
    -o "$TMP/xdom_strict.plan.json"
expect_grep "拒绝码是 FEEDBACK_DEADZONE" "$(cat "$TMP/out.txt")" \
    "FEEDBACK_DEADZONE"
expect_grep "拒绝理由把死区拆开说清（消费者一拍 ＋ 跨域生产一拍）" \
    "$(cat "$TMP/out.txt")" "消费者一拍 1000 µs" "跨域生产一拍 1000 µs" "超过上限 1000"
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
echo "=== 32. PR10-F/PR11-B：跨域 IPC 参考模型（v3 µs 时间戳 / 多通道 / 取数三臂）==="
# 本机无 C 编译器，ecos_xipc.h / ecos_xdom.c 这套运行时只能在 Pi 上编译验证。
# 这里用一份等价的 Python 模型先验"算法对不对"：新桥必须做到 sub(X) 不吞 sub(Y)、
# 取最近一条、时间戳透传、age_us 与速率无关、拍数向上取整、预算按 µs 判；
# 并复现旧桥（PR10-C）的多通道消费 bug。
"$PY" "$HERE/xipc_versioned_model.py" >"$TMP/xipc_model.txt" 2>&1
if [ $? -eq 0 ]; then
    ok "参考模型：新桥语义全部成立（v3 时间戳 + 复现旧桥多通道 bug）"
else
    bad "参考模型：有断言未通过"
    sed -n '1,20p' "$TMP/xipc_model.txt" | sed 's/^/      /'
fi
# 规格与实现必须同口径：模型里必须出现"µs 判据"与"向上取整"这两条，
# 否则它会退化成"只描述挑哪条消息"的空壳（那正是 PR10-F 时期的状况）。
expect_grep "参考模型用 µs 判据（不是整数拍）" "$(cat "$TMP/xipc_model.txt")" \
    "预算 1 拍 = 1000 µs ⇒ 1797 > 1000 拒收" "1797 µs 记为 2 拍"

echo
echo "=== 33. PR11-C：多执行域事件时间线（跨域年龄到底由什么决定）==="
# 单核串行时间线只能给出"跨域加一拍"，而实测（§14/§15）说明那不对。
# 这里用事件网格模型验四件事：集合只由 ρ mod P_c 决定、生产者更快时 age 恒 0、
# 旧桥"每拍只取一条"会积压、以及"2ms 生产 + 1ms 消费 + 1 拍预算"没有安全相位。
"$PY" - "$HERE/../ecos" <<'PYEOF' >"$TMP/timeline.txt" 2>&1
import sys, os
sys.path.insert(0, sys.argv[1])          # ecos 目录由脚本传进来，不靠环境变量
import xdom_timeline as T

ok = True
def chk(name, cond, extra=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name + (("  " + extra) if extra else ""))
    ok = ok and cond

def band(Pp, Pc, rho, pick="latest", drift=0.0, n=600):
    return T.age_band(T.Domain("p", Pp, drift_ppm=drift),
                      T.Domain("c", Pc, phase_us=rho, drift_ppm=drift),
                      n_cons=n, pick=pick)

# 1) 1:1 同速率：每拍都有新数据，年龄恒 0
b = band(1000, 1000, 0)
chk("1:1 同速率 age 恒 0", b["values"] == [0.0] and b["reused"] == 0, str(b["values"]))

# 2) 生产 2x 慢：年龄只在 {a, a+P_c} 两值交替
b = band(2000, 1000, 254)
chk("2x 慢生产 → 两个值 {254,1254}", b["values"] == [254.0, 1254.0], str(b["values"]))
chk("2x 慢生产 → 半数拍复用旧值", abs(b["reused"] - b["n"] / 2.0) <= 2, "%d/%d" % (b["reused"], b["n"]))

# 3) 集合只由 ρ mod P_c 决定 ⇒ ρ 与 ρ+P_c 给同一集合（这正是"打印只看一个奇偶"的陷阱）
b2 = band(2000, 1000, 1254)
chk("ρ 与 ρ+P_c 的年龄集合相同（奇偶混叠）", b2["values"] == b["values"],
    "%s vs %s" % (b2["values"], b["values"]))
b3 = band(2000, 1000, 600)
chk("但不同 ρ mod P_c 换集合 {600,1600}", b3["values"] == [600.0, 1600.0], str(b3["values"]))

# 4) 生产者**更快**：抽干取最新 ⇒ age 恒 0，只浪费数据（drain 把积压吃掉）
b = band(500, 1000, 0)
chk("生产者更快 → drain-latest 的 age 恒 0（丢数据不增龄）",
    b["max_us"] == 0 and b["drops"] == 0, str(b["values"]))

# 5) 旧桥的"每拍只取一条"：生产者更快时必然积压（这就是它和取最新的本质差别）
b = band(500, 1000, 0, pick="one")
chk("生产者更快 + 每拍只取一条 → 积压或丢包",
    (b["backlog"] > 0 or b["drops"] > 0), "backlog=%d drops=%d" % (b["backlog"], b["drops"]))

# 6) 相位安全窗：2ms 生产 + 1ms 消费 + 1 拍预算 ⇒ 没有可用相位
w = T.safe_phase_window(2000, 1000, 1000, steps=360, n_cons=400)
chk("2ms/1ms + 1 拍预算 → 安全相位窗总宽 0", w["safe_width"] == 0.0,
    "%.0f/%.0f µs" % (w["safe_width"], w["lcm"]))
w15 = T.safe_phase_window(2000, 1000, 1000, drift_ppm=15, steps=360, n_cons=400)
chk("加 15 ppm 漂移 → 连孤立点都没有", w15["safe"] == [], str(w15["safe"]))
w2 = T.safe_phase_window(2000, 1000, 2000, steps=360, n_cons=400)
chk("预算放宽到 2 拍 → 几乎全宽可用", w2["safe_width"] >= 0.99 * w2["lcm"],
    "%.0f/%.0f µs" % (w2["safe_width"], w2["lcm"]))

# 7) 1:1 + 1 拍预算：任何相位都安全（这解释了"1:1 下相位看起来没用"）
w = T.safe_phase_window(1000, 1000, 1000, steps=360, n_cons=400)
chk("1:1 + 1 拍预算 → 全相位安全", w["safe_width"] >= 0.99 * w["lcm"],
    "%.0f/%.0f µs" % (w["safe_width"], w["lcm"]))

sys.exit(0 if ok else 1)
PYEOF
if [ $? -eq 0 ]; then
    ok "PR11-C 事件时间线模型：7 组断言全部成立"
else
    bad "PR11-C 事件时间线模型：有断言未通过"
    sed -n '1,24p' "$TMP/timeline.txt" | sed 's/^/      /'
fi

echo
echo "=== 34. PR11-D：多速率进验证器（各域周期各算各的）==="
# 之前 TemporalModel 只有**一个** period：多速率部署的组容量、反馈死区、完成时刻
# 全按单一周期判。后果不是"保守"，是**数值错**：
#   · 2 ms 域的组被拿 1 ms 卡容量（误杀）
#   · 死区公式 (1+xd)·P 用了消费者的 P 还是生产者的 P 根本没区分
# 这一节把三件事钉住：1:1 逐位不变、多速率死区按消费者周期×速率比、期限真的会翻红。
XC11="$HERE/../../contracts/examples/xdom_loop.contract.json"
XCMR="$HERE/../../contracts/examples/xdom_loop_multirate.contract.json"
XCMRF="$HERE/../../contracts/examples/xdom_loop_multirate_feasible.contract.json"
EX5='{"stage_sample":0.60,"stage_fir":0.67,"stage_pid":0.28,"stage_clamp":0.28,"stage_plant":0.50}'

expect_rc "1:1 跨域环仍通过（多速率改动不碰它）" 0 \
    "$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$XC11" -o "$TMP/mr_11.plan.json" --quiet

# root 2 ms ⇒ 执行器每 2 ms 才动一次，1:1 时代的 2500 µs 期限**不自洽**。
# 现在这条要被验证器翻红，而且要说清是"跨域期限"，不是含糊的失败。
"$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$XCMR" -o "$TMP/mr_bad.plan.json" >"$TMP/mr_bad.txt" 2>&1
if [ $? -eq 3 ]; then ok "多速率(2ms/1ms)+2500µs 期限 → 拒绝"; else
    bad "多速率(2ms/1ms)+2500µs 期限应被拒（rc=$?）"; fi
expect_grep "拒绝原因指到「跨域期限」而不是笼统失败" "$(cat "$TMP/mr_bad.txt")" \
    "跨域期限" "2621.8"

# 自洽的部署照样过：期限放宽到 5000 µs（2.5 个控制拍）后三道判据同时满足。
expect_rc "多速率 + 期限 5000 µs → 通过（不是一刀切）" 0 \
    "$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$XCMRF" -o "$TMP/mr_ok.plan.json" --quiet
expect_grep "多速率端到端报价用消费者周期（2000 µs/拍）" "$(cat "$TMP/mr_bad.txt")" \
    "2000 µs/拍"

# 死区数值：1:1 = 400 µs；2 ms 生产 / 1 ms 消费 = (2 拍 + 0 跨域)×1000 − 600 = 1400 µs。
# 直接问模型，不从文本里猜。
"$PY" - "$HERE/../ecos" "$FIX/xdom_loop.dag.json" "$XC11" "$XCMR" <<'PYEOF' >"$TMP/mr_dead.txt" 2>&1
import io, json, sys
sys.path.insert(0, sys.argv[1])
import temporal_model as TM

dag = json.load(io.open(sys.argv[2], encoding="utf-8"))
EX = {"stage_sample": 0.60, "stage_fir": 0.67, "stage_pid": 0.28,
      "stage_clamp": 0.28, "stage_plant": 0.50}
GR = [{"id": "g0", "members": ["stage_sample", "stage_fir", "stage_pid"],
       "release_phase_us": 0},
      {"id": "g1", "members": ["stage_clamp", "stage_plant"],
       "release_phase_us": 600}]

ok = True
def chk(name, cond, extra=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name + (("  " + extra) if extra else ""))
    ok = ok and cond

import copy, domain_model as DM

def dead(ctr):
    """按**真实调用路径**算：验证器/规划器都传 dom，跨域那一拍才算得出来。"""
    tm = TM.TemporalModel(dag, contract=ctr, exec_us=EX, release_cost_us=21)
    dom = DM.load(dag, period_us=tm.period,
                  domain_period_us=tm.domain_periods)
    rows = tm.schedule(GR)
    return tm, dom, tm.feedback_delays(GR, dom=dom, sched=rows)[0]

C11 = json.load(io.open(sys.argv[3], encoding="utf-8"))
CMR = json.load(io.open(sys.argv[4], encoding="utf-8"))
tm1, dom1, f1 = dead(C11)
# 1:1 跨域：消费者一拍 1000 ＋ 生产者一拍 1000 ＋ 相位差(−600) = 1400 µs。
# 与 PR10-B 的 (1+xd)·P 逐位一致——这是"改动没动 1:1"的凭据。
chk("1:1 跨域死区仍是 1400 µs（等价 PR10-B 的 2P − 600）",
    abs(f1["dead_us"] - 1400.0) < 0.5, str(f1["dead_us"]))
# 同域（不跨域）必须退化成"一拍"：400 µs = 1000 − 600
f1_same = TM.TemporalModel(dag, contract=C11, exec_us=EX,
                           release_cost_us=21).feedback_delays(
    GR, dom=None, sched=tm1.schedule(GR))[0]
chk("同域反馈边退化成消费者一拍（400 µs），不带跨域项",
    abs(f1_same["dead_us"] - 400.0) < 0.5, str(f1_same["dead_us"]))
chk("1:1 不声明各域周期 ⇒ multirate=False", tm1.multirate is False)

tm2, dom2, f2 = dead(CMR)
c2 = f2["components"]
# 死区 = 消费者一拍 1000 + 跨域生产一拍 2000 + 相位差(−600) = 2400 µs
chk("多速率：死区 = 消费者一拍 + 跨域生产一拍 = 2400 µs",
    abs(f2["dead_us"] - 2400.0) < 0.5, str(f2["dead_us"]))
chk("多速率：跨域项用的是**生产者**周期 2000 µs",
    abs(c2["cross_period_us"] - 2000.0) < 1e-6
    and abs(c2["period_us"] - 1000.0) < 1e-6,
    "cross=%s Pc=%s" % (c2["cross_period_us"], c2["period_us"]))
chk("多速率：rate_ratio 仍按 ceil 报（2 拍，只作人类可读）",
    c2["rate_ratio"] == 2, str(c2["rate_ratio"]))

# 非整数倍速率比：ceil 会**高估**死区。1.5:1 的真实最大年龄是 1500（=P_p），
# 而 ceil 给 2·P_c = 2000 ⇒ 死区 2400 而不是 1900，会假性突破上限、误拒方案。
C15 = copy.deepcopy(CMR)
C15["timing_constraints"]["domain_period_us"]["root"] = 1500
tm3, dom3, f3 = dead(C15)
chk("非整数比 1.5:1：死区 = 1000 + 1500 − 600 = 1900 µs（不再被 ceil 抬到 2400）",
    abs(f3["dead_us"] - 1900.0) < 0.5, str(f3["dead_us"]))
chk("非整数比：确实比 ceil 旧公式（2400）更紧", f3["dead_us"] < 2399.5,
    str(f3["dead_us"]))
# 上界性：死区（扣掉相位差）必须 ≥ 模型扫出来的最大年龄
import xdom_timeline as TL
worst = 0
for rho in (0, 250, 500, 750):
    b = TL.age_band(TL.Domain("p", 1500), TL.Domain("c", 1000, phase_us=rho),
                    n_cons=400)
    worst = max(worst, b["max_us"] or 0)
chk("1.5:1 的死区确实覆盖模型实测的最大年龄",
    (f3["dead_us"] - c2["phase_gap_us"]) >= worst - 1.0,
    "dead-gap=%.0f 实测 max=%.0f" % (f3["dead_us"] - c2["phase_gap_us"], worst))
chk("多速率：period_of 按域取（root=2000 / nuttx=1000）",
    tm2.period_of("root") == 2000.0 and tm2.period_of("nuttx") == 1000.0)

# 组容量要按**本组所在域**的周期卡：root 组给 1.5 ms 执行时间，
# 用 1 ms 判会误杀，用 root 的 2 ms 判应通过。
import copy
EX2 = dict(EX); EX2["stage_clamp"] = 1500.0
tm4 = TM.TemporalModel(dag, contract=CMR, exec_us=EX2, release_cost_us=21)
chk("组容量按域周期：2 ms 域里 1.5 ms 的段不再误杀",
    tm4.period_of(tm4.group_domain(["stage_clamp", "stage_plant"])) == 2000.0)

sys.exit(0 if ok else 1)
PYEOF
if [ $? -eq 0 ]; then
    ok "多速率时间语义：6 组断言全部成立"
else
    bad "多速率时间语义：有断言未通过"
    sed -n '1,12p' "$TMP/mr_dead.txt" | sed 's/^/      /'
fi

echo
echo "=== 35. PR11-D：部署可行性进了**验证器**（不只是一道门）==="
# 以前这条只判在契约层（contract_validate）和生成层（emit）。项目铁律是
# "整方案合法性只认 plan_verify"，于是会出现：一份方案在这里验证通过，
# 却在该速率组合下根本排不出预算内的年龄——那种通过是假的。
# 这一节钉住三件事：自洽部署过、不合预算的部署被验证器判、缺预算是 fail-closed。
XCMRF="$HERE/../../contracts/examples/xdom_loop_multirate_feasible.contract.json"

rm -f "$TMP/dep.plan.json"
expect_rc "自洽的多速率部署：规划 + 独立验证都过" 0 \
    "$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$XCMRF" -o "$TMP/dep.plan.json" --quiet

# 只把预算从 2 拍改成 1 拍。期限仍是 5000（跨域期限**不**红），
# 所以这条如果被拒，只能是因为"年龄预算排不出"——判据挂对了地方。
"$PY" - "$XCMRF" "$TMP/dep_b1.contract.json" <<'PYEOF'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
d["timing_constraints"]["xdom_max_age_ticks"] = 1
json.dump(d, io.open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
"$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$TMP/dep_b1.contract.json" -o "$TMP/dep_b1.plan.json" \
    >"$TMP/dep_b1.log" 2>&1
if [ $? -eq 3 ]; then ok "预算 1 拍 + 2ms/1ms ⇒ 规划阶段即拒绝"; else
    bad "预算 1 拍 + 2ms/1ms 应在规划阶段被拒"; fi
expect_grep "拒绝码是 XDOM_INFEASIBLE（不是含糊失败）" "$(cat "$TMP/dep_b1.log")" \
    "[XDOM_INFEASIBLE]"

# 关键一条：把**已经通过验证**的方案拿去用更严的契约复检，也必须红。
# 这证明判据在验证器里（而不是只挡在生成那条路上）。
"$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$TMP/dep_b1.contract.json" --verify "$TMP/dep.plan.json" \
    >"$TMP/dep_reverify.log" 2>&1
if [ $? -eq 3 ]; then ok "已通过方案在更严预算下复检被拒（判据在验证器里）"; else
    bad "复检没拦住更严预算下的部署"; fi
expect_grep "复检也报 XDOM_INFEASIBLE" "$(cat "$TMP/dep_reverify.log")" \
    "[XDOM_INFEASIBLE]"

# 有跨域边但契约没给预算 ⇒ fail-closed（"预算无穷大"等于没有约束）
"$PY" - "$XCMRF" "$TMP/dep_nob.contract.json" <<'PYEOF'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
d["timing_constraints"].pop("xdom_max_age_ticks", None)
json.dump(d, io.open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
"$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$TMP/dep_nob.contract.json" -o "$TMP/dep_nob.plan.json" \
    >"$TMP/dep_nob.log" 2>&1
if [ $? -eq 3 ]; then ok "有跨域边但没预算 ⇒ fail-closed"; else
    bad "没预算时应 fail-closed"; fi
expect_grep "拒绝码是 XDOM_BUDGET_MISSING" "$(cat "$TMP/dep_nob.log")" \
    "[XDOM_BUDGET_MISSING]"
# §24 长跑 A/B 用的夹具：只需把预算改成 2 拍，其余完全一样（别让它腐坏）
expect_rc "预算 2 拍的长跑夹具仍通过（§24 就是用它跑的）" 0 \
    "$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$HERE/../../contracts/examples/xdom_loop_budget2.contract.json" \
    -o "$TMP/dep_b2.plan.json" --quiet

# 1:1 不能被这次改动带歪（判据对 1:1 本来就是"窗≈85%，通过"）
expect_rc "1:1 跨域环仍通过（判据不误杀）" 0 \
    "$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$HERE/../../contracts/examples/xdom_loop.contract.json" \
    -o "$TMP/dep_11.plan.json" --quiet

echo
echo "=== 36. PR11-D：非整数倍速率比（1.5:1 / 1.2:1）==="
# 之前死区用 (k + xd)·P_c，k=ceil(P_p/P_c)。这只在**整数倍**比下正确：
# 非整数比时 k 会跳一档，死区被凭空放大，反馈上限被假性突破 ⇒ 方案被误拒。
# 模型（xdom_timeline 扫 ρ）告诉我们真实上界是 P_p，所以死区取 P_c + xd·P_p。
"$PY" - "$HERE/../ecos" <<'PYEOF' >"$TMP/mr_int.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import xdom_timeline as T

ok = True
def chk(name, cond, extra=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name + (("  " + extra) if extra else ""))
    ok = ok and cond

# 1) 上界性：扫 ρ 得到的最大年龄 ≤ P_p（这是把死区写成 P_c + xd·P_p 的依据）
worst = {}
for Pp, Pc in ((1500, 1000), (1200, 1000), (2500, 1000), (1000, 1500)):
    m = 0
    for rho in range(0, Pp * Pc // T._gcd(Pp, Pc), max(1, Pc // 4)):
        b = T.age_band(T.Domain("p", Pp), T.Domain("c", Pc, phase_us=rho), n_cons=400)
        m = max(m, b["max_us"] or 0)
    worst[(Pp, Pc)] = m
chk("最大年龄 ≤ P_p（1.5:1 / 1.2:1 / 2.5:1 / 1:1.5 都成立，采样粒度内）",
    all(worst[k] <= k[0] + 0.5 * k[1] for k in worst),
    ", ".join("%d/%d→%.0f" % (a, b, v) for (a, b), v in worst.items()))

# 2) 可行性判据对非整数比也是"排得出/排不出"，不是一刀切
r1 = T.check_edge(1500, 1000, 1000, drift_ppm=15, jitter_us=150)
r2 = T.check_edge(1500, 1000, 2000, drift_ppm=15, jitter_us=150)
chk("1.5:1 + 预算 1 拍 ⇒ 不可行（真实最大年龄 1500 > 850）", not r1["ok"], r1["reason"][:40])
chk("1.5:1 + 预算 2 拍 ⇒ 可行且窗 100%", r2["ok"] and r2["frac"] > 0.99,
    "%.0f%%" % (100 * r2["frac"]))

# 3) "窗很窄"这一档要走告警而不是拒绝：1.2:1 + 预算 1.2 拍
r3 = T.check_edge(1200, 1000, 1200, drift_ppm=15, jitter_us=150)
chk("1.2:1 + 预算 1.2 拍 ⇒ 可行但窗很窄（16%，应告警不应拒）",
    r3["ok"] and 0.05 < r3["frac"] < 0.30, "%.0f%%" % (100 * r3["frac"]))

# 4) 死区上界必须覆盖实测最大年龄（扣掉相位差）
for Pp, Pc in ((1500, 1000), (1200, 1000)):
    dead_bound = Pc + Pp          # 跨域：消费者一拍 + 生产者一拍
    chk("死区上界 %d+%d=%d 覆盖实测最大年龄 %.0f" % (Pc, Pp, dead_bound, worst[(Pp, Pc)]),
        dead_bound >= worst[(Pp, Pc)] - 0.5)
sys.exit(0 if ok else 1)
PYEOF
if [ $? -eq 0 ]; then
    ok "非整数比：上界性 + 可行性判据 7 组断言成立"
else
    bad "非整数比：有断言未通过"
    sed -n '1,10p' "$TMP/mr_int.txt" | sed 's/^/      /'
fi

# 契约层也要真的动：1.5:1 + 预算 1 拍 ⇒ 拒；预算 2 拍 ⇒ 过
"$PY" - "$HERE/../../contracts/examples/xdom_loop_multirate_feasible.contract.json" \
    "$TMP/mr15_b1.contract.json" "$TMP/mr15_b2.contract.json" <<'PYEOF'
import io, json, sys
src, b1, b2 = sys.argv[1:4]
d = json.load(io.open(src, encoding="utf-8"))
d["timing_constraints"]["domain_period_us"]["root"] = 1500
import copy
a = copy.deepcopy(d); a["timing_constraints"]["xdom_max_age_ticks"] = 1
b = copy.deepcopy(d); b["timing_constraints"]["xdom_max_age_ticks"] = 2
for obj, p in ((a, b1), (b, b2)):
    json.dump(obj, io.open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
expect_rc "契约层：1.5:1 + 预算 1 拍 ⇒ 拒（真实最大年龄 1500 > 1000）" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" "$TMP/mr15_b1.contract.json"
expect_rc "契约层：1.5:1 + 预算 2 拍 ⇒ 过" 0 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" "$TMP/mr15_b2.contract.json"
# 反馈死区也要跟着非整数比走：1900 µs（= 1000 + 1500 − 600）≤ 上限 2500
expect_rc "1.5:1 规划 + 验证都过（死区 1900 ≤ 2500）" 0 \
    "$PY" "$PLAN" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$TMP/mr15_b2.contract.json" -o "$TMP/mr15.plan.json" --quiet

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" = 0 ] || exit 1
