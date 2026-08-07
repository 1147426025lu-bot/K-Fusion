#!/bin/bash
# ============================================================================
# plc_fusion_wcet_probe__短测探针.sh — 单份 kernel.o 短 insmod WCET 探针
# ============================================================================
# 功能: 将指定 .o 链入 official_cycletest_mod.ko → insmod → 采样 fused_stats → rmmod
# 输入: manifest.env kernel.o路径 [采样秒数]
# 输出: 终端一行 abs_max_ns=...；可选 WCET_PROBE_STATS 文件
# 用法:
#   bash scripts/plc_fusion_wcet_probe__短测探针.sh \
#     manifests/manifest_cyclictest__主线压测.env test/.official_cycletest_wcet_sweep/hotpath.o 30
# 环境:
#   WCET_PROBE_STATS=   写入 debugfs 原始行
#   WCET_PROBE_TAG=     runner profile 标签（默认 autotune）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
DEPLOY="$PROJECT_ROOT/scripts/deploy"
MANIFEST="${1:-}"
KERNEL_OBJ="${2:-}"
DURATION_SEC="${3:-${WCET_PROBE_SEC:-30}}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
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
KERNEL_O="${FUSE_NAME}_kernel.o"
MOD_KO="official_cycletest_mod.ko"
STATS_OUT="${WCET_PROBE_STATS:-}"

echo "=== WCET probe: ${FUSE_NAME} (${DURATION_SEC}s) ==="
echo "    obj=$KERNEL_OBJ"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

cp "$PROJECT_ROOT/src/plc_runner_official__cyclictest宿主.c" plc_runner_official.c
STUBS="$TEST_DIR/${FUSE_NAME}_runtime_stubs.c"
[ -f "$STUBS" ] || STUBS="$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c"
cp "$STUBS" plc_runtime_stubs.c
cp -f "$KERNEL_OBJ" "$KERNEL_O"

if [ -n "${FUSE_GLOBALIZE_SYMBOLS:-}" ]; then
    OBJCOPY_ARGS=()
    for sym in $FUSE_GLOBALIZE_SYMBOLS; do
        OBJCOPY_ARGS+=(--globalize-symbol="$sym")
    done
    objcopy "${OBJCOPY_ARGS[@]}" "$KERNEL_O"
fi

echo "savedcmd_${BUILD_DIR}/${KERNEL_O} := true" > ".${KERNEL_O}.cmd"

cat > Makefile <<MAKEFILE_EOF
obj-m += official_cycletest_mod.o
official_cycletest_mod-objs := plc_runner_official.o plc_runtime_stubs.o ${KERNEL_O}
KDIR := /lib/modules/\$(shell uname -r)/build
PWD := \$(shell pwd)
ccflags-y += -O2 -I${PROJECT_ROOT}/include
all:
	\$(MAKE) -C \$(KDIR) M=\$(PWD) modules
clean:
	\$(MAKE) -C \$(KDIR) M=\$(PWD) clean
MAKEFILE_EOF

if ! make all 2>"$BUILD_DIR/build.err"; then
    tail -20 "$BUILD_DIR/build.err" >&2 || true
    plc_die "$PLC_E_BUILD" "探针 Kbuild 失败" "检查 $KERNEL_OBJ 符号是否完整"
fi
plc_require_file "$MOD_KO" "探针 .ko"
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

echo "WCET_PROBE: tag=${WCET_PROBE_TAG:-autotune} duration=${DURATION_SEC}s cycles=${cycles:-0} min_ns=${min_ns:-0} abs_max_ns=${abs_max}"
echo "abs_max_ns=${abs_max}"
