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
expect_grep "不是 1 组（环 SCC + 钉住 → 只能全拆）" "$OUT" "方案：5 组"
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
expect_grep "反馈死区保持 200µs（未被拉到 998）" "$OUT" "间隔 200 µs → 200 µs（×1.00）"

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
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" = 0 ] || exit 1
