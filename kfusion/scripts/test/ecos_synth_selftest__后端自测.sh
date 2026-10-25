#!/usr/bin/env bash
# ecos_synth_selftest__后端自测.sh — PR9-C 双后端生成的回归测试
#
# 用法：bash kfusion/scripts/test/ecos_synth_selftest__后端自测.sh [PYTHON]
#
# 本机没有 gcc/clang/make，也没有内核树，所以这里**不测"编译通过"**——
# 那必须在目标侧做。这里测的是"生成物是否具备可编译/可运行的必要结构"：
#
#   1. 周期体里**没有**调度原语（sleep/timer/线程/stdio/main）——这是 PR9-C 的边界
#   2. 组内调用顺序 = 方案顺序（顺序错了，融合语义就错了）
#   3. ring-0 宿主有 module_init/exit + hrtimer + kthread，且不睡
#   4. 两个后端的周期体**逐字节相同**（否则"同方案两部署"不可比）
#   5. 含 sleep 的阶段：linux 拒、nuttx 放行
#   6. 未验证方案：一律拒绝生成
#
# set -u -o pipefail：任何未定义变量/管道失败都要暴露，不静默通过。

set -u -o pipefail

PY="${1:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAN_PY="$HERE/../fusion_plan__融合规划.py"
SYNTH="$HERE/../../backend/synthesis/emit.py"
ECOS="$HERE/../ecos_build.sh"
FIX="$HERE/fusion"
CAPS="$HERE/../../contracts/examples/platform_caps.json"
CONTRACT="$HERE/../../contracts/examples/chain_window.contract.json"
SLEEP_CONTRACT="$HERE/../../contracts/examples/synth_sleep.contract.json"
SRC="$HERE/../../examples/ecos/chain_window__四段窗口示例.c"
SLEEP_SRC="$HERE/../../examples/ecos/synth_sleep__睡眠阶段示例.c"
TMP="$(mktemp -d 2>/dev/null || echo /tmp/ecos_synth.$$)"
mkdir -p "$TMP"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

expect_rc() {
    local desc="$1" want="$2"; shift 2
    "$@" >"$TMP/out.txt" 2>&1
    local got=$?
    if [ "$got" = "$want" ]; then ok "$desc（rc=$got）"
    else bad "$desc（期望 rc=$want，实际 rc=$got）"; sed -n '1,8p' "$TMP/out.txt" | sed 's/^/      /'; fi
}
expect_grep() {
    local desc="$1" file="$2"; shift 2
    local k
    for k in "$@"; do
        if ! grep -qF -- "$k" "$file"; then bad "$desc（缺少「$k」）"; return; fi
    done
    ok "$desc"
}
expect_absent() {
    local desc="$1" file="$2"; shift 2
    local k
    for k in "$@"; do
        if grep -qF -- "$k" "$file"; then bad "$desc（不该出现「$k」）"; return; fi
    done
    ok "$desc"
}

echo "=== 0. 先规划出一份带验证凭据的方案 ==="
expect_rc "chain_window 规划成功" 0 \
    "$PY" "$PLAN_PY" "$FIX/chain_window.dag.json" --platforms "$CAPS" \
    --platform nuttx --release-cost-us 21 \
    --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}' \
    --contract "$CONTRACT" -o "$TMP/plan.json" --quiet

echo "=== 1. Linux ring-0 后端：产物结构 ==="
expect_rc "emit --target linux-kernel" 0 \
    "$PY" "$SYNTH" --plan "$TMP/plan.json" --dag "$FIX/chain_window.dag.json" \
    --source "$SRC" --out-dir "$TMP/linux" --target linux-kernel \
    --contract "$CONTRACT" --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}'
for f in ecos_fused_body.h ecos_fused_body.c ecos_fused_linux.c Kbuild README.build.txt fusion_ir.json; do
    if [ -f "$TMP/linux/$f" ]; then ok "生成 $f"; else bad "缺 $f"; fi
done

echo "=== 2. 周期体不得含调度原语（PR9-C 的核心边界）==="
expect_absent "body.c 无睡眠/入口/stdio/pthread" "$TMP/linux/ecos_fused_body.c" \
    "clock_nanosleep" "int main" "stdio.h" "pthread" "usleep"

echo "=== 3. 组内调用顺序 = 方案顺序 ==="
expect_grep "g0 先 s1 后 s2" "$TMP/linux/ecos_fused_body.c" \
    "组 g0：释放相位 0 µs" "s1();" "s2();"
if awk '/void ecos_body_g0/,/^}/' "$TMP/linux/ecos_fused_body.c" | grep -n "s1();\|s2();" | head -2 \
   | grep -q "s1();" && \
   [ "$(awk '/void ecos_body_g0/,/^}/' "$TMP/linux/ecos_fused_body.c" | grep -c '();')" = "2" ]; then
    ok "g0 恰好 2 次调用且 s1 在前"
else
    bad "g0 的调用顺序/次数不对"
fi
expect_grep "g1 含 s3+s4" "$TMP/linux/ecos_fused_body.c" "组 g1：释放相位 400 µs" "s3();" "s4();"

echo "=== 4. ring-0 宿主：有模块骨架，且不睡 ==="
expect_grep "模块骨架" "$TMP/linux/ecos_fused_linux.c" \
    "module_init" "module_exit" "hrtimer_init" "kthread_create" "ecos_run_body"
expect_absent "内核宿主不含用户态原语" "$TMP/linux/ecos_fused_linux.c" \
    "clock_nanosleep" "int main" "stdio.h" "pthread_create"
expect_grep "组相位写入 hrtimer 表" "$TMP/linux/ecos_fused_linux.c" \
    "ecos_phase_ns" "400000"
expect_grep "Kbuild 模块名与方案名一致" "$TMP/linux/Kbuild" \
    "obj-m := chain_window.o" "stage_impl.o"

echo "=== 5. 两后端共用同一份周期体（逐字节）==="
expect_rc "emit --target nuttx" 0 \
    "$PY" "$SYNTH" --plan "$TMP/plan.json" --dag "$FIX/chain_window.dag.json" \
    --source "$SRC" --out-dir "$TMP/nuttx" --target nuttx \
    --contract "$CONTRACT" --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}'
if cmp -s "$TMP/linux/ecos_fused_body.c" "$TMP/nuttx/ecos_fused_body.c"; then
    ok "两后端 body.c 逐字节相同（差异只在宿主）"
else
    bad "两后端 body.c 不同 —— 对比实验会被代码差异污染"
fi

echo "=== 6. NuttX 宿主：允许睡眠、按组相位释放 G 次/周期 ==="
expect_grep "NuttX 用绝对时刻睡眠" "$TMP/nuttx/ecos_nuttx_main.c" \
    "clock_nanosleep" "TIMER_ABSTIME" "int main"
expect_grep "按组相位释放（2 组）" "$TMP/nuttx/ecos_nuttx_main.c" \
    "ecos_phase_us" "ecos_run_body(i)"
expect_grep "NuttX 构建骨架" "$TMP/nuttx/Makefile" "PROGNAME" "MAINSRC"

echo "=== 6b. NuttX 探针与入口名（A/B 上板用）==="
"$PY" "$SYNTH" --plan "$TMP/plan.json" --dag "$FIX/chain_window.dag.json" \
    --source "$SRC" --out-dir "$TMP/nuttx_probe" --target nuttx \
    --contract "$CONTRACT" --probe --nuttx-main fiveloop_main >/dev/null 2>&1
expect_grep "入口名可替换（接进既有 app 骨架）" "$TMP/nuttx_probe/ecos_nuttx_main.c" \
    "int fiveloop_main("
expect_grep "NuttX 侧也有 dz 探针（同一量法）" "$TMP/nuttx_probe/ecos_nuttx_main.c" \
    "ecos_dz_report" "# dz:" "dz_lag_sum_us"
expect_grep "NuttX 仍用绝对时刻睡眠" "$TMP/nuttx_probe/ecos_nuttx_main.c" \
    "clock_nanosleep" "TIMER_ABSTIME"

echo "=== 7. 含 sleep 的阶段：linux 拒、nuttx 放行 ==="
expect_rc "linux-kernel 拒绝 io_read（rc=3）" 3 \
    bash "$ECOS" --dag "$FIX/synth_sleep.dag.json" --contract "$SLEEP_CONTRACT" \
    --platforms "$CAPS" --platform nuttx --release-cost-us 6 \
    --source "$SLEEP_SRC" --out-dir "$TMP/sleep_linux" --target linux-kernel
expect_grep "指认具体阶段与原因" "$TMP/out.txt" "ring-0 禁止" "io_read" "sleep"
expect_rc "同一方案 nuttx 放行（rc=0）" 0 \
    bash "$ECOS" --dag "$FIX/synth_sleep.dag.json" --contract "$SLEEP_CONTRACT" \
    --platforms "$CAPS" --platform nuttx --release-cost-us 6 \
    --source "$SLEEP_SRC" --out-dir "$TMP/sleep_nuttx" --target nuttx

echo "=== 8. 未验证方案：一律拒绝生成 ==="
expect_rc "reversed.plan.json（无验证凭据）→ rc=3" 3 \
    "$PY" "$SYNTH" --plan "$FIX/reversed.plan.json" --dag "$FIX/four_stage.dag.json" \
    --source "$HERE/../../examples/ecos/four_stage_chain__四阶段示例.c" \
    --out-dir "$TMP/unverified" --target nuttx
expect_grep "说清是被验证门挡住" "$TMP/out.txt" "验证凭据"

echo "=== 9. 编译期约束（上板踩到的坑，锁进生成器）==="
# 5.15 只导出 sched_set_fifo / sched_setattr_nocheck；
# sched_setscheduler_nocheck 在 kallsyms 里是 T 但没 EXPORT → insmod 必失败。
expect_grep "用导出的 sched_set_fifo 设 FIFO" "$TMP/linux/ecos_fused_linux.c" \
    "sched_set_fifo"
# 只看代码行：注释里保留"为什么不用它"的说明是好事，不算违规
if grep -v '^[[:space:]]*[*/]' "$TMP/linux/ecos_fused_linux.c" \
     | grep -q "sched_setscheduler_nocheck"; then
    bad "代码里仍在用未导出的 sched_setscheduler_nocheck"
else
    ok "代码不调用未导出的 sched_setscheduler_nocheck（注释说明保留）"
fi
# 用户态阶段源码含 <stdint.h>，内核里没有 int32_t → 必须带 compat 头
if [ -f "$TMP/linux/compat/stdint.h" ] && \
   grep -q "typedef __s32 int32_t" "$TMP/linux/compat/stdint.h"; then
    ok "compat/stdint.h 提供定宽整数 typedef"
else
    bad "缺 compat/stdint.h（阶段源码在内核态会编不过）"
fi
expect_grep "Kbuild 挂上 compat 头搜索路径" "$TMP/linux/Kbuild" "-I\$(src)/compat"
# 观察点：没有它就证明不了"算得对"
"$PY" "$SYNTH" --plan "$TMP/plan.json" --dag "$FIX/chain_window.dag.json" \
    --source "$SRC" --out-dir "$TMP/linux_obs" --target linux-kernel \
    --contract "$CONTRACT" --digest-var cw_out --probe >/dev/null 2>&1
expect_grep "观察变量发布为只读 sysfs 参数" "$TMP/linux_obs/ecos_fused_linux.c" \
    "module_param_named(cw_out" "ecos_obs_0 = cw_out" "module_param(periods"

# 同拍快照与优先级：sysfs 分次读会跨周期；FIFO 99 要与用户态口径对齐
expect_grep "支持冻结取同拍快照" "$TMP/linux_obs/ecos_fused_linux.c" \
    "module_param(frozen" "if (unlikely(frozen))"
expect_grep "优先级可配且默认 99（sched_setattr_nocheck）" \
    "$TMP/linux_obs/ecos_fused_linux.c" "rt_priority" "sched_setattr_nocheck"
# 周期体会用到 int32_t（阶段原型），必须自带 <stdint.h>（内核侧由 compat 顶上）
expect_grep "周期体头文件引入 stdint" "$TMP/linux/ecos_fused_body.h" \
    "#include <stdint.h>"
# 相位锚点必须只取一次：逐组建线程各取一次 ktime_get() 会把相位污染
# （实测 5 组时 plant 的 800µs 变成 ≈1370µs，死区从 200 量成 626）
expect_grep "相位共用一个锚点" "$TMP/linux_obs/ecos_fused_linux.c" \
    "t0 = ktime_get()" "t0 + ecos_phase_ns[i]"
expect_absent "不再逐组各取锚点" "$TMP/linux_obs/ecos_fused_linux.c" \
    "ktime_get() + ecos_phase_ns[i]"
# 绑核 + 分位数：不绑核时跨核唤醒会污染尾部；分位数比均值更能说明问题
expect_grep "支持绑核（线程 bind + 定时器在目标核启动）" \
    "$TMP/linux_obs/ecos_fused_linux.c" \
    "module_param(cpu" "kthread_bind(g->task, cpu)" "smp_call_function_single"
expect_grep "死区直方图与分位数" "$TMP/linux_obs/ecos_fused_linux.c" \
    "dz_hist" "p99.9" "ecos_dz_hist_ops"

# ---------------------------------------------------------------------------
echo
echo "=== 10. 跨域 IPC 共享布局（PR10）==="
# 两端的字段偏移必须逐字节一致。C 侧的 XIPC_ASSERT_EQ 只在有编译器时生效，
# 本机没有，所以自测里用纯 Python 算术再独立校一遍（前置闸门）。
LAYOUT="$HERE/../ecos/xipc_layout_check.py"
XHDR="$HERE/../../runtime/xshm/ecos_xipc.h"

expect_rc "布局校验通过（head=0x40 tail=0x80 slot=0x100）" 0 \
    "$PY" "$LAYOUT"

# 负向：把 tail 前面的填充少写一个字，tail 就会挪到 0x7C，两端立刻错位
BADHDR="$TMP/ecos_xipc_broken.h"
sed 's/volatile uint32_t pad1\[15\]/volatile uint32_t pad1[14]/' "$XHDR" > "$BADHDR"
expect_rc "布局漂移必须被拒（负向）" 1 \
    "$PY" "$LAYOUT" --header "$BADHDR"

# 静态断言存在（有编译器的那一侧会真正编译它）
expect_grep "头文件带编译期布局断言" "$XHDR" \
    "__builtin_offsetof(struct xipc_ring, head)" "sizeof(struct xipc_ring) <= 4096"

# session：IVSHMEM 内存不随 cell 重建清零，没有它后启动的一侧会拿旧 magic 当就绪
expect_grep "环带 session（防残留 magic 误判）" "$XHDR" \
    "volatile uint32_t session;" "xipc_wait_session" "r->session = 0;"

# 跨域计数器只能传低 32 位，两端必须用回绕差
expect_grep "32 位回绕差（不是直接截断比较）" \
    "$HERE/../../runtime/xshm/root_side.c" "s.b - sent_lo"
expect_grep "inmate 侧也用回绕差" \
    "$HERE/../../../crtos/jailhouse_overlay_pi4/nuttx/apps/examples/xipc/xipc_main.c" \
    "XIPC_KIND_MODE" "XIPC_KIND_STOP"

# 串口只有单向时，开机入口必须让 xipc 自启动；否则实验无法由 root 侧驱动
XBUILD="$HERE/../ecos/xipc_build__构建跨域IPC两端.sh"
expect_grep "构建脚本会把开机入口换成 xipc" "$XBUILD" \
    "CONFIG_INIT_ENTRYPOINT" "xipc_main"
expect_grep "构建脚本强制刷 mtime（cp -a 保留旧时间戳会导致不重编）" "$XBUILD" \
    "-name '*.o' -delete" "touch \"\$B/apps/examples/xipc\""
expect_grep "镜像校验用本版专有标记，不用第一版就有的串" "$XBUILD" \
    "xipc: session 0x"

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" = 0 ] || exit 1
