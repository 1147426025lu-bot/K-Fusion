# ============================================================================
# profile_paper_plot__论文抖动出图.env.sh — L2 浸泡 + ringbuf 导出（生成抖动/延迟图）
# ============================================================================
# 与 profile_soak_l2_best 测量条件相同，但开启 decim 环形缓冲 → jitter.bin → PNG
# 用法:
#   PLC_PROFILE=scripts/deploy/profiles/profile_paper_plot__论文抖动出图.env.sh \
#     DURATION_MIN=15 bash scripts/deploy/run_soak_cycletest__浸泡长测.sh
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PAPER_RING_EXPORT="${RING_EXPORT_PATH-}"
# shellcheck source=profile_soak_l2_best__安静浸泡.env.sh
source "$SCRIPT_DIR/profile_soak_l2_best__安静浸泡.env.sh"
if [ -n "${_PAPER_RING_EXPORT}" ]; then
    export RING_EXPORT_PATH="$_PAPER_RING_EXPORT"
fi

export RUNNER_PROFILE=fused_paper_plot_soak
export FUSED_RINGBUF_ENABLE=1
export EXPORT_DECIM_MAX=72000
export DECIM_STRIDE="${DECIM_STRIDE:-50}"
if [ -n "${_PAPER_RING_EXPORT}" ]; then
    export RING_EXPORT_PATH="$_PAPER_RING_EXPORT"
fi
# 出图跑需重建 .ko（含定点桩 plc_fix_*）
export SOAK_SKIP_KBUILD=0
export SYNC_RUNNER_SOURCE=1
export FORCE_REBUILD_KERNEL_O=1
