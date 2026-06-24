#!/bin/bash
# ============================================================================
# run_ko_build__全类ko编译.sh — 全部 manifest 通用 .ko 编译（无 insmod）
# ============================================================================
# 功能: 对 CI 清单中每一类应用执行 ignite_fused，验证「内核化 → 可链接 .ko」
# 用法: bash scripts/run_ko_build__全类ko编译.sh
# 环境: KO_BUILD_MANIFESTS=  空格分隔 manifest 列表（默认同 run_ci）
#       KO_BUILD_SKIP_FUSE=1   已有 _kernel.o 时跳过 fuse
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
IGNITE="$SCRIPT_DIR/ignite_fused__通用ko构建.sh"

DEFAULT_MANIFESTS=(
    "$PRJ/manifests/manifest_cyclictest__主线压测.env"
    "$PRJ/manifests/manifest_cyclictest__多TU压测.env"
    "$PRJ/manifests/manifest_signaltest__信号测试.env"
    "$PRJ/manifests/manifest_ptsematest__互斥锁测试.env"
    "$PRJ/manifests/manifest_github_rt_periodic__周期demo.env"
    "$PRJ/manifests/manifest_github_rt_periodic_multitu__多TU.env"
    "$PRJ/manifests/manifest_github_stb_sprintf__sprintf_demo.env"
    "$PRJ/manifests/manifest_plc_cc_gpio__PLC示例.env"
    "$PRJ/manifests/manifest_plc_cc_pure_logic__纯逻辑.env"
    "$PRJ/manifests/manifest_plc_cc_temp_control__温控.env"
    "$PRJ/manifests/manifest_plc_cc_isolation__隔离测试.env"
    "$PRJ/manifests/manifest_plc_cc_dither__抖动测试.env"
    "$PRJ/manifests/manifest_plc_cc_hello__入门.env"
)

if [ -n "${KO_BUILD_MANIFESTS:-}" ]; then
    # shellcheck disable=SC2206
    MANIFEST_LIST=($KO_BUILD_MANIFESTS)
else
    MANIFEST_LIST=("${DEFAULT_MANIFESTS[@]}")
fi

export FORCE_REBUILD_KERNEL_O="${KO_BUILD_FORCE_FUSE:-0}"

echo "=== PLCFusion 全类 .ko 编译 (${#MANIFEST_LIST[@]} manifests) ==="
OK=0
FAIL=0
for m in "${MANIFEST_LIST[@]}"; do
    name="$(basename "$m")"
    echo ""
    echo "    ▶ $name"
    if bash "$IGNITE" "$m"; then
        OK=$((OK + 1))
    else
        FAIL=$((FAIL + 1))
        plc_warn "ko 编译失败: $m"
    fi
done

echo ""
if [ "$FAIL" -gt 0 ]; then
    plc_die "$PLC_E_BUILD" "ko 编译: ok=$OK fail=$FAIL"
fi
echo "✅ 全类 .ko 编译通过 (${OK}/${#MANIFEST_LIST[@]})"
