# ============================================================================
# 角色: SWAPPABLE — 测量 profile
# profile_soak_l4__深度隔离.env.sh — L4 深度隔离浸泡测（在 L2 之上叠加扩展钩子）
# ============================================================================
# 与 profile_soak_l2_best 测量协议相同（1kHz fused cyclictest，无背景负载），
# 但通过 ISO_L4_HOOK 叠加 L4 深度隔离（停桌面 / 设备 IRQ 亲和清场 /
# workqueue cpumask 封锁 unbound kworker / watchdog+stat_interval 调优），
# 并默认开启 **全量样本导出**（stride=1）——默认 stride=50 抽稀会系统性漏掉
# 「1/90 万」量级单发尖峰（2026-10-16 实测漏掉 268µs 尖峰），论文对比必须全量。
#
# 启动参数级隔离（需重启，单独做，不进本 profile）：
#   isolcpus=domain,managed_irq,3 nohz_full=3 rcu_nocbs=3 rcu_nocb_poll irqaffinity=0-2
#
# 用法:
#   DURATION_MIN=15 \
#   PLC_PROFILE=scripts/deploy/profiles/profile_soak_l4__深度隔离.env.sh \
#     bash scripts/deploy/run_soak_cycletest__浸泡长测.sh
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=profile_soak_l2_best__安静浸泡.env.sh
source "$SCRIPT_DIR/profile_soak_l2_best__安静浸泡.env.sh"

export RUNNER_PROFILE=fused_soak_l4
# >>> L4 深度隔离钩子链（由 rt_host_isolate 在 setup/teardown 时透传执行）：
#     1) iso_l4_extra     —— 停桌面 / 设备 IRQ 亲和清场 / watchdog / stat_interval
#     2) iso_rtbubbles    —— rt-bubbles 隔离方案移植（cgroup 分区 + RT 节流关闭 + governor）
#     teardown 按相反顺序还原
export ISO_L4_HOOK="$SCRIPT_DIR/../../tune/iso_l4_extra__深度隔离扩展.sh:$SCRIPT_DIR/../../tune/iso_rtbubbles__cgroup分区.sh"

# >>> 全量样本导出（容量 >= 1h@1kHz 周期数；vmalloc 32MB，Pi4 充足）
export FUSED_RINGBUF_ENABLE=1
export DECIM_STRIDE=1
export EXPORT_DECIM_MAX=4000000
export RING_EXPORT_PATH="${RING_EXPORT_PATH:-/home/pi/kfusion_results/jitter.bin}"
