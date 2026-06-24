# ============================================================================
# 角色: SWAPPABLE — resync=0 对照 profile（实验用，非 opt5 默认）
# profile_soak_l2_honest__诚实浸泡.env.sh — 如实统计浸泡测（不丢尖峰样本）
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=profile_soak_l2_best__安静浸泡.env.sh
source "$SCRIPT_DIR/profile_soak_l2_best__安静浸泡.env.sh"

export RUNNER_PROFILE="fused_soak_l2_honest"
export JITTER_RESYNC_THRESH_NS="${JITTER_RESYNC_THRESH_NS:-0}"
export JITTER_EWMA_IGNORE_NS="${JITTER_EWMA_IGNORE_NS:-2000}"
export JITTER_SPIKE_LOG_ENABLE="${JITTER_SPIKE_LOG_ENABLE:-0}"
export ISOLATE_REFRESH_SEC="${ISOLATE_REFRESH_SEC:-900}"
export TARGET_ABS_MAX_NS="${TARGET_ABS_MAX_NS:-5000}"
