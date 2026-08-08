#!/bin/bash
# ============================================================================
# ignite_official_cycletest__cyclictest主线.sh — cyclictest .ko 构建 / 加载
# ============================================================================
# 推荐: bash scripts/plc_kernelize__内核化.sh manifests/manifest_cyclictest__主线压测.env
#       FUSE_RUNNER_PROFILE=l2 同上（L2 测量）
#
# FUSE_RUNNER_PROFILE:
#   generic — 组合宿主（CI / 功能）
#   l2      — L2 runner + debugfs（浸泡 / 论文，默认）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"
# shellcheck source=../lib/plc_ignite__ko构建公共.sh
source "$SCRIPT_DIR/../lib/plc_ignite__ko构建公共.sh"
plc_enable_err_trap

MANIFEST="${PLC_FUSE_MANIFEST:-$PROJECT_ROOT/manifests/manifest_cyclictest__主线压测.env}"
plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
export PLC_FUSE_MANIFEST="$MANIFEST"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

FUSE_RUNNER_PROFILE="${FUSE_RUNNER_PROFILE:-l2}"
MOD_NAME="${FUSE_NAME}_mod"
MOD_KO="$PROJECT_ROOT/test/${MOD_NAME}.ko"
IGNITE_BUILD_ONLY="${IGNITE_BUILD_ONLY:-0}"
IGNITE_INSMOD_ONLY="${IGNITE_INSMOD_ONLY:-0}"

echo "=== ignite cyclictest: profile=${FUSE_RUNNER_PROFILE} manifest=$(basename "$MANIFEST") ==="

if [ "$FUSE_RUNNER_PROFILE" = generic ]; then
    if [ "$IGNITE_INSMOD_ONLY" = "1" ]; then
        plc_die "$PLC_E_USAGE" "generic 宿主请用 plc_kernelize 或 ignite_fused 的 insmod 说明" \
            "sudo insmod test/${MOD_NAME}.ko main_args='...'"
    fi
    bash "$PROJECT_ROOT/scripts/ignite_fused__通用ko构建.sh" "$MANIFEST"
    exit 0
fi

if [ "$FUSE_RUNNER_PROFILE" != l2 ]; then
    plc_die "$PLC_E_MANIFEST" "未知 FUSE_RUNNER_PROFILE=$FUSE_RUNNER_PROFILE" \
        "可选: generic | l2"
fi

plc_check_kernel_build >/dev/null

insmod_l2_ko() {
    plc_check_sudo 1
    plc_check_module_stuck "$MOD_NAME"

    if lsmod | grep -q "^${MOD_NAME}"; then
        echo "ℹ️  ${MOD_NAME} 已加载，跳过 insmod"
        return 0
    fi

    plc_require_file "$MOD_KO" "内核模块" \
        "先运行 IGNITE_BUILD_ONLY=1 或 PLC_KERNELIZE_STAGE=ko"

    if [ "${IGNITE_PRE_INSMOD_REFRESH:-0}" = "1" ]; then
        # shellcheck source=env_setup__测量环境.sh
        source "$SCRIPT_DIR/env_setup__测量环境.sh"
        env_setup_host >/dev/null 2>&1 || true
    fi

    sudo -n dmesg -c >/dev/null 2>&1 || true
    echo "🚀 insmod ${MOD_NAME}.ko (L2 runner)..."
    sudo -n insmod "$MOD_KO" \
        cycletest_priority="${CYCLICTEST_PRIORITY:-99}" \
        cycletest_interval_us="${CYCLICTEST_INTERVAL_US:-1000}" \
        jitter_probe_cpu="${JITTER_PROBE_CPU:-3}" \
        timerthread_cpu="${TIMERTHREAD_CPU:--1}" \
        probe_rt_enable="${PROBE_RT_ENABLE:-0}" \
        runner_profile="${RUNNER_PROFILE:-fused_soak_l2}" \
        clock_abs_enable="${CLOCK_ABS_ENABLE:-1}" \
        jitter_compensation_enable="${JITTER_COMPENSATION_ENABLE:-1}" \
        jitter_resync_thresh_ns="${JITTER_RESYNC_THRESH_NS:-3000}" \
        jitter_ewma_ignore_ns="${JITTER_EWMA_IGNORE_NS:-5000}" \
        jitter_spike_log_enable="${JITTER_SPIKE_LOG_ENABLE:-0}" \
        fused_hist_enable="${FUSED_HIST_ENABLE:-0}" \
        fused_wake_timertthread="${FUSED_WAKE_TIMERTHREAD:-0}" \
        fused_ringbuf_enable="${FUSED_RINGBUF_ENABLE:-0}" \
        export_decim_max="${EXPORT_DECIM_MAX:-0}" \
        decim_stride="${DECIM_STRIDE:-50}" \
        ring_export_path="${RING_EXPORT_PATH:-}" \
        shutdown_request=0

    sleep "${POST_INSMOD_SETTLE_SEC:-2}"
    if ! lsmod | grep -q "^${MOD_NAME}"; then
        plc_die "$PLC_E_KMOD" "insmod 后模块未加载"
    fi
    echo "✅ ${MOD_NAME} loaded (debugfs: fused_stats, fused_stats_reset)"
}

if [ "$IGNITE_INSMOD_ONLY" != "1" ]; then
    plc_ignite_build_l2 "$MANIFEST"
fi

if [ "$IGNITE_BUILD_ONLY" = "1" ]; then
    echo "ℹ️  IGNITE_BUILD_ONLY=1，跳过 insmod"
    exit 0
fi

insmod_l2_ko
