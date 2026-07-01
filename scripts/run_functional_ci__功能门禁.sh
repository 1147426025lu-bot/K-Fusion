#!/bin/bash
# ============================================================================
# run_functional_ci__功能门禁.sh — 融合产物功能级门禁（ko 符号 + 可选短 insmod）
# ============================================================================
# 功能: 验证 kernel.o 非空、.ko 存在、modpost 无 unresolved
# 用法: bash scripts/run_functional_ci__功能门禁.sh
# 环境: FUNCTIONAL_INSMOD=1  对小型 manifest 做 5s insmod 短测（需 sudo）
#       FUNCTIONAL_MANIFESTS=  空格分隔（默认同 run_ci 子集）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
IGNITE="$SCRIPT_DIR/ignite_fused__通用ko构建.sh"

DEFAULT_MANIFESTS=(
    "$PRJ/manifests/manifest_plc_cc_hello__入门.env"
    "$PRJ/manifests/manifest_github_rt_periodic__周期demo.env"
    "$PRJ/manifests/manifest_signaltest__信号测试.env"
)

if [ -n "${FUNCTIONAL_MANIFESTS:-}" ]; then
    # shellcheck disable=SC2206
    MANIFEST_LIST=($FUNCTIONAL_MANIFESTS)
else
    MANIFEST_LIST=("${DEFAULT_MANIFESTS[@]}")
fi

echo "=== PLCFusion 功能门禁 (${#MANIFEST_LIST[@]} manifests) ==="
OK=0
FAIL=0

for m in "${MANIFEST_LIST[@]}"; do
    name="$(basename "$m")"
    # shellcheck disable=SC1090
    source "$m"
    KO="$PRJ/test/${FUSE_NAME}_mod.ko"
    OBJ="$PRJ/test/${FUSE_NAME}_kernel.o"

    echo ""
    echo "    ▶ $name"
    if [ ! -f "$OBJ" ]; then
        bash "$IGNITE" "$m" || { FAIL=$((FAIL + 1)); continue; }
        OBJ="$PRJ/test/${FUSE_NAME}_kernel.o"
    fi
    sz=$(stat -c%s "$OBJ" 2>/dev/null || echo 0)
    if [ "$sz" -lt 512 ]; then
        plc_warn "kernel.o 过小 ($sz bytes)"
        FAIL=$((FAIL + 1))
        continue
    fi

    if [ ! -f "$KO" ]; then
        bash "$IGNITE" "$m" || { FAIL=$((FAIL + 1)); continue; }
    fi
    ko_sz=$(stat -c%s "$KO" 2>/dev/null || echo 0)
    if [ "$ko_sz" -lt 4096 ]; then
        plc_warn ".ko 过小 ($ko_sz bytes)"
        FAIL=$((FAIL + 1))
        continue
    fi

    KLOG="$PRJ/test/${FUSE_NAME}.kbuild.log"
    if [ -f "$KLOG" ] && grep -qE "undefined symbol|Error 1" "$KLOG"; then
        if grep -q "undefined symbol" "$KLOG"; then
            plc_warn "Kbuild 日志含 undefined symbol"
            FAIL=$((FAIL + 1))
            continue
        fi
    fi

    if [ "${FUNCTIONAL_INSMOD:-0}" = "1" ]; then
        mod="${FUSE_NAME}_mod"
        if lsmod | grep -q "^${mod} "; then
            echo 1 | sudo tee "/sys/module/${mod}/parameters/shutdown_request" >/dev/null 2>&1 || true
            sudo rmmod "$mod" 2>/dev/null || true
        fi
        if ! sudo insmod "$KO"; then
            plc_warn "insmod 失败: $mod"
            FAIL=$((FAIL + 1))
            continue
        fi
        sleep 2
        if [ -f "/sys/kernel/debug/fused_stats" ] || [ -f "/sys/kernel/debug/fused_timer_stats" ]; then
            echo "    debugfs OK"
        fi
        echo 1 | sudo tee "/sys/module/${mod}/parameters/shutdown_request" >/dev/null
        sleep 1
        sudo rmmod "$mod" || plc_warn "rmmod $mod 失败"
    fi

    OK=$((OK + 1))
    echo "    ✅ $FUSE_NAME"
done

echo ""
if [ "$FAIL" -gt 0 ]; then
    plc_die "$PLC_E_BUILD" "功能门禁: ok=$OK fail=$FAIL"
fi
echo "✅ 功能门禁通过 (${OK}/${#MANIFEST_LIST[@]})"
