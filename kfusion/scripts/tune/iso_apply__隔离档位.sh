#!/bin/bash
# ============================================================================
# iso_apply__隔离档位.sh — 按档位施加隔离，并可回退
# ============================================================================
# 为什么不重写一套：仓库里已经有 L1..L4 的历史实现（2026-10 在 Pi4 上逐项试出来的），
# 这里只做「按档位有序调用 + 统一留痕」，避免出现第二套隔离方案各自说自己的话。
#
# 档位定义（与本仓库 profile_soak_l4__深度隔离.env.sh 的口径一致）：
#   l0  = 只有启动参数（isolcpus/nohz_full/rcu_nocbs/irqaffinity）——本脚本不改任何运行时
#   l1  = l0 + 运行时调优：RT 节流关闭、cpuidle 全关、timer_migration=0、
#         cpu_dma_latency=0、workqueue unbound 排除探针核、停 irqbalance
#   l4  = l1 + 历史 L4 钩子链：iso_l4_extra（IRQ 亲和清场 / watchdog / stat_interval）
#         + iso_rtbubbles（cgroup v2 cpuset 独立 partition + RT 节流 + governor）
#
# 用法：
#   sudo bash iso_apply__隔离档位.sh l1|l4|teardown|status
#
# 注意：钩子一律用 `bash <文件>` 调用，且判据用 -f 不用 -x——仓库里这些脚本
# 多为 100644（无可执行位），用 -x 判断会让 L4 被静默跳过（踩过一次）。
#   env: PROBE_CPU=3  HK_CPUS=0-2
#
# fail-closed：l4 档位若启动参数缺 isolcpus（探针核未真正隔离），直接退出 3——
# 否则会得到一份「号称 L4、其实只在 L1」的数据。
# ============================================================================
set -u

ACTION="${1:-status}"
PROBE_CPU="${PROBE_CPU:-3}"
HK_CPUS="${HK_CPUS:-0-2}"
HK_MASK="${HK_MASK:-7}"          # 0-2 的位掩码
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
L4_EXTRA="$SELF_DIR/iso_l4_extra__深度隔离扩展.sh"
RTB="$SELF_DIR/iso_rtbubbles__cgroup分区.sh"
TUNE="$SELF_DIR/rt_host_tune__主机调优.sh"

die() { echo "✗ $*" >&2; exit "${2:-1}"; }

need_root() { [ "$(id -u)" = "0" ] || exec sudo -n bash "$0" "$@"; }

w() { [ -e "$2" ] && echo "$1" > "$2" 2>/dev/null; }

check_boot_args() {
    local cmd; cmd="$(cat /proc/cmdline)"
    case " $cmd " in
        *" isolcpus="*) : ;;
        *) die "启动参数缺 isolcpus（探针核未域级隔离）；拒绝在缺前提时声称 L4

  当前 cmdline: $cmd
  修法：/boot/firmware/cmdline.txt 加 isolcpus=domain,managed_irq,${PROBE_CPU} 后重启" 3 ;;
    esac
    local iso; iso="$(cat /sys/devices/system/cpu/isolated 2>/dev/null)"
    [ "$iso" = "$PROBE_CPU" ] || die "isolated=「$iso」≠ ${PROBE_CPU}：启动参数写了但未生效（需要重启）" 3
}

apply_l1() {
    echo "── L1 运行时调优 ──"
    if [ -f "$TUNE" ]; then
        bash "$TUNE" 2>&1 | sed 's/^/   /' | tail -6 || true
    else
        echo "   ⚠ 找不到 rt_host_tune，按等价项内联执行"
        echo 0 > /dev/null
    fi

    # RT 带宽节流：默认 950000µs/s，长跑（尤其 8h）一旦超支会被 throttle，
    # 表现为「周期性整段变慢」，那不是被测代码的性质，必须关。
    local prev; prev="$(cat /proc/sys/kernel/sched_rt_runtime_us 2>/dev/null || echo '?')"
    echo "$prev" > /tmp/iso_prev_rt_runtime
    w -1 /proc/sys/kernel/sched_rt_runtime_us \
        && echo "   sched_rt_runtime_us: $prev → -1"

    w 0 /proc/sys/kernel/timer_migration
    for tm in /sys/devices/system/cpu/cpu*/timer_migration; do w 0 "$tm"; done
    echo "   timer_migration=0（全局 + 每核）"

    for st in /sys/devices/system/cpu/cpu"${PROBE_CPU}"/cpuidle/state*/disable; do
        w 1 "$st"
    done
    echo "   CPU${PROBE_CPU} cpuidle 全关"

    w "$HK_MASK" /sys/devices/virtual/workqueue/cpumask
    local n=0 f
    for f in /sys/devices/virtual/workqueue/*/cpumask; do
        w "$HK_MASK" "$f" && n=$((n + 1))
    done
    echo "   workqueue cpumask → ${HK_CPUS}（全局 + ${n} 个队列）"

    systemctl stop irqbalance 2>/dev/null || true
    systemctl disable irqbalance 2>/dev/null || true

    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        w performance "$g"
    done
    echo "   governor → performance"
}

apply_l4() {
    echo "── L4 钩子链 ──"
    if [ -f "$L4_EXTRA" ]; then
        PROBE_CPU="$PROBE_CPU" HK_CPUS="$HK_CPUS" \
            bash "$L4_EXTRA" setup 2>&1 | sed 's/^/   /'
    else
        die "缺 $L4_EXTRA"
    fi
    # 顺序有意：先清 IRQ/后台源，再立 cgroup 分区。反过来做的话，
    # 分区建立后新迁入 housekeeping 的进程会重新把 IRQ 拉回来。
    if [ -f "$RTB" ]; then
        PROBE_CPU="$PROBE_CPU" HK_CPUS="$HK_CPUS" \
            bash "$RTB" setup 2>&1 | sed 's/^/   /'
    else
        die "缺 $RTB"
    fi
}

teardown() {
    echo "── 回退 ──"
    [ -f "$RTB" ] && PROBE_CPU="$PROBE_CPU" HK_CPUS="$HK_CPUS" \
        bash "$RTB" teardown 2>&1 | sed 's/^/   /'
    [ -f "$L4_EXTRA" ] && PROBE_CPU="$PROBE_CPU" HK_CPUS="$HK_CPUS" \
        bash "$L4_EXTRA" teardown 2>&1 | sed 's/^/   /'
    if [ -f /tmp/iso_prev_rt_runtime ]; then
        w "$(cat /tmp/iso_prev_rt_runtime)" /proc/sys/kernel/sched_rt_runtime_us
        rm -f /tmp/iso_prev_rt_runtime
    fi
    echo "   完成（启动参数级隔离需改 cmdline 并重启，脚本不动它）"
}

status() {
    echo "PROBE_CPU=${PROBE_CPU} HK_CPUS=${HK_CPUS}"
    echo -n "  cmdline isolcpus : "; grep -oE 'isolcpus=[^ ]+' /proc/cmdline || echo 无
    echo -n "  isolated         : "; cat /sys/devices/system/cpu/isolated 2>/dev/null
    echo -n "  rt_runtime_us    : "; cat /proc/sys/kernel/sched_rt_runtime_us
    echo -n "  workqueue cpumask: "; cat /sys/devices/virtual/workqueue/cpumask 2>/dev/null
    echo -n "  irqbalance       : "; systemctl is-active irqbalance 2>/dev/null || echo inactive
    echo -n "  cgroup 分区      : "
    if [ -d /sys/fs/cgroup/kfusion_iso ]; then
        cat /sys/fs/cgroup/kfusion_iso/cpuset.cpus.partition 2>/dev/null
    else
        echo 未建立
    fi
    echo -n "  IRQ 含 CPU${PROBE_CPU} 的个数: "
    local n=0 f
    for f in /proc/irq/*/smp_affinity_list; do
        case ",$(cat "$f" 2>/dev/null)," in *",${PROBE_CPU},"*) n=$((n + 1)) ;; esac
    done
    echo "$n"
}

main() {
    case "$ACTION" in
        l0) check_boot_args ;;
        l1) check_boot_args; apply_l1 ;;
        l4) check_boot_args; apply_l1; apply_l4 ;;
        teardown) teardown ;;
        status) status ;;
        *) echo "usage: $0 l0|l1|l4|teardown|status" >&2; exit 2 ;;
    esac
}

need_root "$@"
main "$@"
