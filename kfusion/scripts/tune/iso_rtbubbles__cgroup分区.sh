#!/bin/bash
# ============================================================================
# iso_rtbubbles__cgroup分区.sh — rt-bubbles 隔离方案移植（libisolation/setup_cgroup）
# ============================================================================
# 来源: https://github.com/1147426025lu-bot/rt-bubbles.git  libisolation/cgroup_control.c
# 原版机制（C 实现）:
#   1) sched_rt_runtime_us = -1            —— 关闭 RT 任务运行时节流
#   2) cpuset cgroup: housekeeping(0-2) + isolated(3)
#   3) cpuset.sched_load_balance = 0       —— 把 CPU3 踢出调度域
#   4) 全部进程迁入 housekeeping
#   5) cpufreq → performance
#
# 与原版的**一处偏差**（有意为之，已在论文口径注明）:
#   原版把根 cpuset.cpus 收窄为 0-2，用户态任务自行进入 isolated 组。
#   K-Fusion 的合体代码是**内核模块 kthread**（official_cycletest），由 kthreadd
#   在根 cgroup 生成，无法像用户任务那样预置进 isolated 组；若根 cpuset 收窄，
#   模块的 sched_setaffinity(CPU3) 会因 cpuset 交集为空而失败，kthread 反而落不到
#   隔离核。故本移植**不收窄根 cpuset**，域级隔离由启动参数 isolcpus=domain,
#   managed_irq,3 提供（强度等同），kthread 由模块自身显式亲和绑定 CPU3。
#
# 用法: iso_rtbubbles__cgroup分区.sh setup|teardown
#   env: PROBE_CPU(默认3) HK_CPUS(默认0-2)
# 兼容 cgroup v2（Debian trixie 默认）与 v1（原版 2022 bullseye）。
# ============================================================================
set -u

if [ "$(id -u)" != "0" ]; then
    exec sudo -n bash "$0" "$@"
fi

ACTION="${1:-}"
PROBE_CPU="${PROBE_CPU:-3}"
HK_CPUS="${HK_CPUS:-0-2}"
CG=/sys/fs/cgroup
HK_NAME="kfusion_hk"
ISO_NAME="kfusion_iso"
STATE="/tmp/iso_rtbubbles_state_cpu${PROBE_CPU}.sh"

w()   { if [ -e "$2" ]; then echo "$1" > "$2" 2>/dev/null; fi; }
record() {
    local key="$1" val="$2"
    if [ -f "$STATE" ] && grep -q "^${key}=" "$STATE"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$STATE"
    else
        echo "${key}=${val}" >> "$STATE"
    fi
}

rtb_setup() {
    : > "$STATE"
    record "RTB_PREV_RT_RUNTIME" "$(cat /proc/sys/kernel/sched_rt_runtime_us 2>/dev/null || echo '?')"

    # ---- 1) 关闭 RT 节流（原版第 1 步）----
    w -1 /proc/sys/kernel/sched_rt_runtime_us && echo "   [RTB] sched_rt_runtime_us → -1"

    # ---- 2/3/4) cpuset 分区 ----
    if [ -f "$CG/cgroup.controllers" ]; then
        # ===== cgroup v2（trixie）=====
        grep -qw cpuset "$CG/cgroup.controllers" || { echo "   [RTB] ⚠️ cpuset 控制器不可用"; return 0; }
        grep -qw cpuset "$CG/cgroup.subtree_control" 2>/dev/null || w +cpuset "$CG/cgroup.subtree_control"
        mkdir -p "$CG/$HK_NAME" "$CG/$ISO_NAME"
        echo "$HK_CPUS"   > "$CG/$HK_NAME/cpuset.cpus" 2>/dev/null
        echo "$PROBE_CPU" > "$CG/$ISO_NAME/cpuset.cpus" 2>/dev/null
        w 0 "$CG/$HK_NAME/cpuset.mems"; w 0 "$CG/$ISO_NAME/cpuset.mems"
        # v2 的分区语义等价于原版 sched_load_balance=0（CPU3 独立 partition root）
        w isolated "$CG/$ISO_NAME/cpuset.cpus.partition"
        ptype="$(cat "$CG/$ISO_NAME/cpuset.cpus.partition" 2>/dev/null)"
        echo "   [RTB] v2: $HK_NAME(cpus=$HK_CPUS) + $ISO_NAME(cpus=$PROBE_CPU, partition=$ptype)"
        # 松散进程迁入 housekeeping（跳过自身；失败即跳过——多为已受限的内核线程）
        local moved=0 p
        for p in $(cat "$CG/cgroup.procs" 2>/dev/null); do
            [ "$p" = "$$" ] && continue
            if echo "$p" > "$CG/$HK_NAME/cgroup.procs" 2>/dev/null; then
                moved=$((moved + 1))
            fi
        done
        echo "   [RTB] 迁移松散进程 → $HK_NAME: ${moved}"
    else
        # ===== cgroup v1（原版路径）=====
        [ -d "$CG/cpuset" ] || { mkdir -p "$CG/cpuset"; mount -t cgroup -o cpuset none "$CG/cpuset" 2>/dev/null || true; }
        mkdir -p "$CG/cpuset/$HK_NAME" "$CG/cpuset/$ISO_NAME"
        echo "$HK_CPUS"   > "$CG/cpuset/$HK_NAME/cpuset.cpus" 2>/dev/null
        echo "$PROBE_CPU" > "$CG/cpuset/$ISO_NAME/cpuset.cpus" 2>/dev/null
        w 0 "$CG/cpuset/$HK_NAME/cpuset.mems"; w 0 "$CG/cpuset/$ISO_NAME/cpuset.mems"
        w 0 "$CG/cpuset/$ISO_NAME/cpuset.sched_load_balance"
        record "RTB_ROOT_CPUS" "$(cat "$CG/cpuset/cpuset.cpus" 2>/dev/null || echo '?')"
        w 0 "$CG/cpuset/cpuset.sched_load_balance"
        local moved=0 p
        for p in $(cat "$CG/cpuset/cgroup.procs" 2>/dev/null); do
            [ "$p" = "$$" ] && continue
            if echo "$p" > "$CG/cpuset/$HK_NAME/cgroup.procs" 2>/dev/null; then
                moved=$((moved + 1))
            fi
        done
        echo "   [RTB] v1: housekeeping/isolated 就绪，迁移 ${moved} 进程（根 cpuset 未收窄，见文件头说明）"
    fi

    # ---- 5) cpufreq → performance（原版第 5 步；L1 已做，幂等重申）----
    local gov
    for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        [ -f "$gov" ] && w performance "$gov"
    done
    echo "   [RTB] cpufreq → performance"
}

rtb_teardown() {
    [ -f "$STATE" ] || { echo "   [RTB] 无状态文件，跳过还原"; return 0; }
    # shellcheck disable=SC1090
    . "$STATE"
    [ "${RTB_PREV_RT_RUNTIME:-}" != "" ] && [ "${RTB_PREV_RT_RUNTIME}" != "?" ] && \
        w "${RTB_PREV_RT_RUNTIME}" /proc/sys/kernel/sched_rt_runtime_us
    if [ -f "$CG/cgroup.controllers" ]; then
        [ -d "$CG/$ISO_NAME" ] && w member "$CG/$ISO_NAME/cpuset.cpus.partition" 2>/dev/null
        rm -rf "$CG/$ISO_NAME" "$CG/$HK_NAME" 2>/dev/null
    else
        [ "${RTB_ROOT_CPUS:-}" != "" ] && [ "${RTB_ROOT_CPUS}" != "?" ] && \
            w 1 "$CG/cpuset/cpuset.sched_load_balance"
        rm -rf "$CG/cpuset/$ISO_NAME" "$CG/cpuset/$HK_NAME" 2>/dev/null
    fi
    rm -f "$STATE"
    echo "   [RTB] 已还原"
}

case "$ACTION" in
    setup)    rtb_setup ;;
    teardown) rtb_teardown ;;
    *) echo "usage: $0 setup|teardown" >&2; exit 2 ;;
esac
