#!/bin/bash
# ============================================================================
# quickstart_x86_64__x86快速开始.sh — x86_64 PREEMPT_RT 一键检查 / fuse / ko / 短测
# ============================================================================
# 用法:
#   export PLC_PLATFORM=x86_64   # 可省略：x86 主机上 auto 探测
#   bash scripts/platform/quickstart_x86_64__x86快速开始.sh check|fuse|ko|insmod|all
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_source_platform__加载平台.sh
source "$SCRIPT_DIR/plc_source_platform__加载平台.sh"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../fuse/plc_fusion_common__公共库.sh"

PRJ="$(plc_project_root)"
MANIFEST="${X86_QUICK_MANIFEST:-$PRJ/manifests/manifest_plc_cc_hello__入门.env}"
MODE="${1:-all}"

if [ "$PLATFORM_ID" != "x86_64" ] && [ "$(uname -m)" != "x86_64" ]; then
    plc_warn "当前 PLATFORM_ID=$PLATFORM_ID 主机=$(uname -m)" \
        "交叉编 kernel.o 请: PLC_PLATFORM=x86_64 $0 fuse" \
        "完整 .ko + insmod 必须在 x86_64 实机运行 $0 all"
fi

step_check() {
    echo "=== x86_64 环境检查 (PLC_PLATFORM=${PLC_PLATFORM}) ==="
    echo "    ${PLATFORM_LABEL:-}"
    echo "    LLC: ${FUSE_LLC_ARCH}/${FUSE_LLC_ATTR}"
    uname -a
    for t in clang-19 opt-19 llc-19; do
        command -v "$t" >/dev/null || plc_die "$PLC_E_NOCMD" "缺少 $t" "sudo apt install clang-19 llvm-19"
    done
    if [ ! -d "/lib/modules/$(uname -r)/build" ]; then
        plc_die "$PLC_E_NOFILE" "无 kernel headers: /lib/modules/$(uname -r)/build" \
            "${KERNEL_HEADERS_HINT:-sudo apt install linux-headers-\$(uname -r)}"
    fi
    echo "✅ 依赖 OK"
}

step_fuse() {
    PLC_PLATFORM=x86_64 bash "$PRJ/scripts/plc_fuse__内核化主流程.sh" "$MANIFEST"
}

step_ko() {
    PLC_PLATFORM=x86_64 bash "$PRJ/scripts/ignite_fused__通用ko构建.sh" "$MANIFEST"
}

step_insmod() {
    # shellcheck source=/dev/null
    source "$MANIFEST"
    local ko="$PRJ/test/${FUSE_NAME}_mod.ko"
    plc_require_file "$ko" "fused .ko"
    sudo insmod "$ko"
    sleep 2
    echo 1 | sudo tee "/sys/module/${FUSE_NAME}_mod/parameters/shutdown_request" >/dev/null
    bash "$PRJ/scripts/safe_rmmod_fused__安全卸载.sh" "${FUSE_NAME}_mod"
    echo "✅ insmod 短测通过"
}

case "$MODE" in
    check) step_check ;;
    fuse) step_check; step_fuse ;;
    ko) step_check; step_ko ;;
    insmod) step_insmod ;;
    all) step_check; step_fuse; step_ko; step_insmod ;;
    *)
        echo "用法: $0 check|fuse|ko|insmod|all"
        exit 2
        ;;
esac
