#!/bin/bash
# ============================================================================
# env_setup__测量环境.sh — OFFICIAL cyclictest 测量环境初始化
# ============================================================================
# 功能: source PLC_PROFILE；提供 env_setup_host / env_pre_measure_idle
# 用法: source scripts/deploy/env_setup__测量环境.sh && env_setup_host
# ============================================================================
set -euo pipefail

_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DEPLOY_PROFILES="$_DEPLOY_DIR/profiles"
_TUNE_DIR="$_DEPLOY_DIR/../tune"
# shellcheck source=../plc_fusion_common__公共库.sh
source "$_DEPLOY_DIR/../plc_fusion_common__公共库.sh"
# shellcheck source=../platform/plc_source_platform__加载平台.sh
source "$_DEPLOY_DIR/../platform/plc_source_platform__加载平台.sh"

if [ -n "${PLC_PROFILE:-}" ] && [[ "$PLC_PROFILE" != /* ]] && [ ! -f "$PLC_PROFILE" ]; then
    if [ -f "$_DEPLOY_PROFILES/$PLC_PROFILE" ]; then
        PLC_PROFILE="$_DEPLOY_PROFILES/$PLC_PROFILE"
    elif [ -f "$_DEPLOY_DIR/$PLC_PROFILE" ]; then
        PLC_PROFILE="$_DEPLOY_DIR/$PLC_PROFILE"
    elif [ -f "$_DEPLOY_DIR/../$PLC_PROFILE" ]; then
        PLC_PROFILE="$_DEPLOY_DIR/../$PLC_PROFILE"
    fi
    export PLC_PROFILE
fi

# 先 light 默认，再 PLC_PROFILE 覆盖（避免 profile_soak L2 被 light 的 L3 盖掉）
if [ -f "$_DEPLOY_PROFILES/profile_light__默认测量.env.sh" ]; then
    # shellcheck source=profiles/profile_light__默认测量.env.sh
    source "$_DEPLOY_PROFILES/profile_light__默认测量.env.sh"
elif [ -f "$_DEPLOY_DIR/profile_light__默认测量.env.sh" ]; then
    # shellcheck source=profile_light__默认测量.env.sh
    source "$_DEPLOY_DIR/profile_light__默认测量.env.sh"
else
    plc_warn "未找到 profile_light__默认测量.env.sh，使用内置默认"
fi

if [ -n "${PLC_PROFILE:-}" ]; then
    if [ -f "$PLC_PROFILE" ]; then
        # shellcheck source=/dev/null
        source "$PLC_PROFILE"
    else
        plc_warn "PLC_PROFILE 文件不存在: $PLC_PROFILE" \
            "忽略并继续使用 light_profile"
    fi
fi

# 任意 ≥1 分钟的正式测量必须与短测一致（仅 profile 未设置时回退）
export RT_CPUFREQ_PERFORMANCE="${RT_CPUFREQ_PERFORMANCE:-1}"
export PRE_IDLE_SEC="${PRE_IDLE_SEC:-90}"
export ISOLATION_LEVEL="${ISOLATION_LEVEL:-3}"
export AUTO_CSET_SHIELD="${AUTO_CSET_SHIELD:-1}"
export PLC_CGROUP="${PLC_CGROUP:-1}"
export RT_ETH_ETHTOOL_COMBINED="${RT_ETH_ETHTOOL_COMBINED:-1}"

env_check_cmdline() {
    local cmd
    cmd="$(tr ' ' '\n' </proc/cmdline)"
    echo "=== cmdline 探针隔离检查 (CPU${JITTER_PROBE_CPU:-3}) ==="
    grep -qE '^isolcpus=' <<<"$cmd" && echo "   ✅ isolcpus" || echo "   ⚠️ 缺 isolcpus"
    grep -qE '^nohz_full=.*\b'"${JITTER_PROBE_CPU:-3}"'\b' <<<"$cmd" && echo "   ✅ nohz_full" || \
        grep -q "nohz_full=${JITTER_PROBE_CPU:-3}" /proc/cmdline && echo "   ✅ nohz_full" || \
        echo "   ⚠️ 缺 nohz_full=${JITTER_PROBE_CPU:-3}"
    grep -qE '^rcu_nocbs=' <<<"$cmd" && echo "   ✅ rcu_nocbs" || echo "   ⚠️ 缺 rcu_nocbs"
    if grep -qE 'workqueue\.cpumask=7' /proc/cmdline; then
        echo "   ✅ workqueue.cpumask=7 (cmdline)"
    else
        echo "   ⚠️ 建议 cmdline 增加: workqueue.cpumask=7  (运行时 L1 仍会写 sysfs)"
    fi
}

env_setup_plc_cgroup() {
    if [ "${PLC_CGROUP:-0}" = "1" ]; then
        bash "$_TUNE_DIR/rt_cgroup_plc__cgroup隔离.sh" setup || true
    fi
}

env_setup_host() {
    export DISABLE_RT_RUNTIME_THROTTLE=1
    export DISABLE_GLOBAL_TIMER_MIGRATION=1
    env_check_cmdline
    export RT_TUNE_USE_ISOLATE=1
    if [ ! -f "$_TUNE_DIR/rt_host_isolate__CPU隔离.sh" ]; then
        plc_warn "缺少 $_TUNE_DIR/rt_host_isolate__CPU隔离.sh" \
            "跳过 CPU 隔离（测量结果可能变差）"
    elif ! bash "$_TUNE_DIR/rt_host_isolate__CPU隔离.sh" setup; then
        plc_warn "rt_host_isolate__CPU隔离.sh setup 失败" \
            "检查 root 权限与 isolcpus 内核参数"
    fi
    env_setup_plc_cgroup
}

env_mark_setup_done() {
    export ENV_SETUP_DONE=1
}

env_teardown_host() {
    bash "$_TUNE_DIR/rt_host_unisolate__解除隔离.sh" 2>/dev/null || true
}

env_pre_measure_idle() {
    local sec="${1:-$PRE_IDLE_SEC}"
    if [ "$sec" -gt 0 ] 2>/dev/null; then
        echo "=== 测量前空闲 ${sec}s（PRE_IDLE，隔离已生效）==="
        sleep "$sec"
    fi
}

env_quiesce_background() {
    if [ "${SOAK_QUIESCE_TIMERS:-1}" != "1" ]; then
        return 0
    fi
    echo "=== 静默后台 timer/服务（SOAK_QUIESCE）==="
    if command -v systemctl >/dev/null 2>&1; then
        for svc in cron anacron apt-daily.timer apt-daily-upgrade.timer \
            man-db.timer logrotate.timer fstrim.timer systemd-tmpfiles-clean.timer \
            apt-listchanges.timer rpi-eeprom-update.timer; do
            sudo -n systemctl stop "$svc" 2>/dev/null || true
        done
        sudo -n systemctl stop unattended-upgrades 2>/dev/null || true
        sudo -n systemctl stop apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
    fi
    sync 2>/dev/null || true
}

env_pre_insmod_stabilize() {
    local drain="${DRAIN_SEC:-8}"
    if [ -f "$_TUNE_DIR/rt_host_isolate__CPU隔离.sh" ] && \
       [ "${PRE_INSMOD_REFRESH:-1}" = "1" ]; then
        echo "=== insmod 前隔离 refresh（re-apply L${ISOLATION_LEVEL}）==="
        bash "$_TUNE_DIR/rt_host_isolate__CPU隔离.sh" refresh 2>&1 | tail -8 || true
    fi
    env_quiesce_background || true
    if [ "$drain" -gt 0 ] 2>/dev/null; then
        echo "=== insmod 前稳定 ${drain}s（DRAIN_SEC）==="
        sleep "$drain"
    fi
}

env_report_probe_cpu_irq() {
    local cpu="${JITTER_PROBE_CPU:-3}"
    echo "=== CPU${cpu} 中断列（测量前快照）==="
    awk -v c="CPU${cpu}" '
        NR==1 { for (i=1;i<=NF;i++) if ($i==c) col=i; next }
        col && $(col)+0>0 { print "   "$0 }
    ' /proc/interrupts | head -20
}
