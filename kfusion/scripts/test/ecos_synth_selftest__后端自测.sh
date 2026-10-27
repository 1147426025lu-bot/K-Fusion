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

# PR11-A 前置：宿主的释放必须用**原子计数**，不能是 hrtimer 回调与内核线程共享的裸 bool。
# 裸 bool 既非原子、又把"多次释放"塌成一次 ⇒ 线程落后时丢拍且不可见。
expect_grep "linux 宿主用原子释放计数（非裸 bool）" "$TMP/linux/ecos_fused_linux.c" \
    "atomic_inc(&g->releases)" "atomic_xchg(&g->releases, 0)" "static unsigned long missed"
expect_absent "linux 宿主不再有裸 pending 字段" "$TMP/linux/ecos_fused_linux.c" \
    "->pending" ".pending"

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
# ⚠ 入口名不是随意的：NuttX 的 builtin 注册表按 PROGNAME 引用
#   <PROGNAME 去横线>_main，源码里的入口名必须与之一致（否则链接期
#   undefined reference）。这里 PROGNAME=chain_window → 入口 chain_window_main。
expect_grep "NuttX 用绝对时刻睡眠" "$TMP/nuttx/ecos_nuttx_main.c" \
    "clock_nanosleep" "TIMER_ABSTIME" "int chain_window_main("
expect_grep "按组相位释放（2 组）" "$TMP/nuttx/ecos_nuttx_main.c" \
    "ecos_phase_us" "ecos_run_body(i)"
expect_grep "NuttX 构建骨架" "$TMP/nuttx/Makefile" "PROGNAME" "MAINSRC"
expect_grep "Makefile 的 PROGNAME 与源码入口名对应" "$TMP/nuttx/Makefile" \
    "PROGNAME  = chain_window"

echo "=== 6b. NuttX 探针与入口名（A/B 上板用）==="
# 接进既有 app 骨架：用 --nuttx-progname 对齐那个 app 的 PROGNAME，
# 入口名由它派生，不另起名字。
"$PY" "$SYNTH" --plan "$TMP/plan.json" --dag "$FIX/chain_window.dag.json" \
    --source "$SRC" --out-dir "$TMP/nuttx_probe" --target nuttx \
    --contract "$CONTRACT" --probe --nuttx-progname fiveloop >/dev/null 2>&1
expect_grep "入口名随 PROGNAME 派生（接进既有 app 骨架）" "$TMP/nuttx_probe/ecos_nuttx_main.c" \
    "int fiveloop_main("
expect_grep "probe 版 Makefile 的 PROGNAME 同步" "$TMP/nuttx_probe/Makefile" \
    "PROGNAME  = fiveloop"
expect_grep "NuttX 侧也有 dz 探针（同一量法）" "$TMP/nuttx_probe/ecos_nuttx_main.c" \
    "ecos_dz_report" "# dz:" "dz_lag_sum_us"
expect_grep "NuttX 仍用绝对时刻睡眠" "$TMP/nuttx_probe/ecos_nuttx_main.c" \
    "clock_nanosleep" "TIMER_ABSTIME"

echo "=== 6c. 入口名与 PROGNAME 不一致 → 生成期就拒（rc=2）==="
# 这条是踩过的坑：跨域那版 PROGNAME=fiveloop、入口=xdom_main，
# 板上链接期才报 undefined reference（第一次构建还是靠 libapps.a
# 里的残留 .o 蒙混过去的）。现在在生成期就拒。
expect_rc "入口名与 PROGNAME 不一致（rc=2）" 2 \
    "$PY" "$SYNTH" --plan "$TMP/plan.json" --dag "$FIX/chain_window.dag.json" \
    --source "$SRC" --out-dir "$TMP/nuttx_bad" --target nuttx \
    --contract "$CONTRACT" --nuttx-progname fiveloop --nuttx-main xdom_main
expect_grep "拒绝理由说的是注册表符号" "$TMP/out.txt" \
    "fiveloop_main" "undefined reference"
if [ -e "$TMP/nuttx_bad" ]; then
    bad "拒绝后仍留下半成品目录：$(ls "$TMP/nuttx_bad" | tr '\n' ' ')"
else
    ok "拒绝后不留半成品"
fi

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
    "__builtin_offsetof(struct xipc_ring, head)" "sizeof(struct xipc_ring) <= XIPC_PEER_STRIDE"

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

# ---------------------------------------------------------------------------
echo
echo "=== 11. PR10-B：跨域方案不许合成成单域产物 ==="
# emitter 的产物是"一个宿主 + 一个周期体"。跨域方案塞进一个模块，等于绕过了
# "跨域融合不存在"这条硬约束，而且生成物在板上根本跑不起来。
LOOPSRC="$HERE/../../examples/ecos/five_stage_loop__闭环五阶段示例.c"
XCON="$HERE/../../contracts/examples/xdom_loop.contract.json"
XC_MR="$HERE/../../contracts/examples/xdom_loop_multirate.contract.json"
XC_MR_OK="$HERE/../../contracts/examples/xdom_loop_multirate_ok.contract.json"
EX5='{"stage_sample":0.60,"stage_fir":0.67,"stage_pid":0.28,"stage_clamp":0.28,"stage_plant":0.50}'
expect_rc "跨域夹具规划成功（域纯度已由规划器保证）" 0 \
    "$PY" "$PLAN_PY" "$FIX/xdom_loop.dag.json" --platforms "$CAPS" \
    --platform linux_kernel --release-cost-us 21 --exec-us "$EX5" \
    --contract "$XCON" -o "$TMP/xdom.plan.json" --quiet
rm -rf "$TMP/xdom_out"
expect_rc "跨域方案 → emit 拒绝（rc=6）" 6 \
    "$PY" "$SYNTH" --plan "$TMP/xdom.plan.json" --dag "$FIX/xdom_loop.dag.json" \
    --source "$LOOPSRC" --out-dir "$TMP/xdom_out" --target linux-kernel \
    --contract "$XCON" --exec-us "$EX5" --call "stage_pid=stage_pid(1000)"
expect_grep "拒绝信息点出两个域与各自成员" "$TMP/out.txt" \
    "跨域方案" "nuttx" "root"
if [ -e "$TMP/xdom_out" ]; then bad "拒绝后仍留下产物"; else ok "拒绝即停，未留产物"; fi
# 单域方案必须不受影响（否则这个门会挡住正常链路）
expect_rc "单域方案仍可生成（门不误伤）" 0 \
    "$PY" "$SYNTH" --plan "$TMP/plan.json" --dag "$FIX/chain_window.dag.json" \
    --source "$SRC" --out-dir "$TMP/linux_ok" --target linux-kernel \
    --contract "$CONTRACT" --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}'


echo
echo "=== 12. PR10-C：按域拆分 + 跨域数据桥 ==="
SPLIT="$HERE/../ecos/split_plan__按域拆分方案.py"
rm -rf "$TMP/xsplit"
expect_rc "拆分成功（父方案已验证）" 0 \
    "$PY" "$SPLIT" "$TMP/xdom.plan.json" --dag "$FIX/xdom_loop.dag.json" \
    --out-dir "$TMP/xsplit" 2>/dev/null
for f in interface.json nuttx/plan.json nuttx/meta.json root/plan.json root/meta.json; do
    if [ -f "$TMP/xsplit/$f" ]; then ok "生成 $f"; else bad "缺 $f"; fi
done
expect_grep "两条跨域通道，全局量说清楚" "$TMP/xsplit/interface.json" \
    "stage_pid" "stage_clamp" "stage_plant" "stage_sample" "g_act" "g_plant"
expect_grep "nuttx 侧：收 g_plant 在 sample 前，发 g_act 在 pid 后" \
    "$TMP/xsplit/nuttx/meta.json" "g_act" "g_plant"
# 方向不能反：root 侧应当是收 g_act、发 g_plant
expect_grep "root 侧方向相反" "$TMP/xsplit/root/meta.json" "g_act" "g_plant"

# 负向 1：父方案没验证 → 不拆
expect_rc "未验证父方案 → 拆分拒绝（rc=3）" 3 \
    "$PY" "$SPLIT" "$FIX/xdom_unverified.plan.json" --dag "$FIX/xdom_loop.dag.json" \
    --out-dir "$TMP/xsplit_bad"
expect_grep "拒绝理由说的是缺验证凭据，不是混域" "$TMP/out.txt" "验证凭据"
# 负向 2：混域 plan（跨域融合）→ 不拆
expect_rc "混域方案 → 拆分拒绝（rc=3）" 3 \
    "$PY" "$SPLIT" "$FIX/xdom_mixed.plan.json" --dag "$FIX/xdom_loop.dag.json" \
    --out-dir "$TMP/xsplit_bad2"
expect_grep "混域的拒绝理由说的是跨域融合不存在" "$TMP/out.txt" \
    "跨域" "同一次释放"

# emit：桥文件 + 接线 + 构建文件
rm -rf "$TMP/xe_nuttx" "$TMP/xe_root"
expect_rc "nuttx 端生成（带桥）" 0 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/nuttx/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xe_nuttx" --target nuttx --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/nuttx/meta.json"
for f in ecos_xdom.h ecos_xdom.c; do
    if [ -f "$TMP/xe_nuttx/$f" ]; then ok "nuttx 端生成 $f"; else bad "nuttx 端缺 $f"; fi
done
expect_grep "订阅插在 sample 之前、发布插在 pid 之后" \
    "$TMP/xe_nuttx/ecos_fused_body.c" \
    "ecos_xdom_sub(1, &g_plant);" "stage_sample();" "ecos_xdom_pub(0, g_act);"
expect_grep "nuttx Makefile 把桥编进去" "$TMP/xe_nuttx/Makefile" "ecos_xdom.c"
# PR10-F 修订：跨域宿主的周期上报里应真的调用证据打印（否则证据只存不显）。
expect_grep "nuttx 宿主周期上报打印逐拍证据" "$TMP/xe_nuttx/ecos_nuttx_main.c" \
    "ecos_xdom_report_evidence()"
# 周期锚点：跨域年龄只能靠"首拍真正跑起来的时刻"算，不能用 init 时刻
# （init 到首拍隔着建线程/起 hrtimer，差几百 µs；PR10-F 已栽过一次"各自 init 起算"）。
expect_grep "nuttx 宿主打首拍周期锚点" "$TMP/xe_nuttx/ecos_nuttx_main.c" \
    "xdom anchor base_us=" "ecos_xdom_now_us()"

expect_rc "root 端生成（带桥）" 0 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/root/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xe_root" --target linux-kernel --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/root/meta.json"
expect_grep "root Kbuild 把桥链进去" "$TMP/xe_root/Kbuild" "ecos_xdom.o"
expect_grep "root 宿主打首拍周期锚点" "$TMP/xe_root/ecos_fused_linux.c" \
    "xdom anchor now_us="
# root 侧以前**没有任何** ch0 证据（只有 nuttx 会调 report_evidence），
# 于是"谁消费我"这个方向的年龄在板上看不到（§18.4 的边界）。
expect_grep "root 宿主按秒打跨域证据" "$TMP/xe_root/ecos_fused_linux.c" \
    "ecos_xdom_report_evidence()" "periods % 1000UL == 0UL"
expect_grep "root 侧方向相反（收 g_act 发 g_plant）" \
    "$TMP/xe_root/ecos_fused_body.c" \
    "ecos_xdom_sub(0, &g_act);" "ecos_xdom_pub(1, g_plant);"
expect_grep "桥按本域角色选环（root：out=0x0000 in=0x3000）" \
    "$TMP/xe_root/ecos_xdom.c" "ECOS_XDOM_OUT   0x0000u" "ECOS_XDOM_IN    0x3000u"
expect_grep "nuttx 侧选环相反" "$TMP/xe_nuttx/ecos_xdom.c" \
    "ECOS_XDOM_OUT   0x3000u" "ECOS_XDOM_IN    0x0000u"
# 桥里不许有调度原语：它只搬字。
# ⚠ 必须剥掉注释再查：桥的注释里会解释"非 owner 分支里有 usleep"，
#   直接把注释算进去会把解释文字当成违规（踩过）。
sed 's:/\*.*\*/::g' "$TMP/xe_root/ecos_xdom.c" | grep '^\s*//' -v > "$TMP/xdom_nc.c"
expect_absent "桥里没有睡眠/线程/入口（剥注释后）" "$TMP/xdom_nc.c" \
    "usleep" "kthread" "int main"

# PR10-F 修订：周期号必须来自**共享物理计数器**（不是域内 periods、不是环 seq），
# 否则两侧各从自己 init 起算，age 里混进启动相位偏移；并记录逐拍证据。
# PR11-B：发的是**µs 时间戳**（v2 的"周期索引"在多速率下不可比，已弃用）
expect_grep "桥发 µs 时间戳（不是周期索引）" "$TMP/xe_root/ecos_xdom.c" \
    "xdom_now_us" "xipc_counter() * 1000000ull" "xipc_pub_stamped" "s.ts_us"
expect_absent "桥不再按周期索引发/收" "$TMP/xe_root/ecos_xdom.c" \
    "xipc_pub_ticked" "xdom_period_ticks"
# 拍数向上取整（1797 µs 记 2 拍，不是 1 拍），且判据用 µs 而不是整数拍
expect_grep "证据同时记 µs 与拍（拍数向上取整）" "$TMP/xe_root/ecos_xdom.c" \
    "e->age_us = age" "(age + (uint32_t)ECOS_XDOM_PERIOD_US - 1u)" "max_age = e->age"
# 预算判据必须与编译期同口径（µs）：否则 1797 µs 在预算 1 拍时会被"整数除法"放行
expect_grep "运行时预算判据用 µs（与编译期同口径）" "$TMP/xe_root/ecos_xdom.c" \
    "#define ECOS_XDOM_MAX_AGE_US" "age > ECOS_XDOM_MAX_AGE_US"
expect_absent "不再用整数拍比较做拒收" "$TMP/xe_root/ecos_xdom.c" \
    "e->age > (uint32_t)ECOS_XDOM_MAX_AGE"
expect_grep "桥记录逐拍证据并打印" "$TMP/xe_root/ecos_xdom.c" \
    "ecos_xdom_evidence" "ecos_xdom_report_evidence" "e->age"
expect_absent "桥不再依赖宿主喂的 ecos_xdom_cycle 全局" "$TMP/xe_root/ecos_xdom.c" \
    "uint32_t ecos_xdom_cycle;"

# ---- PR11-A：跨域数据年龄预算 + "取最旧"对照臂 ----
# 预算必须**同源**：契约 timing_constraints.xdom_max_age_ticks 是声明，
# 生成的 ECOS_XDOM_MAX_AGE 是运行时强制。两处各说一套就是又一处静默失效
# （PR10-E 已经栽过：上限写在 control 下，从来没被读到）。
expect_grep "契约的年龄预算落到生成代码（MAX_AGE=1）" "$TMP/xe_root/ecos_xdom.c" \
    "#define ECOS_XDOM_MAX_AGE 1"
expect_grep "默认每拍抽干取最新（PICK=0）" "$TMP/xe_nuttx/ecos_xdom.c" \
    "#define ECOS_XDOM_PICK 0"

rm -rf "$TMP/xe_old"
expect_rc "取最旧对照臂可生成" 0 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/nuttx/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xe_old" --target nuttx --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/nuttx/meta.json" --xdom-mode oldest
expect_grep "对照臂 oldest 确实批内取首条（PICK=1）" "$TMP/xe_old/ecos_xdom.c" \
    "#define ECOS_XDOM_PICK 1" "batch_seen[s.a] = 1;"

# 命令行与契约各说一套 ⇒ 拒（rc=2）。不许悄悄挑一个：声明与强制必须同源。
rm -rf "$TMP/xe_one"
expect_rc "每拍只取一条的对照臂可生成" 0 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/nuttx/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xe_one" --target nuttx --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/nuttx/meta.json" --xdom-mode one
# 这一臂的意义：旧桥 break 之后剩下的消息还留在环里 ⇒ 会积压。必须能在生成物里
# 看到"只 pop 一条"这个结构，否则它和 oldest 臂无法区分。
expect_grep "one 臂每拍只消化一条（不抽干）" "$TMP/xe_one/ecos_xdom.c" \
    "#define ECOS_XDOM_PICK 2" "if (!xipc_pop(xr_in, &s))"

expect_rc "预算与契约冲突被拒" 2 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/nuttx/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xe_conf" --target nuttx --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/nuttx/meta.json" --xdom-max-age 2

# 契约位置错（写在 control 下）与 0 拍（物理不可满足）都必须被契约校验挡住。
"$PY" - "$XCON" "$TMP/bad_age.contract.json" <<'PYEOF'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
d["timing_constraints"].pop("xdom_max_age_ticks", None)
d.setdefault("control", {})["xdom_max_age_ticks"] = 1
json.dump(d, io.open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
expect_rc "预算写在 control 下被契约校验拒" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" \
    "$TMP/bad_age.contract.json"
"$PY" - "$XCON" "$TMP/zero_age.contract.json" <<'PYEOF'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
d["timing_constraints"]["xdom_max_age_ticks"] = 0
json.dump(d, io.open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
expect_rc "0 拍预算被契约校验拒" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" \
    "$TMP/zero_age.contract.json"
# ---- PR11-B：多速率装置开关必须显式承认，且产物留痕 ----
rm -rf "$TMP/xe_exp"
expect_rc "装置实验开关缺少承认时被拒" 2 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/root/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xe_exp" --target linux-kernel --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/root/meta.json" --experiment-period-us 2000
rm -rf "$TMP/xe_exp"
expect_rc "承认后可按多速率装置生成" 0 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/root/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xe_exp" --target linux-kernel --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/root/meta.json" \
    --experiment-period-us 2000 --allow-experiment-deploy \
    --allow-infeasible-deployment
# ⚠ PR11-C 之后：这个 2ms/1ms 装置被部署判据判为**不可行**（安全相位窗为空），
#   必须显式 --allow-infeasible-deployment 才生成。这不是放宽门禁，是复现
#   已证明不可行的装置要留痕——EXPERIMENT.txt 里会写清原因。
# 周期必须真的落到宿主与桥，否则"多速率"只是说说
expect_grep "root 宿主周期变成 2000 µs" "$TMP/xe_exp/ecos_fused_linux.c" \
    "static unsigned int period_us = 2000;"
expect_grep "root 桥按本域周期算拍" "$TMP/xe_exp/ecos_xdom.c" \
    "#define ECOS_XDOM_PERIOD_US 2000"
# 偏离已验证方案必须留痕：否则几个月后没人知道这组数据不能用
if [ -f "$TMP/xe_exp/EXPERIMENT.txt" ] && grep -q "不得用于论文主张" "$TMP/xe_exp/EXPERIMENT.txt"; then
    ok "装置实验产物带 EXPERIMENT.txt 留痕"
else
    bad "装置实验产物缺 EXPERIMENT.txt 留痕"
fi

expect_rc "合法预算的契约校验通过" 0 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" "$XCON"

echo
echo "=== PR11-C：跨域部署可行性判据（排不下安全相位就拒绝生成）==="
# 事件时间线模型能给出"安全相位窗"；窗为空/只有孤立点 ⇒ 这个部署在预算内不可实现。
# 关键：判在**生成期**，不是等板上跑一轮（PR10-F §14 已实证运行时拒收救不回）。
expect_rc "契约层：多速率 + 预算1 判不可行" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" "$XC_MR"
expect_rc "契约层：多速率 + 预算2 通过" 0 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" "$XC_MR_OK"
# 两端都要拒：只看"我消费谁"会漏掉"谁消费我"（P_p 与 P_c 互换后可行性完全不同）
expect_rc "emit：多速率（消费侧）拒绝" 3 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/nuttx/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xc_mr_c" --target nuttx --contract "$XC_MR" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/nuttx/meta.json"
expect_rc "emit：多速率（生产侧）拒绝" 3 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/root/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xc_mr_p" --target linux-kernel --contract "$XC_MR" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/root/meta.json"
rm -rf "$TMP/xc_mr_ok"
expect_rc "emit：预算放宽到 2 拍即通过" 0 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/root/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xc_mr_ok" --target linux-kernel --contract "$XC_MR_OK" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/root/meta.json"
# 契约声明的各域周期必须真的落到生成代码，否则判据是"拿一个不存在的部署在算"
expect_grep "契约声明的域周期落到生成代码（root=2000）" "$TMP/xc_mr_ok/ecos_xdom.c" \
    "#define ECOS_XDOM_PERIOD_US 2000"
expect_grep "契约域周期也落到宿主" "$TMP/xc_mr_ok/ecos_fused_linux.c" \
    "static unsigned int period_us = 2000;"
# 装置开关同样受判据约束（否则实验口子就成了绕过门禁的暗道）
expect_rc "实验开关也受部署判据约束" 3 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/root/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xc_mr_exp" --target linux-kernel --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/root/meta.json" \
    --experiment-period-us 2000 --allow-experiment-deploy
# 承认之后仍能复现（复现已证明不可行的装置是正当需求，但必须留痕）
rm -rf "$TMP/xc_mr_ack"
expect_rc "承认不可行后可复现该装置" 0 \
    "$PY" "$SYNTH" --plan "$TMP/xsplit/root/plan.json" \
    --dag "$FIX/xdom_loop.dag.json" --source "$LOOPSRC" \
    --out-dir "$TMP/xc_mr_ack" --target linux-kernel --contract "$XCON" \
    --exec-us "$EX5" --call "stage_pid=stage_pid(1000)" \
    --xdom "$TMP/xsplit/root/meta.json" \
    --experiment-period-us 2000 --allow-experiment-deploy --allow-infeasible-deployment
if [ -f "$TMP/xc_mr_ack/EXPERIMENT.txt" ] && grep -q "不可行" "$TMP/xc_mr_ack/EXPERIMENT.txt"; then
    ok "承认不可行的产物把原因写进 EXPERIMENT.txt"
else
    bad "承认不可行的产物没把原因写进 EXPERIMENT.txt"
fi
# 有跨域边却没预算 ⇒ fail-closed（"预算无穷大"等于没有约束）
"$PY" - "$XCON" "$TMP/nobudget.contract.json" <<'PYEOF'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
d["timing_constraints"].pop("xdom_max_age_ticks", None)
json.dump(d, io.open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
expect_rc "有跨域边但没预算 → fail-closed" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" \
    "$TMP/nobudget.contract.json"
# 抖动余量：模型不含抖动，判据要用「预算 − 抖动」再算窗；抖动比预算还大 ⇒ 契约不成立
"$PY" - "$XCON" "$TMP/jitter_too_big.contract.json" <<'PYEOF'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
d["timing_constraints"]["xdom_jitter_us"] = 1200   # > 1000 µs 预算
json.dump(d, io.open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
expect_rc "抖动大于预算 → 契约本身不成立" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" \
    "$TMP/jitter_too_big.contract.json"
"$PY" - "$XCON" "$TMP/jitter_ctrl.contract.json" <<'PYEOF'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
d["timing_constraints"].pop("xdom_jitter_us", None)
d.setdefault("control", {})["xdom_jitter_us"] = 100
json.dump(d, io.open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
expect_rc "抖动写在 control 下被拒" 3 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" \
    "$TMP/jitter_ctrl.contract.json"
# 声明抖动后 1:1 + 预算 1 仍要通过（900 µs 有效预算下窗 ≈90%）
expect_rc "声明抖动后 1:1 预算1 仍通过" 0 \
    "$PY" "$HERE/../ecos/contract_validate.py" "$FIX/xdom_loop.dag.json" "$XCON"

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" = 0 ] || exit 1
