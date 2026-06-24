# ============================================================================
# 角色: SWAPPABLE — 测量 profile，可复制改名做对照实验
# profile_soak_l2_best__安静浸泡.env.sh — L2 安静浸泡测（无背景负载）
# ============================================================================
# 测量类型: soak（浸泡）— 探针核尽量安静，仅 1kHz 周期任务自跑，测 best-case 抖动
# 用法: source scripts/deploy/profiles/profile_soak_l2_best__安静浸泡.env.sh
# ============================================================================
# shellcheck disable=SC2034
export MEASURE_KIND=soak
export PLC_PLATFORM="${PLC_PLATFORM:-rpi5}"
export PLC_FUSE_MANIFEST="${PLC_FUSE_MANIFEST:-manifests/manifest_cyclictest__主线压测.env}"
export SYNC_RUNNER_SOURCE="${SYNC_RUNNER_SOURCE:-0}"
export FORCE_REBUILD_KERNEL_O="${FORCE_REBUILD_KERNEL_O:-0}"
export SOAK_SKIP_KBUILD="${SOAK_SKIP_KBUILD:-1}"

export RUNNER_PROFILE=fused_soak_l2
export TIMERTHREAD_CPU="${TIMERTHREAD_CPU:--1}"
export CLOCK_ABS_ENABLE="${CLOCK_ABS_ENABLE:-1}"
export JITTER_COMPENSATION_ENABLE="${JITTER_COMPENSATION_ENABLE:-1}"
export FUSED_WAKE_TIMERTHREAD=0
export FUSED_RINGBUF_ENABLE="${FUSED_RINGBUF_ENABLE:-0}"
export FUSED_HIST_ENABLE="${FUSED_HIST_ENABLE:-0}"
export DECIM_STRIDE="${DECIM_STRIDE:-50}"
export EXPORT_DECIM_MAX=0
export RING_EXPORT_PATH=""

export JITTER_RESYNC_THRESH_NS="${JITTER_RESYNC_THRESH_NS:-3000}"
export JITTER_EWMA_IGNORE_NS="${JITTER_EWMA_IGNORE_NS:-5000}"
export JITTER_SPIKE_LOG_ENABLE="${JITTER_SPIKE_LOG_ENABLE:-0}"
export RT_TUNE_USE_ISOLATE="${RT_TUNE_USE_ISOLATE:-1}"

export JITTER_PROBE_CPU="${JITTER_PROBE_CPU:-3}"
export PROBE_RT_ENABLE=0
export CYCLICTEST_PRIORITY="${CYCLICTEST_PRIORITY:-99}"

# L2：断 eth + 静默服务，压低 CPU3 外来干扰（须覆盖 profile_light 的 L3）
export ISOLATION_LEVEL=2
export STRESS_LOAD_ENABLE=0
export AUTO_CSET_SHIELD=0
export PLC_CGROUP=0
export PRE_IDLE_SEC=90
export POST_BUILD_DRAIN_SEC="${POST_BUILD_DRAIN_SEC:-30}"
export DRAIN_SEC="${DRAIN_SEC:-12}"
export SOAK_QUIESCE_TIMERS=1
export MEASURE_GRACE_TICKS="${MEASURE_GRACE_TICKS:-64}"
export POST_INSMOD_SETTLE_SEC="${POST_INSMOD_SETTLE_SEC:-45}"
export IGNITE_PRE_INSMOD_REFRESH="${IGNITE_PRE_INSMOD_REFRESH:-0}"
export MAX_UPTIME_SEC="${MAX_UPTIME_SEC:-28800}"
export RT_CPUFREQ_PERFORMANCE="${RT_CPUFREQ_PERFORMANCE:-1}"
export RT_ETH_ETHTOOL_COMBINED="${RT_ETH_ETHTOOL_COMBINED:-1}"
export WAIT_KTHREAD_SEC="${WAIT_KTHREAD_SEC:-15}"
export TARGET_ABS_MAX_NS="${TARGET_ABS_MAX_NS:-5000}"
# 浸泡计时窗口内不做 isolate refresh（避免尾部人为尖峰）
export ISOLATE_REFRESH_SEC="${ISOLATE_REFRESH_SEC:-0}"
# 浸泡窗口内压低 console 噪声（减轻 printk IPI）
export SOAK_SUPPRESS_PRINTK="${SOAK_SUPPRESS_PRINTK:-1}"
