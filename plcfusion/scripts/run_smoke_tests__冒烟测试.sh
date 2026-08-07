#!/bin/bash
# ============================================================================
# run_smoke_tests__冒烟测试.sh — 本地冒烟（CI + 可选 insmod）
# ============================================================================
# 功能: run_ci → 构建 cyclictest .ko → 可选 insmod 10s → 安全卸载
# 用法:
#   bash scripts/run_smoke_tests__冒烟测试.sh           # 仅 CI（无 insmod）
#   bash scripts/run_smoke_tests__冒烟测试.sh --insmod  # 含 cyclictest 主线 insmod
#   bash scripts/run_smoke_tests__冒烟测试.sh --full    # CI + cyclictest insmod + verify_fused
# 环境: 需免密 sudo（--insmod / --full）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PRJ="$(plc_project_root)"
MODE="${1:-}"

echo "=== PLCFusion 冒烟测试 mode=${MODE:-ci-only} ==="

bash "$SCRIPT_DIR/run_ci__CI门禁.sh"

case "$MODE" in
    ""|--ci)
        echo "✅ 冒烟完成（CI only，无 insmod）"
        exit 0
        ;;
    --insmod|--full)
        plc_check_sudo 1
        source "$PRJ/scripts/deploy/profiles/profile_soak_l2_best__安静浸泡.env.sh"
        export PLC_FUSE_MANIFEST="$PRJ/manifests/manifest_cyclictest__主线压测.env"
        export FORCE_REBUILD_KERNEL_O=0
        export FUSE_MAIN_ARGS="${SMOKE_CYCLIC_ARGS:--p 99 -n 50 -i 1000 -m -q}"
        echo "🚀 cyclictest 通用宿主 insmod 冒烟（10s）..."
        bash "$PRJ/scripts/ignite_fused__通用ko构建.sh" "$PLC_FUSE_MANIFEST"
        ko="$PRJ/test/official_cycletest_mod.ko"
        plc_require_file "$ko" "cyclictest .ko"
        bash "$PRJ/scripts/safe_rmmod_fused__安全卸载.sh" official_cycletest_mod 2>/dev/null || true
        sudo insmod "$ko"
        sleep 10
        if [ -r /sys/kernel/debug/fused_stats ]; then
            echo "--- fused_stats ---"
            cat /sys/kernel/debug/fused_stats
        fi
        echo 1 | sudo tee /sys/module/official_cycletest_mod/parameters/shutdown_request >/dev/null
        bash "$PRJ/scripts/safe_rmmod_fused__安全卸载.sh" official_cycletest_mod
        echo "✅ cyclictest insmod 冒烟通过"
        ;;
    *)
        plc_die "$PLC_E_USAGE" "未知参数: $MODE" \
            "用法: $0 | $0 --insmod | $0 --full"
        ;;
esac

if [ "$MODE" = "--full" ]; then
    echo "🧪 批量 fused 应用验证..."
    bash "$SCRIPT_DIR/verify_fused_apps__批量验证.sh"
    echo "✅ 全量冒烟通过"
fi
