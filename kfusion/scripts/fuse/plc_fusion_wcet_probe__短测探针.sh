#!/bin/bash
# ============================================================================
# plc_fusion_wcet_probe__短测探针.sh — 单份 kernel.o 短 insmod WCET 探针
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
# shellcheck source=../lib/plc_ignite__ko构建公共.sh
source "$SCRIPTS_ROOT/lib/plc_ignite__ko构建公共.sh"

PROJECT_ROOT="$(plc_project_root)"
DEPLOY="$PROJECT_ROOT/scripts/deploy"
MANIFEST="${1:-}"
KERNEL_OBJ="${2:-}"
DURATION_SEC="${3:-${WCET_PROBE_SEC:-30}}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
export PLC_FUSE_MANIFEST="$MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""
plc_require_file "$KERNEL_OBJ" "kernel.o" "传入 sweep 生成的 .o 路径"
[[ "$KERNEL_OBJ" != /* ]] && KERNEL_OBJ="$PROJECT_ROOT/$KERNEL_OBJ"
KERNEL_OBJ="$(readlink -f "$KERNEL_OBJ")"
plc_require_file "$KERNEL_OBJ" "kernel.o"

if [ "${FUSE_KTHREAD_ENTRY:-}" != timerthread ] && [ "$FUSE_NAME" != official_cycletest ]; then
    plc_die "$PLC_E_MANIFEST" "短测探针仅支持 cyclictest 主线（timerthread）" \
        "使用 manifests/manifest_cyclictest__主线压测.env"
fi

plc_check_kernel_build >/dev/null
plc_check_sudo 1
plc_check_module_stuck "official_cycletest_mod"

TEST_DIR="$PROJECT_ROOT/test"
BUILD_DIR="$TEST_DIR/.wcet_probe_build"
MOD_KO="official_cycletest_mod.ko"
STATS_OUT="${WCET_PROBE_STATS:-}"

echo "=== WCET probe: ${FUSE_NAME} (${DURATION_SEC}s) ==="
echo "    obj=$KERNEL_OBJ"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

PLC_IGNITE_BUILD_DIR="$BUILD_DIR"
PLC_IGNITE_KERNEL_O_SRC="$KERNEL_OBJ"
PLC_IGNITE_MOD_NAME="official_cycletest_mod"
PLC_BUILD_LOG="$BUILD_DIR/build.log"
FUSE_KO_FIXUP_LOOP=0
export PLC_IGNITE_BUILD_DIR PLC_IGNITE_KERNEL_O_SRC PLC_IGNITE_MOD_NAME PLC_BUILD_LOG FUSE_KO_FIXUP_LOOP

plc_ignite_build_l2 "$MANIFEST"
MOD_KO_ABS="$BUILD_DIR/$MOD_KO"

if lsmod | grep -q '^official_cycletest_mod'; then
    bash "$DEPLOY/safe_rmmod_official__cyclictest卸载.sh" || true
    sleep 1
fi

sudo -n dmesg -c >/dev/null 2>&1 || true
sudo -n insmod "$MOD_KO_ABS" \
    cycletest_priority="${CYCLICTEST_PRIORITY:-99}" \
    cycletest_interval_us="${CYCLICTEST_INTERVAL_US:-1000}" \
    jitter_probe_cpu="${JITTER_PROBE_CPU:-3}" \
    timerthread_cpu="${TIMERTHREAD_CPU:--1}" \
    probe_rt_enable="${PROBE_RT_ENABLE:-1}" \
    runner_profile="${WCET_PROBE_TAG:-autotune}" \
    clock_abs_enable="${CLOCK_ABS_ENABLE:-1}" \
    jitter_compensation_enable="${JITTER_COMPENSATION_ENABLE:-1}" \
    jitter_hist_enable="${JITTER_HIST_ENABLE:-0}" \
    jitter_outlier_log_enable="${JITTER_OUTLIER_LOG_ENABLE:-0}" \
    shutdown_request=0

sleep "$DURATION_SEC"

STATS_LINE=""
if [ -r /sys/kernel/debug/fused_stats ]; then
    STATS_LINE="$(sudo -n cat /sys/kernel/debug/fused_stats 2>/dev/null | tr -d '\n' || true)"
fi
[ -n "$STATS_OUT" ] && printf '%s\n' "$STATS_LINE" > "$STATS_OUT"

bash "$DEPLOY/safe_rmmod_official__cyclictest卸载.sh" || true

abs_max=""
cycles=""
min_ns=""
if [ -n "$STATS_LINE" ]; then
    abs_max="$(echo "$STATS_LINE" | sed -n 's/.*abs_max_ns=\([0-9-]*\).*/\1/p')"
    cycles="$(echo "$STATS_LINE" | sed -n 's/.*cycles=\([0-9]*\).*/\1/p')"
    min_ns="$(echo "$STATS_LINE" | sed -n 's/.*min_ns=\([0-9-]*\).*/\1/p')"
fi

if [ -z "$abs_max" ]; then
    plc_die "$PLC_E_KMOD" "探针未读到 fused_stats" \
        "确认 insmod 成功且 debugfs 可访问" \
        "可设 JITTER_HIST_ENABLE=0 减少 dmesg 干扰"
fi

echo "abs_max_ns=${abs_max} cycles=${cycles} min_ns=${min_ns}"
