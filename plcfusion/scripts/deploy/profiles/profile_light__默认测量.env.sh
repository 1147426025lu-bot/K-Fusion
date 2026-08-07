# =============================================================================
# 角色: SWAPPABLE — L3 开发快测 profile（非正式 ≥15min 默认）
# 默认 profile：PLCFusion + hrtimer 驱动官方 cyclictest（official_cycletest_kernel.o）
# 融合清单: manifests/manifest_cyclictest__主线压测.env
# =============================================================================
export PLC_FUSE_MANIFEST="${PLC_FUSE_MANIFEST:-manifests/manifest_cyclictest__主线压测.env}"
export SYNC_RUNNER_SOURCE="${SYNC_RUNNER_SOURCE:-1}"
export FORCE_REBUILD_KERNEL_O="${FORCE_REBUILD_KERNEL_O:-0}"

export RUNNER_PROFILE="${RUNNER_PROFILE:-fused}"
export TIMERTHREAD_CPU="${TIMERTHREAD_CPU:--1}"
export CLOCK_ABS_ENABLE="${CLOCK_ABS_ENABLE:-1}"
export EXPORT_DECIM_MAX="${EXPORT_DECIM_MAX:-72000}"
export DECIM_STRIDE="${DECIM_STRIDE:-50}"
export RING_EXPORT_PATH="${RING_EXPORT_PATH:-}"

export JITTER_COMPENSATION_ENABLE="${JITTER_COMPENSATION_ENABLE:-1}"
export JITTER_REPORT_INTERVAL_SEC="${JITTER_REPORT_INTERVAL_SEC:-0}"
export FOLLOW_DMESG="${FOLLOW_DMESG:-0}"

export RUNNER_CPU="${RUNNER_CPU:-0}"
export JITTER_PROBE_CPU="${JITTER_PROBE_CPU:-3}"
export RUNNER_RT_ENABLE="${RUNNER_RT_ENABLE:-1}"
export PROBE_RT_ENABLE="${PROBE_RT_ENABLE:-1}"
export CYCLICTEST_PRIORITY="${CYCLICTEST_PRIORITY:-99}"

export ISOLATION_LEVEL="${ISOLATION_LEVEL:-3}"
export AUTO_CSET_SHIELD="${AUTO_CSET_SHIELD:-1}"
export PLC_CGROUP="${PLC_CGROUP:-1}"
export PRE_IDLE_SEC="${PRE_IDLE_SEC:-30}"
export RT_CPUFREQ_PERFORMANCE="${RT_CPUFREQ_PERFORMANCE:-1}"
export RT_ETH_ETHTOOL_COMBINED="${RT_ETH_ETHTOOL_COMBINED:-1}"
export DRAIN_SEC="${DRAIN_SEC:-8}"
export WAIT_KTHREAD_SEC="${WAIT_KTHREAD_SEC:-15}"
