#!/bin/bash
# ============================================================================
# plc_fuse_report__覆盖率报告.sh — 融合覆盖率报告
# ============================================================================
# 功能: 统计 kernel.ll 活跃 external、plc_* 映射、缺桩符号、孤儿 declare
# 输入: manifest.env
# 输出: 终端报告；末行「缺少实现: N」供 plc_fuse_check__覆盖率门禁.sh 解析
# 用法: bash scripts/plc_fuse_report__覆盖率报告.sh manifests/foo.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-manifests/manifest_cyclictest__主线压测.env}}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
KLL="$WORK/${FUSE_NAME}_kernel.ll"
UNMAPPED="$WORK/${FUSE_NAME}.unmapped"
STUBS="${PLC_RUNTIME_STUBS:-$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c}"
APP_STUBS="$WORK/${FUSE_NAME}_runtime_stubs.c"
[ -f "$APP_STUBS" ] && STUBS="$APP_STUBS"
RUNNER="${PLC_RUNNER_STUBS:-$PROJECT_ROOT/src/plc_runner_official__cyclictest宿主.c}"

if [ ! -f "$KLL" ]; then
    plc_die "$PLC_E_NOFILE" "未找到 kernel IR: $KLL" \
        "先运行: bash scripts/plc_fuse__内核化主流程.sh $MANIFEST" \
        "融合失败时检查 test/${FUSE_NAME}.pipeline.log"
fi

filter_declares() {
    grep '^declare ' | grep -v '@llvm\.' | grep -v ' @memset' | grep -v ' @memcpy' | \
        grep -v ' @strlen' | grep -v ' @strcmp' | grep -v ' @strncmp' | \
        sed -E 's/declare .* @([^ (]+).*/\1/' | sort -u
}

has_live_call() {
    local sym="$1"
    grep -qE "(call|invoke)[^@]*@${sym}[( ]" "$KLL"
}

has_stub_impl() {
    local sym="$1"
    local ko="$PROJECT_ROOT/test/${FUSE_NAME}_kernel.o"
    local modpost="$PROJECT_ROOT/test/${FUSE_NAME}_modpost_stubs.c"
    grep -qE "\\b${sym}\\b" "$STUBS" 2>/dev/null && return 0
    grep -qE "\\b${sym}\\b" "$RUNNER" 2>/dev/null && return 0
    [ -f "$modpost" ] && grep -qE "\\b${sym}\\b" "$modpost" 2>/dev/null && return 0
    if [ -f "$ko" ] && nm "$ko" 2>/dev/null | awk '{print $3}' | grep -qx "$sym"; then
        return 0
    fi
    hint="$(plc_remap_hint_for_sym "$sym" 2>/dev/null || true)"
    if [[ "$hint" == plc_* ]] && grep -qE "@${hint}[( ]" "$KLL" 2>/dev/null; then
        return 0
    fi
    [[ "$sym" == plc_* ]] && return 0
    return 1
}

echo "=== PLCFusion 覆盖率: ${FUSE_NAME} ==="
echo "    manifest=$MANIFEST"
echo "    ir=$KLL"
echo

echo "--- 已映射 plc_*（IR 中出现）---"
mapped=$(grep -oE '@plc_[a-zA-Z0-9_]+' "$KLL" 2>/dev/null | sort -u | sed 's/^@/  /' || true)
if [ -n "$mapped" ]; then
    echo "$mapped"
else
    echo "  （无 plc_* — Pass remap 可能未运行或 IR 异常）"
fi

echo
echo "--- 活跃 external 调用（非 plc_*）---"
LIVE=0
STUBBED=0
MISSING=0
while IFS= read -r sym; do
    [[ "$sym" == plc_* ]] && continue
    if ! has_live_call "$sym"; then
        continue
    fi
    LIVE=$((LIVE + 1))
    if has_stub_impl "$sym"; then
        STUBBED=$((STUBBED + 1))
    else
        echo "  ❌ $sym"
        MISSING=$((MISSING + 1))
    fi
done < <(grep '^declare ' "$KLL" 2>/dev/null | filter_declares || true)

if [ "$MISSING" -eq 0 ] && [ "$LIVE" -gt 0 ]; then
    echo "  （全部 $LIVE 个活跃 external 已有桩或 plc_* 映射）"
elif [ "$LIVE" -eq 0 ]; then
    plc_warn "无活跃 external 调用" \
        "可能 DCE 已裁剪全部用户代码，检查 FUSE_DCE_ROOTS"
fi

echo
echo "--- 融合入口 ---"
entries=$(grep -E '^define .* @(timerthread|signalthread|semathread|main|worker|thread|plc_cycle|plc_main|plc_logic)\(' "$KLL" 2>/dev/null | \
    sed -E 's/^define .* @([^ (]+).*/  \1/' | sort -u || true)
if [ -n "$entries" ]; then
    echo "$entries"
else
    echo "  （无标准入口 — 检查 DCE 或手动 FUSE_KTHREAD_ENTRY）"
fi

if [ -f "$UNMAPPED" ] && [ -s "$UNMAPPED" ]; then
    echo
    echo "--- Pass 黑洞记录 (${FUSE_NAME}.unmapped) ---"
    sort -u "$UNMAPPED" | sed 's/^/  /'
fi

HINTS="$PROJECT_ROOT/test/${FUSE_NAME}.remap_hints"
if [ -f "$HINTS" ]; then
    echo
    echo "--- Remap 建议 (${FUSE_NAME}.remap_hints) ---"
    grep '→' "$HINTS" 2>/dev/null | head -20 | sed 's/^/  /' || true
fi

ORPHAN=$(grep '^declare ' "$KLL" 2>/dev/null | filter_declares | wc -l || true)
ORPHAN=$((ORPHAN - LIVE))

echo
echo "活跃 external 调用数: $LIVE"
echo "已有桩/plc_* 满足: $STUBBED"
echo "缺少实现（须扩展 Pass/桩）: $MISSING"
echo "孤儿 declare（Pass 已清理）: $ORPHAN"
if [ "$MISSING" -gt 0 ]; then
    echo "修复: bash scripts/plc_fuse_merge_stubs__桩合并.sh $MANIFEST"
    echo "      或编辑 backend/pass/PLCFusionPass__内核化Pass.cpp / src/plc_runtime_stubs__POSIX桩.c"
fi
