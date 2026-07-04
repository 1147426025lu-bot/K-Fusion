#!/bin/bash
# ============================================================================
# ignite_official_cycletest__cyclictest主线.sh — cyclictest 主线 .ko 构建
# ============================================================================
# 功能: plc_runner_official.c + official_cycletest_kernel.o + runtime_stubs → .ko
#       hrtimer 驱动 timerthread；可选 L2 profile / 快验 FORCE_REBUILD_KERNEL_O=0
# 输入: manifests/manifest_cyclictest__主线压测.env（PLC_FUSE_MANIFEST 可覆盖）
# 用法: cd scripts/deploy && bash ignite_official_cycletest__cyclictest主线.sh
# 注意: 不要用 sudo 跑整脚本（会丢 PATH，找不到 /usr/local/llvm-*）；仅 insmod 步骤内部 sudo
# ============================================================================
set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_DIR="$DEPLOY_DIR"
# shellcheck source=../plc_fusion_common__公共库.sh
source "$DEPLOY_DIR/../plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
TUNE_SCRIPT="$DEPLOY_DIR/../tune/rt_host_tune__主机调优.sh"
ISOLATE_SCRIPT="$DEPLOY_DIR/../tune/rt_host_isolate__CPU隔离.sh"
FUSE_MANIFEST="${PLC_FUSE_MANIFEST:-$PROJECT_ROOT/manifests/manifest_cyclictest__主线压测.env}"
plc_resolve_manifest "$FUSE_MANIFEST" "$PROJECT_ROOT"
FUSE_MANIFEST="$PLC_MANIFEST"
plc_require_file "$FUSE_MANIFEST" "manifest"
plc_source_manifest "$FUSE_MANIFEST"
KERNEL_O="${FUSE_NAME}_kernel.o"

# L2 生产 profile（可用 PLC_PROFILE 覆盖；IGNITE_SKIP_PROFILE=1 跳过）
if [ "${IGNITE_SKIP_PROFILE:-0}" != "1" ]; then
    _IGNITE_PROFILE="${PLC_PROFILE:-$DEPLOY_DIR/profiles/profile_soak_l2_best__安静浸泡.env.sh}"
    if [ -f "$_IGNITE_PROFILE" ]; then
        echo "📦 加载 profile: $_IGNITE_PROFILE"
        # shellcheck source=/dev/null
        source "$_IGNITE_PROFILE"
    fi
fi

cd "$PROJECT_ROOT/test"

if [ "${IGNITE_INSMOD_ONLY:-0}" = "1" ]; then
    echo "⚙️ insmod-only：跳过 build/fuse/Kbuild"
else
FORCE_REBUILD_KERNEL_O="${FORCE_REBUILD_KERNEL_O:-1}"
echo "🧹 [0/5] 清理 Kbuild 产物..."
rm -f .*.cmd *.mod *.mod.c *.mod.o Module.symvers modules.order *.ko

if [ "$FORCE_REBUILD_KERNEL_O" = "0" ] && [ -f "${KERNEL_O}_shipped" ]; then
    echo "🧽 快验模式：保留 ${KERNEL_O}（跳过 LLVM 全量编译）"
    rm -f official_cycletest_mod.ko official_cycletest_mod.o plc_runner_official.o 2>/dev/null || true
else
    echo "🧽 全量 clean（含 kernel.o）..."
    make clean > /dev/null 2>&1 || true
fi

echo "📋 [1/5] 主机侧 RT 调优..."
if [ "${ENV_SETUP_DONE:-0}" = "1" ]; then
    echo "   -> 已由 env_setup__测量环境.sh 完成，跳过重复隔离"
elif [ "${RT_TUNE_USE_ISOLATE:-0}" = "1" ] && [ -f "$ISOLATE_SCRIPT" ]; then
    export ISOLATION_LEVEL="${ISOLATION_LEVEL:-2}"
    export DISABLE_RT_RUNTIME_THROTTLE="${DISABLE_RT_RUNTIME_THROTTLE:-1}"
    export DISABLE_GLOBAL_TIMER_MIGRATION="${DISABLE_GLOBAL_TIMER_MIGRATION:-1}"
    bash "$ISOLATE_SCRIPT" setup
elif [ -f "$TUNE_SCRIPT" ]; then
    bash "$TUNE_SCRIPT" || true
else
    echo "   ⚠️ 未找到调优脚本"
fi

echo "📄 同步宿主源码..."
if [ "${SYNC_RUNNER_SOURCE:-1}" = "1" ]; then
    cp "$PROJECT_ROOT/src/plc_runner_official__cyclictest宿主.c" "$PROJECT_ROOT/test/plc_runner_official.c"
    cp "$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c" "$PROJECT_ROOT/test/plc_runtime_stubs.c"
else
    echo "   -> 保留 test/ 下已有宿主"
fi

echo "🚀 [2/5] PLCFusion 融合目标 (${KERNEL_O})..."
FUSE_SCRIPT="$DEPLOY_DIR/../plc_fuse__内核化主流程.sh"
if [ "$FORCE_REBUILD_KERNEL_O" = "1" ]; then
    echo "   -> 重建 ${KERNEL_O} (manifest=${FUSE_MANIFEST})..."
    bash "$FUSE_SCRIPT" "$FUSE_MANIFEST"
elif [ -f "$KERNEL_O" ]; then
    echo "   -> 复用已有 ${KERNEL_O}"
elif [ -f "${KERNEL_O}_shipped" ]; then
    echo "   -> 复用 ${KERNEL_O}_shipped（FORCE_REBUILD_KERNEL_O=0）..."
    cp "${KERNEL_O}_shipped" "$KERNEL_O"
else
    echo "   -> 无缓存，全量融合 ${KERNEL_O}..."
    bash "$FUSE_SCRIPT" "$FUSE_MANIFEST"
fi

echo "🔧 [3/5] 确认 ${KERNEL_O} 已就绪..."
plc_require_file "$KERNEL_O" "融合对象" \
    "plc_fuse__内核化主流程.sh 失败或 FORCE_REBUILD_KERNEL_O=0 但无缓存"
plc_check_kernel_build >/dev/null
cp "$KERNEL_O" "${KERNEL_O}_shipped"
echo "savedcmd_$(pwd)/${KERNEL_O} := true" > ".${KERNEL_O}.cmd"

echo "🛠️ [4/5] 构建内核模块 (host + fused .o)..."
if [ "${SOAK_SKIP_KBUILD:-0}" = "1" ] && [ "${FORCE_KO_REBUILD:-0}" != "1" ] && \
   [ -f "official_cycletest_mod.ko" ]; then
    echo "   -> SOAK_SKIP_KBUILD=1，复用已有 official_cycletest_mod.ko（跳过 Kbuild/block IO）"
else
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

if ! make all; then
    plc_die "$PLC_E_BUILD" "Kbuild 编译 official_cycletest_mod.ko 失败" \
        "运行: bash scripts/plc_fuse_report__覆盖率报告.sh $FUSE_MANIFEST" \
        "检查未解析符号与内核头: linux-headers-\$(uname -r)"
fi
fi

plc_require_file "official_cycletest_mod.ko" "内核模块"

POST_BUILD_DRAIN_SEC="${POST_BUILD_DRAIN_SEC:-0}"
if [ "$POST_BUILD_DRAIN_SEC" -gt 0 ] 2>/dev/null; then
    echo "   -> Kbuild 后 drain ${POST_BUILD_DRAIN_SEC}s（消 block kworker 余热）..."
    sync 2>/dev/null || true
    sleep "$POST_BUILD_DRAIN_SEC"
fi

if [ "${IGNITE_BUILD_ONLY:-0}" = "1" ]; then
    echo "✅ build-only 完成（跳过 insmod）"
    exit 0
fi

fi

echo "⚙️ [5/5] 加载模块..."
echo "   -> 检查 sudo（需已 sudo -v 或 NOPASSWD）..."
plc_check_sudo 1

echo "   -> 检查模块 refcnt..."
plc_check_module_stuck "official_cycletest_mod"
if lsmod | grep -q '^official_cycletest_mod'; then
    echo "⚠️  卸载旧模块（最多约 ${TIMEOUT_SEC:-60}s）..."
    if ! bash "$SCRIPT_DIR/safe_rmmod_official__cyclictest卸载.sh"; then
        plc_die "$PLC_E_KMOD" "旧模块无法卸载，拒绝 insmod" \
            "加大 DRAIN_SEC / WAIT_KTHREAD_SEC 后重试 safe_rmmod_official__cyclictest卸载.sh" \
            "refcnt=-1 时需 sudo reboot"
    fi
fi

if [ "${ENV_SETUP_DONE:-0}" = "1" ] && [ "${IGNITE_PRE_INSMOD_REFRESH:-1}" = "1" ] && \
   [ -f "$ISOLATE_SCRIPT" ]; then
    echo "🔄 insmod 前刷新 CPU${JITTER_PROBE_CPU:-3} 隔离 (refresh)..."
    bash "$ISOLATE_SCRIPT" refresh 2>&1 | tail -8
fi

sudo -n dmesg -c > /dev/null
set +e
CYCLICTEST_PRIORITY="${CYCLICTEST_PRIORITY:-99}"
CYCLICTEST_INTERVAL_US="${CYCLICTEST_INTERVAL_US:-1000}"
JITTER_PROBE_CPU="${JITTER_PROBE_CPU:-3}"
PROBE_RT_ENABLE="${PROBE_RT_ENABLE:-0}"
JITTER_COMPENSATION_ENABLE="${JITTER_COMPENSATION_ENABLE:-1}"
JITTER_RESYNC_THRESH_NS="${JITTER_RESYNC_THRESH_NS:-3000}"
JITTER_EWMA_IGNORE_NS="${JITTER_EWMA_IGNORE_NS:-2000}"
JITTER_SPIKE_LOG_ENABLE="${JITTER_SPIKE_LOG_ENABLE:-0}"
SHUTDOWN_REQUEST="${SHUTDOWN_REQUEST:-0}"
RUNNER_PROFILE="${RUNNER_PROFILE:-fused_l2_best}"
TIMERTHREAD_CPU="${TIMERTHREAD_CPU:--1}"
CLOCK_ABS_ENABLE="${CLOCK_ABS_ENABLE:-1}"
EXPORT_DECIM_MAX="${EXPORT_DECIM_MAX:-0}"
DECIM_STRIDE="${DECIM_STRIDE:-50}"
RING_EXPORT_PATH="${RING_EXPORT_PATH:-}"
FUSED_HIST_ENABLE="${FUSED_HIST_ENABLE:-0}"
FUSED_WAKE_TIMERTHREAD="${FUSED_WAKE_TIMERTHREAD:-0}"
FUSED_RINGBUF_ENABLE="${FUSED_RINGBUF_ENABLE:-0}"
MEASURE_GRACE_TICKS="${MEASURE_GRACE_TICKS:-128}"

mkdir -p "$(dirname "${RING_EXPORT_PATH:-$PROJECT_ROOT/results}")"
echo "   -> profile=${RUNNER_PROFILE} prio=${CYCLICTEST_PRIORITY} interval_us=${CYCLICTEST_INTERVAL_US} cpu_tt=${TIMERTHREAD_CPU} probe_rt=${PROBE_RT_ENABLE} hist=${FUSED_HIST_ENABLE} wake_tt=${FUSED_WAKE_TIMERTHREAD} ring=${FUSED_RINGBUF_ENABLE} resync=${JITTER_RESYNC_THRESH_NS}ns grace=${MEASURE_GRACE_TICKS}"
INSMOD_TIMEOUT_SEC="${INSMOD_TIMEOUT_SEC:-30}"
echo "   -> insmod official_cycletest_mod.ko（module_init 最长 ${INSMOD_TIMEOUT_SEC}s）..."
set +e
if command -v timeout >/dev/null 2>&1; then
    sudo -n timeout --foreground "$INSMOD_TIMEOUT_SEC" insmod official_cycletest_mod.ko \
    cycletest_priority="${CYCLICTEST_PRIORITY}" \
    cycletest_interval_us="${CYCLICTEST_INTERVAL_US}" \
    jitter_probe_cpu="${JITTER_PROBE_CPU}" \
    timerthread_cpu="${TIMERTHREAD_CPU}" \
    probe_rt_enable="${PROBE_RT_ENABLE}" \
    runner_profile="${RUNNER_PROFILE}" \
    clock_abs_enable="${CLOCK_ABS_ENABLE}" \
    jitter_compensation_enable="${JITTER_COMPENSATION_ENABLE}" \
    jitter_resync_thresh_ns="${JITTER_RESYNC_THRESH_NS}" \
    jitter_ewma_ignore_ns="${JITTER_EWMA_IGNORE_NS}" \
    jitter_spike_log_enable="${JITTER_SPIKE_LOG_ENABLE}" \
    export_decim_max="${EXPORT_DECIM_MAX}" \
    decim_stride="${DECIM_STRIDE}" \
    ring_export_path="${RING_EXPORT_PATH}" \
    fused_hist_enable="${FUSED_HIST_ENABLE}" \
    fused_wake_timertthread="${FUSED_WAKE_TIMERTHREAD}" \
    fused_ringbuf_enable="${FUSED_RINGBUF_ENABLE}" \
    measure_grace_default="${MEASURE_GRACE_TICKS}" \
    shutdown_request="${SHUTDOWN_REQUEST}"
    INSMOD_RC=$?
else
    sudo -n insmod official_cycletest_mod.ko \
    cycletest_priority="${CYCLICTEST_PRIORITY}" \
    cycletest_interval_us="${CYCLICTEST_INTERVAL_US}" \
    jitter_probe_cpu="${JITTER_PROBE_CPU}" \
    timerthread_cpu="${TIMERTHREAD_CPU}" \
    probe_rt_enable="${PROBE_RT_ENABLE}" \
    runner_profile="${RUNNER_PROFILE}" \
    clock_abs_enable="${CLOCK_ABS_ENABLE}" \
    jitter_compensation_enable="${JITTER_COMPENSATION_ENABLE}" \
    jitter_resync_thresh_ns="${JITTER_RESYNC_THRESH_NS}" \
    jitter_ewma_ignore_ns="${JITTER_EWMA_IGNORE_NS}" \
    jitter_spike_log_enable="${JITTER_SPIKE_LOG_ENABLE}" \
    export_decim_max="${EXPORT_DECIM_MAX}" \
    decim_stride="${DECIM_STRIDE}" \
    ring_export_path="${RING_EXPORT_PATH}" \
    fused_hist_enable="${FUSED_HIST_ENABLE}" \
    fused_wake_timertthread="${FUSED_WAKE_TIMERTHREAD}" \
    fused_ringbuf_enable="${FUSED_RINGBUF_ENABLE}" \
    measure_grace_default="${MEASURE_GRACE_TICKS}" \
    shutdown_request="${SHUTDOWN_REQUEST}"
    INSMOD_RC=$?
fi
set -e

if [ "${INSMOD_RC:-1}" -eq 0 ]; then
    echo "✅ 注入成功！"
    echo "ℹ️ 预停机: echo 1 | sudo tee /sys/module/official_cycletest_mod/parameters/shutdown_request"
    if [ "${FOLLOW_DMESG:-0}" = "1" ]; then
        sudo -n dmesg -w
    fi
else
    if [ "${INSMOD_RC:-1}" -eq 124 ]; then
        plc_die "$PLC_E_KMOD" "insmod 超时（${INSMOD_TIMEOUT_SEC}s）— module_init 未返回" \
            "另开终端: sudo dmesg | tail -40" \
            "若旧模块仍在: bash scripts/deploy/safe_rmmod_official__cyclictest卸载.sh" \
            "仍卡住可 sudo reboot"
    fi
    plc_die "$PLC_E_KMOD" "insmod official_cycletest_mod.ko 失败 (rc=${INSMOD_RC})" \
        "查看: sudo dmesg | tail -40" \
        "常见: 符号未解析 / timerthread 入口缺失 / 参数非法" \
        "确认: bash scripts/plc_fuse__内核化主流程.sh $FUSE_MANIFEST 成功"
fi
