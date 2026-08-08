#!/bin/bash
# ============================================================================
# run_host_stubs_sync_check__宿主桩同步门禁.sh — src 宿主与 test 副本一致
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"

PRJ="$(plc_project_root)"
FAIL=0

check_copy() {
    local src="$1" test="$2" name="$3"
    [ -f "$src" ] || return 0
    if [ ! -f "$test" ]; then
        echo "    FAIL 缺少 test 副本: $name"
        FAIL=1
        return
    fi
    if grep -q 'PLCFUSION_GENERATED_STUBS' "$test" 2>/dev/null; then
        return 0
    fi
    if ! cmp -s "$src" "$test"; then
        echo "    FAIL $name 与 src 不一致（请 cp src → test 或跑 ignite_fused）"
        FAIL=1
    fi
}

echo "=== 宿主源码同步门禁 ==="
check_copy "$PRJ/src/plc_runtime_stubs__POSIX桩.c" "$PRJ/test/plc_runtime_stubs.c" \
    "plc_runtime_stubs"
check_copy "$PRJ/src/plc_fused_host__通用宿主.c" "$PRJ/test/plc_fused_host.c" \
    "plc_fused_host"
check_copy "$PRJ/src/plc_fused_timer_host__hrtimer宿主.c" \
    "$PRJ/test/plc_fused_timer_host.c" "plc_fused_timer_host"
check_copy "$PRJ/src/plc_hrtimer_core__定时核心.c" \
    "$PRJ/test/plc_hrtimer_core.c" "plc_hrtimer_core"
check_copy "$PRJ/src/plc_pthread_host__pthread宿主.c" "$PRJ/test/plc_pthread_host.c" \
    "plc_pthread_host"

if [ "$FAIL" -ne 0 ]; then
    plc_die "${PLC_E_VALIDATE:-11}" "宿主 test/ 副本与 src/ 漂移" \
        "修复: cp src/plc_runtime_stubs__POSIX桩.c test/plc_runtime_stubs.c 等"
fi
echo "✅ 宿主源码同步门禁通过"
