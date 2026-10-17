#!/bin/bash
# ============================================================================
# iso_l4_extra__深度隔离扩展.sh — L4 深度隔离扩展（由 rt_host_isolate 钩子调用）
# ============================================================================
# 背景（Pi4B 实测，2026-10-16）：L1+L2 之后 15min 浸泡仍出现 104µs / 268µs 单发
# 尖峰。cpu3_monitor 快照显示 CPU3 仍被以下源打扰：
#   - 桌面全家桶（lightdm/kanshi/polkit-mate/PulseAudio/mpris-proxy）线程可调度到探针核
#   - 设备 IRQ 亲和未清场（irq/48-vc4 线程落在 CPU3；vc4/vchiq/mmc/wlan 均为潜在源）
#   - unbound kworker 池默认全 CPU 可再生（thermal_events/cgroup_free 反复出现在 CPU3）
#   - softlockup watchdog 每 CPU 周期 IRQ work；vm.stat_interval 默认过短
# 本脚本在 setup 时做 4 件事，并把原值记录到状态文件供 teardown 还原：
#   1) 停显示管理器/桌面（graphical.target → multi-user.target）
#   2) 设备 IRQ 亲和清场 → housekeeping CPU（写失败的 per-CPU PPI/IPI 自动跳过）
#   3) workqueue unbound cpumask → housekeeping CPU
#   4) softlockup watchdog 关闭 + vm.stat_interval 放大
# 用法: iso_l4_extra__深度隔离扩展.sh setup|teardown
#   env: PROBE_CPU(默认3) HK_CPUS(默认0-2)  —— 由 rt_host_isolate 透传
# 依赖 root（rt_host_isolate 内经 sudo_cmd 调用）。
# ============================================================================
set -u

# 自我提权: 钩子由 rt_host_isolate 以普通用户调用，写 IRQ/sysfs 需要 root
# （依赖 sudoers NOPASSWD；与脚本内 sudo_cmd 同一前提）。
if [ "$(id -u)" != "0" ]; then
    exec sudo -n bash "$0" "$@"
fi

ACTION="${1:-}"
PROBE_CPU="${PROBE_CPU:-3}"
HK_CPUS="${HK_CPUS:-0-2}"
STATE="/tmp/iso_l4_state_cpu${PROBE_CPU}.sh"

has() { [ -e "$1" ]; }
w()   { if has "$2"; then echo "$1" > "$2" 2>/dev/null; fi; }

record() { # 追加记录（幂等：同 key 覆盖旧值）
    local key="$1" val="$2"
    if [ -f "$STATE" ] && grep -q "^${key}=" "$STATE"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$STATE"
    else
        echo "${key}=${val}" >> "$STATE"
    fi
}

l4_setup() {
    : > "$STATE"
    echo "   [L4] 状态文件: $STATE"

    # ---- 1) 停桌面 ----
    if command -v systemctl >/dev/null 2>&1; then
        local cur
        cur="$(systemctl get-default 2>/dev/null || true)"
        record "L4_PREV_DEFAULT" "${cur:-multi-user.target}"
        if [ "$cur" = "graphical.target" ]; then
            echo "   [L4] 停桌面: graphical.target → multi-user.target"
            systemctl isolate multi-user.target 2>/dev/null || true
        fi
        local dm
        for dm in lightdm gdm sddm; do
            systemctl stop "$dm" 2>/dev/null || true
        done
    fi

    # ---- 2) 设备 IRQ 亲和清场 ----
    local f irq cur moved=0 kept=0 failed=0
    for f in /proc/irq/*/smp_affinity_list; do
        irq="$(echo "$f" | awk -F/ '{print $4}')"
        case "$irq" in ''|*[!0-9]*) continue ;; esac
        cur="$(cat "$f" 2>/dev/null)" || continue
        case ",$cur," in *",$HK_CPUS,"*) kept=$((kept + 1)); continue ;; esac
        record "L4_IRQ_${irq}" "$cur"
        if echo "$HK_CPUS" > "$f" 2>/dev/null; then
            moved=$((moved + 1))
        else
            failed=$((failed + 1))   # per-CPU PPI/IPI（arch_timer 等）写不进，属预期
        fi
    done
    echo "   [L4] IRQ 亲和 → CPU${HK_CPUS}: 迁移 ${moved}，已合规 ${kept}，per-CPU 跳过 ${failed}"

    # ---- 3) workqueue unbound cpumask ----
    if has /sys/devices/virtual/workqueue/cpumask; then
        record "L4_WQ_CPUMASK" "$(cat /sys/devices/virtual/workqueue/cpumask 2>/dev/null || echo '?')"
        if echo "$HK_CPUS" > /sys/devices/virtual/workqueue/cpumask 2>/dev/null; then
            echo "   [L4] workqueue unbound cpumask → ${HK_CPUS}"
        fi
    fi

    # ---- 4) watchdog / stat_interval ----
    if has /proc/sys/kernel/watchdog; then
        record "L4_WATCHDOG" "$(cat /proc/sys/kernel/watchdog)"
        w 0 /proc/sys/kernel/watchdog && echo "   [L4] softlockup watchdog → 0"
    fi
    if has /proc/sys/vm/stat_interval; then
        record "L4_STAT_INTERVAL" "$(cat /proc/sys/vm/stat_interval)"
        w 60 /proc/sys/vm/stat_interval && echo "   [L4] vm.stat_interval → 60"
    fi
}

l4_teardown() {
    [ -f "$STATE" ] || { echo "   [L4] 无状态文件，跳过还原"; return 0; }
    # shellcheck disable=SC1090
    . "$STATE"
    local f key irq val
    # 还原 IRQ 亲和
    for f in /proc/irq/*/smp_affinity_list; do
        irq="$(echo "$f" | awk -F/ '{print $4}')"
        case "$irq" in ''|*[!0-9]*) continue ;; esac
        key="L4_IRQ_${irq}"
        val="$(eval "echo \${${key}:-}" 2>/dev/null)" || true
        [ -n "$val" ] && echo "$val" > "$f" 2>/dev/null
    done
    [ "${L4_WQ_CPUMASK:-}" != "" ] && [ "${L4_WQ_CPUMASK}" != "?" ] && \
        echo "${L4_WQ_CPUMASK}" > /sys/devices/virtual/workqueue/cpumask 2>/dev/null
    [ "${L4_WATCHDOG:-}" != "" ] && w "${L4_WATCHDOG}" /proc/sys/kernel/watchdog
    [ "${L4_STAT_INTERVAL:-}" != "" ] && w "${L4_STAT_INTERVAL}" /proc/sys/vm/stat_interval
    if [ "${L4_PREV_DEFAULT:-}" = "graphical.target" ] && command -v systemctl >/dev/null 2>&1; then
        echo "   [L4] 恢复桌面: graphical.target"
        systemctl isolate graphical.target 2>/dev/null || true
    fi
    rm -f "$STATE"
    echo "   [L4] 已还原"
}

case "$ACTION" in
    setup)    l4_setup ;;
    teardown) l4_teardown ;;
    *) echo "usage: $0 setup|teardown" >&2; exit 2 ;;
esac
