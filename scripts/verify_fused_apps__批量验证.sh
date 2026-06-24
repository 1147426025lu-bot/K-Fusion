#!/bin/bash
# ============================================================================
# verify_fused_apps__批量验证.sh — 批量验证 fused 模块加载/卸载
# ============================================================================
# 功能: 对 signaltest/ptsematest/github_rt_periodic 等执行
#       insmod → 等待 → shutdown_request → safe_rmmod
# 覆盖: rt-tests / GitHub demo / stb / plc-cc 全大类（统一 ignite_fused 路径）
# 用法: bash scripts/verify_fused_apps__批量验证.sh
# 环境: VERIFY_CONTINUE=1  单个失败仍继续后续应用（默认 0）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PRJ="$(plc_project_root)"
VERIFY_CONTINUE="${VERIFY_CONTINUE:-0}"
FAILED=0

plc_check_sudo 1

run_one() {
    local manifest="$1"
    local wait_sec="${2:-5}"
    local args_override="${3:-}"
    local mod ko

    plc_require_file "$manifest" "manifest"
    # shellcheck disable=SC1090
    source "$manifest"
    if [ -n "$args_override" ]; then
        export FUSE_MAIN_ARGS="$args_override"
    fi
    mod="${FUSE_NAME}_mod"
    ko="$PRJ/test/${mod}.ko"

    echo ""
    echo "========== $mod ($FUSE_DESC) =========="

    if ! bash "$SCRIPT_DIR/ignite_fused__通用ko构建.sh" "$manifest"; then
        plc_warn "ignite_fused 失败: $manifest"
        return 1
    fi

    plc_require_file "$ko" "模块 .ko" \
        "ignite_fused 未生成 $ko"

    if lsmod | awk '{print $1}' | grep -qx "$mod"; then
        plc_die "$PLC_E_KMOD" "$mod 已加载" \
            "先: bash scripts/safe_rmmod_fused__安全卸载.sh $mod" \
            "或 refcnt=-1 时需 reboot"
    fi

    plc_check_module_stuck "$mod" || true

    if ! sudo insmod "$ko"; then
        plc_die "$PLC_E_KMOD" "insmod $ko 失败" \
            "查看: sudo dmesg | tail -30" \
            "常见: 未解析符号 / vermagic 不匹配 / 旧模块未卸载"
    fi

    sleep "$wait_sec"
    echo "--- dmesg (tail) ---"
    sudo dmesg | tail -8

    if ! bash "$SCRIPT_DIR/safe_rmmod_fused__安全卸载.sh" "$mod"; then
        return 1
    fi
    echo "✅ $mod OK"
    return 0
}

for stuck in signaltest_mod github_rt_periodic_mod ptsematest_mod; do
    if lsmod | awk -v m="$stuck" '$1==m && $3=="-1" {found=1} END{exit !found}'; then
        plc_die "$PLC_E_STUCK" "检测到 refcnt=-1: $stuck" \
            "执行 sudo reboot 后再运行本脚本" \
            "勿对卡住模块执行 rmmod/insmod"
    fi
done

APPS=(
    "$PRJ/manifests/manifest_plc_cc_hello__入门.env:3"
    "$PRJ/manifests/manifest_plc_cc_pure_logic__纯逻辑.env:3"
    "$PRJ/manifests/manifest_plc_cc_gpio__PLC示例.env:3"
    "$PRJ/manifests/manifest_plc_cc_temp_control__温控.env:3"
    "$PRJ/manifests/manifest_plc_cc_isolation__隔离测试.env:3"
    "$PRJ/manifests/manifest_plc_cc_dither__抖动测试.env:3"
    "$PRJ/manifests/manifest_github_rt_periodic__周期demo.env:3"
    "$PRJ/manifests/manifest_github_rt_periodic_multitu__多TU.env:3"
    "$PRJ/manifests/manifest_github_stb_sprintf__sprintf_demo.env:5"
    "$PRJ/manifests/manifest_ptsematest__互斥锁测试.env:5"
    "$PRJ/manifests/manifest_signaltest__信号测试.env:8"
    "$PRJ/manifests/manifest_cyclictest__主线压测.env:8"
    "$PRJ/manifests/manifest_cyclictest__多TU压测.env:8"
)

for item in "${APPS[@]}"; do
    manifest="${item%%:*}"
    wait="${item##*:}"
    extra_args=""
    if [[ "$manifest" == *cyclictest* ]]; then
        extra_args="${VERIFY_CYCLIC_ARGS:--p 99 -n 50 -i 1000 -m -q}"
    fi
    if run_one "$manifest" "$wait" "$extra_args"; then
        :
    else
        FAILED=$((FAILED + 1))
        if [ "$VERIFY_CONTINUE" != "1" ]; then
            plc_die "$PLC_E_KMOD" "验证失败: $manifest" \
                "修复后重试，或 VERIFY_CONTINUE=1 跑完全部"
        fi
        plc_warn "跳过并继续（VERIFY_CONTINUE=1）"
    fi
done

if [ "$FAILED" -gt 0 ]; then
    plc_die "$PLC_E_KMOD" "${FAILED} 个应用验证失败" \
        "查看上方 dmesg 与 plc_fuse_report__覆盖率报告.sh"
fi

echo ""
echo "✅ 全部 fused 应用验证通过"
