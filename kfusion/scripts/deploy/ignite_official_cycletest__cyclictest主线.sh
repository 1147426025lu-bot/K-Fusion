#!/bin/bash
# ============================================================================
# ignite_official_cycletest__cyclictest主线.sh — cyclictest .ko 构建 / 加载（统一入口）
# ============================================================================
# 宿主策略（FUSE_RUNNER_PROFILE）:
#   generic — ignite_fused：main + hrtimer + pthread（CI / 功能验证 / manifest 默认路径）
#   l2      — plc_runner_official：直接 timerthread + L2 测量 debugfs（浸泡 / 论文，默认）
#
# 环境:
#   PLC_FUSE_MANIFEST     manifest 路径（默认 cyclictest 主线）
#   FUSE_RUNNER_PROFILE   generic | l2（默认 l2）
#   IGNITE_BUILD_ONLY=1   仅 Kbuild，不 insmod
#   IGNITE_INSMOD_ONLY=1  仅 insmod（需已有 test/official_cycletest_mod.ko）
#   FORCE_REBUILD_KERNEL_O / SOAK_SKIP_KBUILD 等见 profile
#
# 用法:
#   bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh
#   FUSE_RUNNER_PROFILE=generic bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

MANIFEST="${PLC_FUSE_MANIFEST:-$PROJECT_ROOT/manifests/manifest_cyclictest__主线压测.env}"
plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

FUSE_RUNNER_PROFILE="${FUSE_RUNNER_PROFILE:-l2}"
MOD_NAME="${FUSE_NAME}_mod"
MOD_KO="$PROJECT_ROOT/test/${MOD_NAME}.ko"
TEST_DIR="$PROJECT_ROOT/test"
KERNEL_O="${FUSE_NAME}_kernel.o"
IGNITE_BUILD_ONLY="${IGNITE_BUILD_ONLY:-0}"
IGNITE_INSMOD_ONLY="${IGNITE_INSMOD_ONLY:-0}"

echo "=== ignite cyclictest: profile=${FUSE_RUNNER_PROFILE} manifest=$(basename "$MANIFEST") ==="

if [ "$FUSE_RUNNER_PROFILE" = generic ]; then
    if [ "$IGNITE_INSMOD_ONLY" = "1" ]; then
        plc_die "$PLC_E_USAGE" "generic 宿主请用 ignite_fused 的 insmod 说明" \
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

ensure_kernel_o() {
    local force="${FORCE_REBUILD_KERNEL_O:-0}"
    if [ "$force" = "1" ] || [ ! -f "$TEST_DIR/$KERNEL_O" ]; then
        echo "🔮 重建 ${KERNEL_O}..."
        bash "$PROJECT_ROOT/scripts/plc_fuse__内核化主流程.sh" "$MANIFEST"
    elif [ -f "$TEST_DIR/${KERNEL_O}_shipped" ]; then
        echo "🧽 复用 ${KERNEL_O}_shipped"
        cp -f "$TEST_DIR/${KERNEL_O}_shipped" "$TEST_DIR/$KERNEL_O"
    else
        bash "$PROJECT_ROOT/scripts/plc_fuse__内核化主流程.sh" "$MANIFEST"
    fi
    plc_require_file "$TEST_DIR/$KERNEL_O" "融合 .o"
}

build_l2_ko() {
    local stubs cflags extra_objcopy=() ko_ok=0

    ensure_kernel_o
    cd "$TEST_DIR"

    cp "$PROJECT_ROOT/src/plc_hrtimer_core__定时核心.c" plc_hrtimer_core.c
    cp "$PROJECT_ROOT/src/plc_runner_official__cyclictest宿主.c" plc_runner_official.c
    stubs="${FUSE_NAME}_runtime_stubs.c"
    if [ ! -f "$stubs" ]; then
        stubs="plc_runtime_stubs.c"
        cp "$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c" "$stubs"
    fi

    if [ -n "${FUSE_GLOBALIZE_SYMBOLS:-}" ]; then
        for sym in $FUSE_GLOBALIZE_SYMBOLS; do
            extra_objcopy+=(--globalize-symbol="$sym")
        done
    fi
    if [ "${#extra_objcopy[@]}" -gt 0 ]; then
        objcopy "${extra_objcopy[@]}" "$KERNEL_O"
    fi
    cp -f "$KERNEL_O" "${KERNEL_O}_shipped" 2>/dev/null || true
    echo "savedcmd_${TEST_DIR}/${KERNEL_O} := true" > ".${KERNEL_O}.cmd"

    cflags="-O2 -I${PROJECT_ROOT}/include -I${PROJECT_ROOT}/src"
    [ "${HOST_DRIVER_OPT_LEVEL:--O2}" != "-O2" ] && cflags="${HOST_DRIVER_OPT_LEVEL} -I${PROJECT_ROOT}/include -I${PROJECT_ROOT}/src"

    cat > Makefile <<MAKEFILE_EOF
obj-m += ${MOD_NAME}.o
${MOD_NAME}-objs := plc_runner_official.o plc_hrtimer_core.o ${stubs%.c}.o ${KERNEL_O}
KDIR := /lib/modules/\$(shell uname -r)/build
PWD := \$(shell pwd)
ccflags-y += ${cflags}
CFLAGS_plc_runner_official.o += ${HOST_DRIVER_OPT_LEVEL:--O2}
all:
	\$(MAKE) -C \$(KDIR) M=\$(PWD) modules
clean:
	\$(MAKE) -C \$(KDIR) M=\$(PWD) clean
MAKEFILE_EOF

    rm -f "${MOD_NAME}.ko" "${MOD_NAME}.o" "${MOD_NAME}.mod" "${MOD_NAME}.mod.o" \
        "${MOD_NAME}.mod.c" plc_runner_official.o plc_hrtimer_core.o "${stubs%.c}.o" 2>/dev/null || true

    echo "🔧 Kbuild L2 runner → ${MOD_NAME}.ko"
    if make all 2>&1 | tee "${FUSE_NAME}.kbuild.log"; then
        ko_ok=1
    fi
    if [ "$ko_ok" != "1" ]; then
        plc_die "$PLC_E_BUILD" "L2 runner Kbuild 失败" \
            "日志: test/${FUSE_NAME}.kbuild.log"
    fi
    plc_require_file "${MOD_NAME}.ko" "内核模块"
    echo "✅ built test/${MOD_NAME}.ko (FUSE_RUNNER_PROFILE=l2)"
}

insmod_l2_ko() {
    plc_check_sudo 1
    plc_check_module_stuck "$MOD_NAME"

    if lsmod | grep -q "^${MOD_NAME}"; then
        echo "ℹ️  ${MOD_NAME} 已加载，跳过 insmod"
        return 0
    fi

    plc_require_file "$MOD_KO" "内核模块" \
        "先运行 IGNITE_BUILD_ONLY=1 或去掉 IGNITE_INSMOD_ONLY"

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
    build_l2_ko
fi

if [ "$IGNITE_BUILD_ONLY" = "1" ]; then
    echo "ℹ️  IGNITE_BUILD_ONLY=1，跳过 insmod"
    exit 0
fi

insmod_l2_ko
