#!/bin/bash
# ============================================================================
# plc_fuse_stub_loop__桩闭环.sh — Pass unmapped → 桩合并 → 覆盖率闭环
# ============================================================================
# 功能: 循环执行桩合并 + 覆盖率统计，直到缺桩稳定或达上限
# 输入: manifest.env（须已有 _kernel.ll）
# 输出: test/${FUSE_NAME}_runtime_stubs.c；终端缺桩计数
# 用法: bash scripts/plc_fuse_stub_loop__桩闭环.sh manifests/foo.env
# 环境: FUSE_STUB_LOOP_MAX=3（默认）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
MAX="${FUSE_STUB_LOOP_MAX:-3}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

MERGE="$SCRIPT_DIR/plc_fuse_merge_stubs__桩合并.sh"
REPORT="$SCRIPT_DIR/plc_fuse_report__覆盖率报告.sh"

missing_count() {
    local out
    out="$("$REPORT" "$MANIFEST" 2>/dev/null || true)"
    echo "$out" | sed -n 's/^缺少实现.*: \([0-9]*\)/\1/p' | tail -1
}

echo "=== 桩闭环: ${FUSE_NAME} (max=${MAX}) ==="

prev=-1
for i in $(seq 1 "$MAX"); do
    bash "$MERGE" "$MANIFEST"
    cur="$(missing_count)"
    [ -z "$cur" ] && cur=999
    echo "    轮次 $i: 缺少实现=$cur"
    if [ "$cur" -eq 0 ]; then
        echo "✅ 桩闭环完成: unmapped=0"
        exit 0
    fi
    if [ "$cur" = "$prev" ]; then
        if [ "${PLC_FUSE_STUB_STRICT:-0}" = "1" ] && [ "$cur" -gt 0 ]; then
            plc_die "$PLC_E_BUILD" "桩闭环在 missing=$cur 停滞（PLC_FUSE_STUB_STRICT=1）"
        fi
        echo "⚠️  缺桩未再减少，停止闭环"
        exit 0
    fi
    prev="$cur"
done

cur="$(missing_count)"
echo "⚠️  已达上限 ${MAX} 轮，仍缺 ${cur:-?} 个实现"
exit 0
