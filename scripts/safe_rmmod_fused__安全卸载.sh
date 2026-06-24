#!/bin/bash
# ============================================================================
# safe_rmmod_fused__安全卸载.sh — 安全卸载 fused 内核模块
# ============================================================================
# 功能: 检测 refcnt=-1（oops 后卡住），先 shutdown_request 再 rmmod
# 用法: bash scripts/safe_rmmod_fused__安全卸载.sh <module_name>
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

MOD="${1:-}"
if [ -z "$MOD" ]; then
    plc_hint_usage "$0" "<module_name>"
fi

if ! lsmod | grep -q "^${MOD}"; then
    echo "ℹ️  ${MOD} 未加载，无需卸载"
    exit 0
fi

plc_check_module_stuck "$MOD"

plc_check_sudo 0
if ! sudo -n true 2>/dev/null; then
    plc_die "$PLC_E_PERM" "需要 sudo 权限执行 rmmod" \
        "先执行: sudo -v"
fi

PARAM="/sys/module/${MOD}/parameters/shutdown_request"
if [ -f "$PARAM" ]; then
    echo "🛑 shutdown_request=1..."
    if ! echo 1 | sudo tee "$PARAM" >/dev/null; then
        plc_warn "写入 shutdown_request 失败，仍尝试 rmmod"
    fi
    sleep 2
else
    plc_warn "模块 ${MOD} 无 shutdown_request 参数" \
        "直接 rmmod（可能因 kthread 未退出而失败）" \
        "等待几秒后重试，或 reboot"
fi

if ! sudo rmmod "$MOD" 2>/dev/null; then
    if lsmod | awk -v m="$MOD" '$1==m && $3=="-1" {found=1} END{exit !found}'; then
        plc_die "$PLC_E_STUCK" "rmmod 后 ${MOD} refcnt=-1" \
            "勿再次 rmmod / insmod / pkill" \
            "执行 sudo reboot"
    fi
    plc_die "$PLC_E_KMOD" "rmmod ${MOD} 失败" \
        "模块可能仍有活动 kthread" \
        "重试: sleep 3 && bash $0 $MOD" \
        "或加大等待: DRAIN_SEC=5（official 模块用 safe_rmmod_official__cyclictest卸载.sh）"
fi

echo "✅ unloaded $MOD"
