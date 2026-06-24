#!/bin/bash
# ============================================================================
# validate_build__平台构建验证.sh — 无 insmod：仅验证指定平台能产出 kernel.o
# ============================================================================
# 可在 Pi 上交叉验证 x86_64（llc -march=x86-64），无需 x86 实机。
# 用法:
#   bash scripts/platform/validate_build__平台构建验证.sh
#   PLC_PLATFORM=x86_64 bash scripts/platform/validate_build__平台构建验证.sh
#   PLC_PLATFORM=rpi5 bash scripts/platform/validate_build__平台构建验证.sh
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../plc_fusion_common__公共库.sh"
# shellcheck source=plc_source_platform__加载平台.sh
source "$SCRIPT_DIR/plc_source_platform__加载平台.sh"

PROJECT_ROOT="$(plc_project_root)"
export PATH="${PATH:-}"
plc_prepend_llvm_path

MANIFESTS=(
    "$PROJECT_ROOT/manifests/manifest_plc_cc_hello__入门.env"
    "$PROJECT_ROOT/manifests/manifest_signaltest__信号测试.env"
)

echo "=== PLCFusion 平台构建验证 ==="
echo "    PLC_PLATFORM=${PLC_PLATFORM:-auto}"
echo "    PLATFORM_ID=${PLATFORM_ID:-?} ARCH=${PLATFORM_ARCH:-?}"
echo "    LLC=${FUSE_LLC_ARCH:-?} attr=${FUSE_LLC_ATTR:-}"

FAIL=0
for m in "${MANIFESTS[@]}"; do
    subdir="test/platform_${PLATFORM_ID}_$(basename "$m" .env)"
    export FUSE_WORK_DIR="$PROJECT_ROOT/$subdir"
    mkdir -p "$FUSE_WORK_DIR"
    # 复用主 test/ 下已有 rt-tests，避免重复 git clone
    if [ ! -e "$FUSE_WORK_DIR/rt-tests" ] && [ -d "$PROJECT_ROOT/test/rt-tests" ]; then
        ln -s "$PROJECT_ROOT/test/rt-tests" "$FUSE_WORK_DIR/rt-tests"
    fi
    echo ""
    echo "--- fuse $m → $subdir ---"
    if ! PLC_PLATFORM="$PLC_PLATFORM" FUSE_WORK_DIR="$FUSE_WORK_DIR" \
        PLC_FUSE_STRICT_VALIDATE=0 FUSE_STRICT_VALIDATE=0 \
        bash "$SCRIPT_DIR/../plc_fuse__内核化主流程.sh" "$m"; then
        echo "❌ fuse 失败: $m"
        FAIL=$((FAIL + 1))
        continue
    fi
    # shellcheck source=/dev/null
    source "$m"
    obj="$FUSE_WORK_DIR/${FUSE_NAME}_kernel.o"
    if [ ! -s "$obj" ]; then
        echo "❌ 无 kernel.o: $obj"
        FAIL=$((FAIL + 1))
        continue
    fi
    if command -v file >/dev/null 2>&1; then
        echo "    file: $(file -b "$obj")"
    fi
    if command -v llvm-readobj >/dev/null 2>&1; then
        llvm-readobj -h "$obj" 2>/dev/null | grep -E 'Arch:|Type:' | sed 's/^/    /' || true
    fi
    echo "✅ ${FUSE_NAME}_kernel.o"
done

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "❌ ${FAIL} 项失败"
    exit 1
fi
echo ""
echo "✅ 平台 ${PLATFORM_ID} 构建验证通过（未做 insmod）"
