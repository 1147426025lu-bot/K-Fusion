#!/bin/bash
# CPU3 探针核隔离 — 分级 setup / teardown
# ISOLATION_LEVEL:
#   1 = 基础运行时调优（IRQ/workqueue/idle/dma）
#   2 = + 断网 + 静默服务（减少 backlog_napi / 蓝牙等）
#   3 = + cset shield（kthread 赶到 housekeeping 核）+ cgroup plcrt
set -uo pipefail

ACTION="${1:-setup}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="${STATE_FILE:-/tmp/plc_rt_isolation.state}"
AUTO_CSET_SHIELD="${AUTO_CSET_SHIELD:-0}"
DISABLE_RT_RUNTIME_THROTTLE="${DISABLE_RT_RUNTIME_THROTTLE:-1}"
DISABLE_GLOBAL_TIMER_MIGRATION="${DISABLE_GLOBAL_TIMER_MIGRATION:-1}"

PROBE_CPU="${JITTER_PROBE_CPU:-3}"
RT_NETDEV="${RT_NETDEV:-eth0}"
HK_MASK="${HOUSEKEEPING_CPU_MASK:-7}"          # CPU0-2
HK_CPUS="${HOUSEKEEPING_CPUS:-0-2}"
ISOLATION_LEVEL="${ISOLATION_LEVEL:-1}"

sudo_cmd() {
    if sudo -n true 2>/dev/null; then
        sudo -n "$@"
    else
        "$@"
    fi
}

write_if_possible() {
    local path="$1"
    local value="$2"
    if [ ! -e "$path" ]; then
        return 1
    fi
    echo "$value" | sudo_cmd tee "$path" >/dev/null 2>&1
}

save_state() {
    cat >"$STATE_FILE" <<EOF
ISOLATION_LEVEL=${ISOLATION_LEVEL}
ETH_QUIET=${ETH_QUIET:-0}
ETH_WAS_UP=${ETH_WAS_UP:-0}
CSET_SHIELD=${CSET_SHIELD:-0}
PREV_RT_RUNTIME_US=${PREV_RT_RUNTIME_US:-}
PREV_TIMER_MIGRATION=${PREV_TIMER_MIGRATION:-}
EOF
}

load_state() {
    # shellcheck disable=SC1090
    [ -f "$STATE_FILE" ] && source "$STATE_FILE"
}

level1_basic_tune() {
    echo "🛠️ [ISO-L1] 基础运行时调优 (housekeeping=CPU${HK_CPUS})..."

    local tune_script="${SCRIPT_DIR}/rt_host_tune__主机调优.sh"
    if [ -x "$tune_script" ]; then
        echo "   -> rt_host_tune（arch_timer→0-2, CPU${PROBE_CPU} cpuidle off）"
        bash "$tune_script" 2>/dev/null | sed 's/^/      /' || true
    fi

    if command -v systemctl >/dev/null 2>&1; then
        sudo_cmd systemctl stop irqbalance 2>/dev/null || true
        sudo_cmd systemctl disable irqbalance 2>/dev/null || true
    fi

    if [ "$DISABLE_RT_RUNTIME_THROTTLE" = "1" ] && [ -r /proc/sys/kernel/sched_rt_runtime_us ]; then
        PREV_RT_RUNTIME_US="$(cat /proc/sys/kernel/sched_rt_runtime_us 2>/dev/null || true)"
        echo "   sched_rt_runtime_us=-1 (禁用 RT 带宽节流)"
        write_if_possible /proc/sys/kernel/sched_rt_runtime_us -1 || true
    fi

    echo "   cpu_dma_latency=0"
    write_if_possible /dev/cpu_dma_latency 0 || echo 0 | tee /dev/cpu_dma_latency >/dev/null 2>&1 || true

    echo "   禁用 CPU${PROBE_CPU} cpuidle"
    for state in /sys/devices/system/cpu/cpu"${PROBE_CPU}"/cpuidle/state*/disable; do
        [ -f "$state" ] && write_if_possible "$state" 1 || true
    done

    if [ "$DISABLE_GLOBAL_TIMER_MIGRATION" = "1" ] && [ -r /proc/sys/kernel/timer_migration ]; then
        PREV_TIMER_MIGRATION="$(cat /proc/sys/kernel/timer_migration 2>/dev/null || true)"
        echo "   全局 timer_migration=0"
        write_if_possible /proc/sys/kernel/timer_migration 0 || true
    fi

    echo "   timer_migration=0 (all CPUs)"
    for tm in /sys/devices/system/cpu/cpu*/timer_migration; do
        write_if_possible "$tm" 0 || true
    done

    if grep -q "rcu_nocbs=${PROBE_CPU}" /proc/cmdline 2>/dev/null; then
        echo "   ✅ rcu_nocbs=${PROBE_CPU}"
    else
        echo "   ⚠️ rcu_nocbs=${PROBE_CPU} 未在 cmdline 中"
    fi

    if grep -q "isolcpus=" /proc/cmdline 2>/dev/null; then
        echo "   ✅ isolcpus 已配置: $(grep -oE 'isolcpus=[^ ]+' /proc/cmdline)"
    else
        echo "   ⚠️ isolcpus 未配置"
    fi

    if grep -q "nohz_full=${PROBE_CPU}" /proc/cmdline 2>/dev/null; then
        echo "   ✅ nohz_full=${PROBE_CPU}"
    elif grep -q 'CONFIG_NO_HZ_FULL=y' /boot/config-"$(uname -r)" 2>/dev/null; then
        echo "   ⚠️ 内核支持 CONFIG_NO_HZ_FULL，但 cmdline 未指定 nohz_full=${PROBE_CPU}"
    else
        echo "   ⚠️ 内核未启用 CONFIG_NO_HZ_FULL，nohz_full= 效果有限"
    fi

    echo "   全局 workqueue cpumask=${HK_MASK}"
    write_if_possible /sys/devices/virtual/workqueue/cpumask "$HK_MASK" || true

    echo "   子 workqueue cpumask → ${HK_MASK}"
    local wq n=0
    for wq in /sys/devices/virtual/workqueue/*/cpumask; do
        write_if_possible "$wq" "$HK_MASK" && n=$((n + 1)) || true
    done
    echo "      -> 已设置 ${n} 个子队列"

    echo "   迁移可写 IRQ → CPU${HK_CPUS}"
    write_if_possible /proc/irq/default_smp_affinity "$HK_MASK" || true
    local irq moved=0 blocked=0
    for irq in /proc/irq/*/smp_affinity; do
        if write_if_possible "$irq" "$HK_MASK"; then
            moved=$((moved + 1))
        else
            blocked=$((blocked + 1))
        fi
    done
    echo "      -> 成功 ${moved}，不可迁移 ${blocked}（含 arch_timer 等 per-CPU IRQ）"

    if [ -d "/sys/class/net/${RT_NETDEV}" ]; then
        local q
        for q in "/sys/class/net/${RT_NETDEV}/queues/rx-"*/rps_cpus; do
            [ -f "$q" ] && write_if_possible "$q" "$HK_MASK" || true
        done
        echo "   ${RT_NETDEV} 全部 RX 队列 RPS → ${HK_MASK}"
        if [ "${RT_ETH_ETHTOOL_COMBINED:-1}" = "1" ] && command -v ethtool >/dev/null 2>&1; then
            if sudo_cmd ethtool -L "$RT_NETDEV" combined 1 2>/dev/null; then
                echo "   ethtool -L ${RT_NETDEV} combined 1（单队列，减 CPU${PROBE_CPU} NAPI）"
            fi
        fi
    fi

    if ! grep -qE 'workqueue\.cpumask=7' /proc/cmdline 2>/dev/null; then
        echo "   ⚠️ cmdline 无 workqueue.cpumask=7（已写 sysfs；重启可加: workqueue.cpumask=7）"
    fi

    write_if_possible /proc/sys/kernel/watchdog_thresh 120 || true
    write_if_possible /proc/sys/kernel/nmi_watchdog 0 || true
    write_if_possible /proc/sys/vm/dirty_writeback_centisecs 60000 || true
    write_if_possible /proc/sys/vm/dirty_expire_centisecs 60000 || true
    if [ -w /proc/sys/kernel/housekeeping_cpus ]; then
        echo "$HK_CPUS" | sudo_cmd tee /proc/sys/kernel/housekeeping_cpus >/dev/null 2>&1 || true
        echo "   housekeeping_cpus=${HK_CPUS}"
    fi

    if [ "${RT_CPUFREQ_PERFORMANCE:-0}" = "1" ]; then
        local cpu gov
        echo "   cpufreq → performance"
        for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
            [ -f "$gov" ] && write_if_possible "$gov" performance || true
        done
    fi

    if [ -f "/sys/devices/system/cpu/cpu${PROBE_CPU}/cpufreq/scaling_min_freq" ] && \
       [ -f "/sys/devices/system/cpu/cpu${PROBE_CPU}/cpufreq/scaling_max_freq" ]; then
        local maxf
        maxf="$(cat "/sys/devices/system/cpu/cpu${PROBE_CPU}/cpufreq/scaling_max_freq" 2>/dev/null || true)"
        if [ -n "$maxf" ]; then
            write_if_possible "/sys/devices/system/cpu/cpu${PROBE_CPU}/cpufreq/scaling_min_freq" "$maxf" || true
            echo "   CPU${PROBE_CPU} min_freq=max_freq (${maxf})"
        fi
    fi

    write_if_possible /proc/sys/kernel/sched_migration_cost_ns 5000000 || true
    write_if_possible /proc/sys/kernel/numa_balancing 0 2>/dev/null || true

    migrate_movable_tasks_off_probe
}

# 将可迁移任务从探针核赶到 housekeeping（per-CPU 内核线程跳过）
migrate_movable_tasks_off_probe() {
    local pid comm moved=0 skipped=0
    echo "   迁离 CPU${PROBE_CPU} 上可迁移任务 → CPU${HK_CPUS}..."
    while read -r pid comm; do
        [ -z "$pid" ] && continue
        case "$comm" in
            migration/*|rcu_*|rcuc/*|ksoftirqd/*|cpuhp/*|irq_work/*|backlog_napi/*)
                skipped=$((skipped + 1))
                continue
                ;;
        esac
        if sudo_cmd taskset -cp "$HK_CPUS" "$pid" >/dev/null 2>&1; then
            moved=$((moved + 1))
        else
            skipped=$((skipped + 1))
        fi
    done < <(ps -eLo pid=,psr=,comm= 2>/dev/null | awk -v c="$PROBE_CPU" '$2 == c {print $1,$3}')
    echo "      -> 已迁移 ${moved}，保留/失败 ${skipped}（含 kworker 等 per-CPU）"
}

level1_quiet_services() {
    echo "🔇 [ISO-L1b] 静默可能打扰 CPU${PROBE_CPU} 的用户态服务..."
    if command -v systemctl >/dev/null 2>&1; then
        for svc in irqbalance bluetooth triggerhappy hciuart avahi-daemon; do
            sudo_cmd systemctl stop "$svc" 2>/dev/null || true
            sudo_cmd systemctl disable "$svc" 2>/dev/null || true
        done
    fi
    if command -v rfkill >/dev/null 2>&1; then
        sudo_cmd rfkill block bluetooth 2>/dev/null || true
    fi
}

level2_network_quiet() {
    ETH_QUIET=0
    ETH_WAS_UP=0
    if [ ! -d "/sys/class/net/${RT_NETDEV}" ]; then
        echo "ℹ️ [ISO-L2] 无 ${RT_NETDEV}，跳过断网"
        return 0
    fi
    if ip link show "$RT_NETDEV" 2>/dev/null | grep -q "state UP"; then
        ETH_WAS_UP=1
    fi
    echo "🔇 [ISO-L2] 压测期间关闭 ${RT_NETDEV}（减少 backlog_napi/${PROBE_CPU}）"
    sudo_cmd ip link set dev "$RT_NETDEV" down || true
    echo "   压低 softnet budget（减轻 NAPI 软中断占用）"
    write_if_possible /proc/sys/net/core/netdev_budget 1 || true
    write_if_possible /proc/sys/net/core/netdev_budget_usecs 2000 || true
    write_if_possible /proc/sys/net/core/dev_weight 1 || true
    for dev in /sys/block/*/queue/read_ahead_kb; do
        write_if_possible "$dev" 0 2>/dev/null || true
    done
    echo "   block read_ahead_kb → 0（减轻 kblockd 干扰）"
    ETH_QUIET=1
}

level2_network_restore() {
    if [ "${ETH_QUIET:-0}" = 1 ] && [ "${ETH_WAS_UP:-0}" = 1 ]; then
        echo "🔊 [ISO-L2] 恢复 ${RT_NETDEV}"
        sudo_cmd ip link set dev "$RT_NETDEV" up || true
    fi
}

level3_cset_shield() {
    CSET_SHIELD=0
    if ! command -v cset >/dev/null 2>&1; then
        echo "⚠️ [ISO-L3] 未安装 cset，跳过 shield"
        return 0
    fi
    echo "🛡️ [ISO-L3] cset shield cpu=${PROBE_CPU} kthread=on"
    sudo_cmd cset shield --reset 2>/dev/null || true
    if sudo_cmd cset shield --cpu="${PROBE_CPU}" --kthread=on --force; then
        CSET_SHIELD=1
    else
        echo "   ⚠️ cset shield 创建失败"
    fi
}

level3_cset_restore() {
    if [ "${CSET_SHIELD:-0}" = 1 ] && command -v cset >/dev/null 2>&1; then
        echo "🛡️ [ISO-L3] cset shield --reset"
        sudo_cmd cset shield --reset 2>/dev/null || true
    fi
}

report_cpu3_occupants() {
    local col="CPU${PROBE_CPU}"
    echo "📋 CPU${PROBE_CPU} 当前占用（per-CPU 线程无法迁走）："
    ps -eLo pid,psr,comm 2>/dev/null | awk -v cpu="$PROBE_CPU" '$2 == cpu {print "   PID=" $1, $3}' | head -15
    echo "   arch_timer 中断（探针核本地定时器，无法禁）："
    grep arch_timer /proc/interrupts 2>/dev/null | head -1 || true
    echo "   CPU${PROBE_CPU} 非零 IRQ（采样对比用）："
    awk -v c="$col" '
        NR==1 { for (i=1;i<=NF;i++) if ($i==c) col=i; next }
        col && $(col)+0>0 { printf "   %s\n", $0 }
    ' /proc/interrupts 2>/dev/null | head -12
}

auto_enable_l3_if_possible() {
    if [ "$ISOLATION_LEVEL" -ge 3 ] && [ "$AUTO_CSET_SHIELD" != "1" ]; then
        if command -v cset >/dev/null 2>&1; then
            echo "ℹ️ 检测到 cset，建议 AUTO_CSET_SHIELD=1（当前未开）"
        fi
    fi
    if [ "$ISOLATION_LEVEL" -ge 3 ] && [ "$AUTO_CSET_SHIELD" = "1" ] && ! command -v cset >/dev/null 2>&1; then
        echo "⚠️ ISOLATION_LEVEL=3 但未安装 cpuset/cset，降级为 L2"
        ISOLATION_LEVEL=2
    fi
}

do_setup() {
    echo "=== CPU${PROBE_CPU} 隔离 setup (level=${ISOLATION_LEVEL}) ==="
    auto_enable_l3_if_possible
    level1_basic_tune
    level1_quiet_services

    if [ "$ISOLATION_LEVEL" -ge 2 ]; then
        level2_network_quiet
    fi

    if [ "$ISOLATION_LEVEL" -ge 3 ]; then
        if [ "$AUTO_CSET_SHIELD" = "1" ]; then
            level3_cset_shield
        else
            echo "ℹ️ [ISO-L3] 设 AUTO_CSET_SHIELD=1 以启用 cset shield"
        fi
    fi

    save_state
    report_cpu3_occupants
    echo "✅ 隔离 setup 完成 (level=${ISOLATION_LEVEL})"
}

do_teardown() {
    load_state
    echo "=== CPU${PROBE_CPU} 隔离 teardown (level=${ISOLATION_LEVEL:-1}) ==="

    if [ "${ISOLATION_LEVEL:-1}" -ge 3 ] || [ "${CSET_SHIELD:-0}" = 1 ]; then
        level3_cset_restore
    fi
    if [ "${ISOLATION_LEVEL:-1}" -ge 2 ] || [ "${ETH_QUIET:-0}" = 1 ]; then
        level2_network_restore
    fi

    if [ -n "${PREV_RT_RUNTIME_US:-}" ]; then
        write_if_possible /proc/sys/kernel/sched_rt_runtime_us "$PREV_RT_RUNTIME_US" || true
    fi
    if [ -n "${PREV_TIMER_MIGRATION:-}" ]; then
        write_if_possible /proc/sys/kernel/timer_migration "$PREV_TIMER_MIGRATION" || true
    fi

    rm -f "$STATE_FILE"
    echo "✅ 隔离 teardown 完成"
}

case "$ACTION" in
    setup) do_setup ;;
    teardown) do_teardown ;;
    refresh)
        load_state 2>/dev/null || true
        echo "=== CPU${PROBE_CPU} 隔离 refresh (re-apply L1${ISOLATION_LEVEL:-2}+) ==="
        level1_basic_tune
        level1_quiet_services
        if [ "${ISOLATION_LEVEL:-2}" -ge 2 ]; then
            level2_network_quiet
        fi
        report_cpu3_occupants
        ;;
    max)
        ISOLATION_LEVEL=3
        AUTO_CSET_SHIELD=1
        export ISOLATION_LEVEL AUTO_CSET_SHIELD
        do_setup
        ;;
    report) report_cpu3_occupants ;;
    *)
        echo "用法: $0 {setup|teardown|report}"
        exit 1
        ;;
esac
