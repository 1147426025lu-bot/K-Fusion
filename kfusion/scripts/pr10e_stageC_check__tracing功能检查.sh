#!/bin/bash
# PR-10E 阶段 C：tracing 内核启动后的功能检查
#
# 原则（评审阶段 C）：先验证「能跑起来 + tracer 真能用」，不采集结论性数据，
# 不预设 timerlat 的输出格式（实测才知道），只做探测与原始样本留存。
#
# 用法：bash pr10e_stageC_check__tracing功能检查.sh
set -u

ok()   { echo "  ✓ $*"; }
bad()  { echo "  ✗ $*"; }
info() { echo "    $*"; }

RC=0
echo "=============================================================="
echo " PR-10E 阶段 C：tracing 功能检查"
echo " 时间：$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=============================================================="

# ---------- [1] 内核身份 ----------
echo "[1] 内核身份"
KREL=$(uname -r)
info "uname -r        : $KREL"
info "uname -v        : $(uname -v)"
info "arch            : $(uname -m)"
case "$KREL" in
    *-trace) ok "跑的是 tracing 内核（-trace 后缀）" ;;
    *)       bad "不是 tracing 内核（当前 $KREL）—— 后续检查仍会做，但结果不代表 tracing 能力" ;;
esac
if uname -v | grep -q PREEMPT_RT; then
    info "抢占模型      : PREEMPT_RT"
elif uname -v | grep -q PREEMPT; then
    info "抢占模型      : PREEMPT（非 RT）"
else
    info "抢占模型      : 非抢占"
fi

# ---------- [2] tracefs ----------
echo "[2] tracefs"
TDIR=""
for d in /sys/kernel/tracing /sys/kernel/debug/tracing; do
    if [ -d "$d" ] && [ -f "$d/available_tracers" ]; then TDIR="$d"; break; fi
done
if [ -n "$TDIR" ]; then
    ok "tracefs 可用：$TDIR"
else
    # 尝试挂载
    mkdir -p /sys/kernel/tracing 2>/dev/null
    mount -t tracefs nodev /sys/kernel/tracing 2>/dev/null
    if [ -f /sys/kernel/tracing/available_tracers ]; then
        TDIR=/sys/kernel/tracing; ok "已挂载 tracefs：$TDIR"
    else
        bad "tracefs 不可用（tracing 内核是否真的起来了？）"; RC=1
    fi
fi

if [ -n "$TDIR" ]; then
    echo "[3] 可用 tracer"
    info "available_tracers : $(cat "$TDIR/available_tracers" 2>/dev/null)"
    for t in osnoise timerlat; do
        if grep -qw "$t" "$TDIR/available_tracers" 2>/dev/null; then
            ok "$t tracer 可用"
        else
            bad "$t tracer 不可用"; RC=1
        fi
    done

    echo "[4] timerlat 相关文件"
    for f in current_tracer tracing_on trace trace_options; do
        [ -e "$TDIR/$f" ] && info "$TDIR/$f 存在" || info "$TDIR/$f 缺失"
    done
    info "osnoise 事件目录：$(ls -d "$TDIR/events/osnoise" 2>/dev/null || echo 无)"
fi

# ---------- [5] rtla ----------
echo "[5] rtla"
if command -v rtla >/dev/null 2>&1; then
    ok "rtla 已安装：$(command -v rtla)"
    info "$(rtla --version 2>&1 | head -2)"
else
    info "rtla 未安装 —— 将直接用 tracefs 操作（不需要 rtla）"
fi

# ---------- [6] 模块能否加载（用新树编的 kmod）----------
echo "[6] 模块加载能力"
info "模块目录：$(ls -d /lib/modules/"$KREL" 2>/dev/null || echo "缺失（modules_install 没跑？）")"
if [ -d "/lib/modules/$KREL" ]; then
    n=$(find "/lib/modules/$KREL" -name '*.ko' 2>/dev/null | wc -l)
    ok "/lib/modules/$KREL 下有 $n 个 ko"
else
    bad "/lib/modules/$KREL 缺失 —— 我们的 kmod 无法 insmod"; RC=1
fi
# 尝试加载一个无害的小模块验证 insmod 通路
if modinfo -k "$KREL" 2>/dev/null | head -1 >/dev/null 2>&1; then
    info "modinfo 可用"
fi

# ---------- [7] timerlat 短采样（原始留存，不做结论）----------
echo "[7] timerlat 短采样（3 秒，原始输出留存）"
OUT=/root/pr10e_stageC
mkdir -p "$OUT"
if [ -n "$TDIR" ] && grep -qw timerlat "$TDIR/available_tracers" 2>/dev/null; then
    # 清空并用 timerlat tracer；用 tracefs 原生接口，不依赖 rtla
    echo 0 > "$TDIR/tracing_on" 2>/dev/null
    echo > "$TDIR/trace" 2>/dev/null
    echo 1 > "$TDIR/events/osnoise/enable" 2>/dev/null
    if echo timerlat > "$TDIR/current_tracer" 2>/dev/null; then
        echo 1 > "$TDIR/tracing_on" 2>/dev/null
        sleep 3
        echo 0 > "$TDIR/tracing_on" 2>/dev/null
        cp "$TDIR/trace" "$OUT/timerlat_raw.txt" 2>/dev/null
        echo 0 > "$TDIR/current_tracer" 2>/dev/null
        lines=$(wc -l < "$OUT/timerlat_raw.txt" 2>/dev/null || echo 0)
        if [ "$lines" -gt 1 ]; then
            ok "采到 $lines 行原始 trace：$OUT/timerlat_raw.txt"
            info "—— 前 8 行（格式实测，不预设）——"
            head -8 "$OUT/timerlat_raw.txt" | sed 's/^/      /'
            info "—— 关键词命中统计 ——"
            for kw in "IRQ" "Thread" "latency" "us" "ns"; do
                c=$(grep -c "$kw" "$OUT/timerlat_raw.txt" 2>/dev/null || echo 0)
                info "  $kw : $c 次"
            done
        else
            bad "timerlat 未产出有效数据（trace 空）"
            info "可能原因：需要 root、需 CONFIG_OSNOISE_TRACER 事件使能、或该内核 timerlat 需要 rtla"
            head -3 "$OUT/timerlat_raw.txt" 2>/dev/null | sed 's/^/      /'
            RC=1
        fi
    else
        bad "无法写入 current_tracer=timerlat"
        RC=1
    fi
else
    bad "跳过 timerlat 采样（tracer 不可用）"
fi

# ---------- [8] 系统信息快照 ----------
echo "[8] 系统快照"
{
    echo "date=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "uname=$(uname -a)"
    echo "nproc=$(nproc)"
    echo "governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"
    echo "sched_rt_runtime_us=$(cat /proc/sys/kernel/sched_rt_runtime_us 2>/dev/null)"
    echo "tracers=$(cat "$TDIR/available_tracers" 2>/dev/null)"
} > "$OUT/host.txt" 2>/dev/null
ok "快照：$OUT/host.txt"

echo
echo "=============================================================="
if [ $RC -eq 0 ]; then
    echo " 阶段 C 功能检查：通过"
else
    echo " 阶段 C 功能检查：有未通过项（见上 ✗）"
fi
echo " 说明：本脚本只验证「能不能用」，不给任何延迟结论。"
echo "       正式数字仍回 stable 内核采集（评审阶段 D 要求）。"
echo "=============================================================="
exit $RC
