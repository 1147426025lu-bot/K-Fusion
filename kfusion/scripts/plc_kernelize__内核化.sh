#!/bin/bash
# ============================================================================
# plc_kernelize__内核化.sh — 统一内核化入口（fuse → 校验 → .ko）
# ============================================================================
# 阶段（PLC_KERNELIZE_STAGE）:
#   all     — fuse + check + ko（默认）
#   fuse    — 仅 Pass 融合 → test/${FUSE_NAME}_kernel.o
#   check   — 覆盖率 + JSON validate（需已有 kernel.o）
#   ko      — 仅 Kbuild 链接 .ko（需已有 kernel.o；可 FORCE_REBUILD_KERNEL_O=1 先 fuse）
#
# cyclictest 宿主:
#   FUSE_RUNNER_PROFILE=generic  — 组合宿主（main + hrtimer + pthread，CI 默认）
#   FUSE_RUNNER_PROFILE=l2       — L2 测量 runner（浸泡 / 论文）
#
# 用法:
#   bash scripts/plc_kernelize__内核化.sh manifests/manifest_signaltest__信号测试.env
#   PLC_KERNELIZE_STAGE=fuse bash scripts/plc_kernelize__内核化.sh manifests/foo.env
#   FUSE_RUNNER_PROFILE=l2 bash scripts/plc_kernelize__内核化.sh manifests/manifest_cyclictest__主线压测.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
# shellcheck source=lib/plc_ignite__ko构建公共.sh
source "$SCRIPT_DIR/lib/plc_ignite__ko构建公共.sh"
plc_enable_err_trap

MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
STAGE="${PLC_KERNELIZE_STAGE:-all}"
PROJECT_ROOT="$(plc_project_root)"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
export PLC_FUSE_MANIFEST="$MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST"

FUSE_RUNNER_PROFILE="${FUSE_RUNNER_PROFILE:-generic}"
if [ "$FUSE_NAME" != official_cycletest ]; then
    FUSE_RUNNER_PROFILE=generic
fi

FUSE_SCRIPT="$SCRIPT_DIR/plc_fuse__内核化主流程.sh"
CHECK_SCRIPT="$SCRIPT_DIR/plc_fuse_check__覆盖率门禁.sh"
VALIDATE_SCRIPT="$SCRIPT_DIR/plc_fuse_validate__安全验证器JSON.sh"

echo "=== plc_kernelize: ${FUSE_NAME} stage=${STAGE} profile=${FUSE_RUNNER_PROFILE} ==="
echo "    manifest=$(basename "$MANIFEST")"

run_fuse() {
    bash "$FUSE_SCRIPT" "$MANIFEST"
}

run_check() {
    bash "$CHECK_SCRIPT" "$MANIFEST"
    bash "$VALIDATE_SCRIPT" "$MANIFEST"
}

run_ko() {
    if [ "$FUSE_RUNNER_PROFILE" = l2 ]; then
        unset PLC_IGNITE_BUILD_DIR PLC_IGNITE_KERNEL_O_SRC PLC_IGNITE_MOD_NAME
        plc_ignite_build_l2 "$MANIFEST"
    else
        plc_ignite_build_generic "$MANIFEST"
    fi
}

case "$STAGE" in
    fuse)
        run_fuse
        ;;
    check)
        run_check
        ;;
    ko)
        if [ "${FORCE_REBUILD_KERNEL_O:-0}" = "1" ]; then
            run_fuse
        fi
        run_ko
        ;;
    all)
        run_fuse
        run_check
        run_ko
        ;;
    *)
        plc_die "$PLC_E_USAGE" "未知 PLC_KERNELIZE_STAGE=$STAGE" \
            "可选: all | fuse | check | ko"
        ;;
esac

echo ""
echo "✅ plc_kernelize 完成 (${FUSE_NAME}, stage=${STAGE})"
case "$STAGE" in
    fuse|all)
        echo "   产物: test/${FUSE_NAME}_kernel.o"
        echo "   报告: test/${FUSE_NAME}.fusion_report"
        ;;
esac
case "$STAGE" in
    ko|all)
        echo "   模块: test/${FUSE_NAME}_mod.ko"
        echo "   加载: sudo insmod test/${FUSE_NAME}_mod.ko"
        ;;
esac
