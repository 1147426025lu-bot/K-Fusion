#!/bin/bash
# ============================================================================
# safe_rmmod_official__cyclictest卸载.sh — cyclictest 主线模块安全卸载
# ============================================================================
# 功能: flock 防并发 → shutdown_request → 等待 kthread → 限时 rmmod
# 环境: DRAIN_SEC WAIT_KTHREAD_SEC TIMEOUT_SEC
# 用法: bash scripts/deploy/safe_rmmod_official__cyclictest卸载.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../plc_fusion_common__公共库.sh"

MOD="official_cycletest_mod"
TIMEOUT_SEC="${TIMEOUT_SEC:-60}"
DRAIN_SEC="${DRAIN_SEC:-8}"
WAIT_KTHREAD_SEC="${WAIT_KTHREAD_SEC:-15}"
LOCK_FILE="${LOCK_FILE:-/tmp/plc_${MOD}.rmmod.lock}"

plc_require_cmd flock "安装 util-linux: sudo apt install util-linux"
plc_check_sudo 1

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    plc_die "$PLC_E_KMOD" "另一个 rmmod 正在进行" \
        "勿并发执行 safe_rmmod / verify 脚本" \
        "等待前一操作完成或删除陈旧锁: $LOCK_FILE"
fi

if ! lsmod | grep -q "^${MOD}"; then
    echo "ℹ️  ${MOD} 未加载"
    exit 0
fi

plc_check_module_stuck "$MOD"

PARAM="/sys/module/${MOD}/parameters/shutdown_request"
if [ -f "$PARAM" ]; then
    echo "🛑 shutdown_request=1..."
    echo 1 | sudo -n tee "$PARAM" >/dev/null
    sleep "$DRAIN_SEC"
else
    plc_warn "无 shutdown_request 参数" \
        "模块可能为旧版构建，直接尝试 rmmod"
fi

echo "⏳ 等待 official_cycletest 线程退出 (最长 ${WAIT_KTHREAD_SEC}s)..."
deadline=$((SECONDS + WAIT_KTHREAD_SEC))
while [ "$SECONDS" -lt "$deadline" ]; do
    if ! pgrep -x official_cycletest >/dev/null 2>&1; then
        echo "   -> kthread 已退出"
        sleep 1
        break
    fi
    sleep 0.5
done
if pgrep -x official_cycletest >/dev/null 2>&1; then
    plc_warn "official_cycletest 仍在运行" \
        "加大 WAIT_KTHREAD_SEC 或检查 shutdown_request 是否生效"
fi
sleep 1

echo "⏳ rmmod (最长 ${TIMEOUT_SEC}s)..."
set +e
if command -v timeout >/dev/null 2>&1; then
    sudo -n timeout --foreground "$TIMEOUT_SEC" rmmod "$MOD"
    RC=$?
else
    sudo -n rmmod "$MOD"
    RC=$?
fi
set -e

if [ "$RC" -eq 0 ] && ! lsmod | grep -q "^${MOD}"; then
    echo "✅ rmmod 成功"
    exit 0
fi

if lsmod | grep -q "^${MOD}.*-1"; then
    plc_die "$PLC_E_STUCK" "refcnt=-1：模块已损坏" \
        "勿再次 rmmod / insmod / pkill" \
        "执行 sudo reboot"
fi

plc_die "$PLC_E_KMOD" "卸载失败 (rc=${RC})" \
    "当前: $(lsmod | grep ^${MOD} || echo '未在 lsmod')" \
    "可加大 DRAIN_SEC=12 WAIT_KTHREAD_SEC=20 TIMEOUT_SEC=90 后重试" \
    "仍失败请 reboot"
