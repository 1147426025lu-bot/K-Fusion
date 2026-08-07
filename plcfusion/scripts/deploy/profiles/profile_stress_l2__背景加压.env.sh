# ============================================================================
# 角色: SWAPPABLE — stress 加压 profile
# profile_stress_l2__背景加压.env.sh — L1 + hackbench 背景加压测
# ============================================================================
# 测量类型: stress（加压）— CPU0-2 跑 hackbench，CPU3 仍隔离测 fused cyclictest 最坏延迟
# 用法: PLC_PROFILE=scripts/deploy/profiles/profile_stress_l2__背景加压.env.sh bash run_stress_cycletest__加压长测.sh
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=profile_soak_l2_best__安静浸泡.env.sh
source "$SCRIPT_DIR/profile_soak_l2_best__安静浸泡.env.sh"

export MEASURE_KIND=stress
export RUNNER_PROFILE=fused_stress_l2

# L1：基础 IRQ/workqueue 调优，保留 eth（更贴近「有负载」现场）
export ISOLATION_LEVEL=1

export STRESS_LOAD_ENABLE=1
export STRESS_LOAD_CPUS="${STRESS_LOAD_CPUS:-0-2}"
export STRESS_HACKBENCH_LOOPS="${STRESS_HACKBENCH_LOOPS:-8}"
export STRESS_HACKBENCH_PIPE="${STRESS_HACKBENCH_PIPE:-1}"
export STRESS_HACKBENCH_FIFO="${STRESS_HACKBENCH_FIFO:-0}"

# 加压下放宽 PASS 门槛（可按现场再调）
export TARGET_ABS_MAX_NS=15000
export JITTER_RESYNC_THRESH_NS=5000
export ISOLATE_REFRESH_SEC="${ISOLATE_REFRESH_SEC:-0}"
