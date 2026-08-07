# ============================================================================
# profile_paper_plot_stress__论文加压出图.env.sh — L1 加压 + ringbuf 导出
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=profile_paper_plot__论文抖动出图.env.sh
source "$SCRIPT_DIR/profile_paper_plot__论文抖动出图.env.sh"

export MEASURE_KIND=stress
export RUNNER_PROFILE=fused_paper_plot_stress
export ISOLATION_LEVEL=1
export STRESS_LOAD_ENABLE=1
export STRESS_LOAD_CPUS="${STRESS_LOAD_CPUS:-0-2}"
export TARGET_ABS_MAX_NS="${TARGET_ABS_MAX_NS:-15000}"
