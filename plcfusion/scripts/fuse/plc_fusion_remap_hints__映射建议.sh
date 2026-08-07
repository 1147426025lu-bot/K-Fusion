#!/bin/bash
# ============================================================================
# plc_fusion_remap_hints__映射建议.sh — 未映射符号 → Pass remap 建议
# ============================================================================
# 功能: 汇总 .unmapped + 覆盖率缺桩符号，对照 plc_remap_hint_for_sym 输出建议
# 输入: manifest.env（须已有 _kernel.ll 或 .unmapped）
# 输出: test/${FUSE_NAME}.remap_hints
# 用法: bash scripts/plc_fusion_remap_hints__映射建议.sh manifests/foo.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

UNMAPPED="$PROJECT_ROOT/test/${FUSE_NAME}.unmapped"
KLL="$PROJECT_ROOT/test/${FUSE_NAME}_kernel.ll"
OUT="$PROJECT_ROOT/test/${FUSE_NAME}.remap_hints"
REPORT="$SCRIPT_DIR/plc_fuse_report__覆盖率报告.sh"

collect_syms() {
    local sym
    if [ -f "$UNMAPPED" ]; then
        sort -u "$UNMAPPED" | while read -r sym; do
            [[ "$sym" == indirect:* ]] && echo "$sym" && continue
            echo "$sym"
        done
    fi
    if [ -f "$KLL" ] && [ -f "$REPORT" ]; then
        "$REPORT" "$MANIFEST" 2>/dev/null | sed -n 's/^  ❌ //p' || true
    fi
}

{
    echo "# remap hints for ${FUSE_NAME}"
    echo "# manifest: $MANIFEST"
    echo "# action: pass=加入 kRemap 后重跑 plc_fuse; stub=runtime 桩; refusion=重跑融合"
    echo
    while IFS= read -r sym; do
        [ -z "$sym" ] && continue
        [[ "$sym" == plc_* ]] && continue
        hint="$(plc_remap_hint_for_sym "$sym" 2>/dev/null || true)"
        if [[ "$sym" == indirect:* ]]; then
            echo "${sym} → Pass v3.5 resolveIndirectCallee（GEP/常量函数表）；仍失败则 blackhole"
            continue
        fi
        if [ -n "$hint" ]; then
            case "$hint" in
                stub:*)
                    echo "${sym} → ${hint#stub:} (runtime stub)"
                    ;;
                *)
                    echo "${sym} → ${hint} (Pass kRemap → refusion)"
                    ;;
            esac
        else
            echo "${sym} → (unknown — 需手写 stub 或 Pass 规则)"
        fi
    done < <(collect_syms | sort -u)
} > "$OUT"

total="$(grep -c '→' "$OUT" 2>/dev/null || true)"
total="${total:-0}"
known="$(grep -Ec 'Pass kRemap|runtime stub' "$OUT" 2>/dev/null || true)"
known="${known:-0}"
if [ "$total" -eq 0 ]; then
    echo "✅ remap hints: 无缺桩/未映射符号 → $OUT"
else
    echo "✅ remap hints: ${known}/${total} 有建议 → $OUT"
fi
