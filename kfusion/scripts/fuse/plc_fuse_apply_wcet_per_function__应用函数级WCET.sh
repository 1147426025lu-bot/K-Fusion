#!/bin/bash
# ============================================================================
# plc_fuse_apply_wcet_per_function__应用函数级WCET.sh — 将 per-function WCET 写回 manifest
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
DRY="${WCET_APPLY_DRY_RUN:-0}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
SUGGEST="$WORK/${FUSE_NAME}.wcet_suggest.env"
PER_FN="$WORK/${FUSE_NAME}.wcet_per_function.env"

plc_require_file "$SUGGEST" "wcet suggest env" \
    "先运行: bash scripts/plc_fusion_wcet_per_function__函数级WCET.sh $MANIFEST"

echo "=== 应用函数级 WCET → $MANIFEST ==="
echo "    suggest=$SUGGEST"

TMP="$(mktemp)"
grep -vE '^(FUSE_PIPELINE=|FUSE_WCET_MODE=|FUSE_WCET_PER_FUNCTION=|PLC_FUSION_WCET_SCHEDULE_FILE=|FUSE_COLD_PASS_SEQUENCE=|FUSE_MODULE_PASS_SEQUENCE=)' \
    "$MANIFEST" > "$TMP" || true

{
    cat "$TMP"
    echo ""
    echo "# --- per-function WCET applied $(date -Iseconds) from $SUGGEST ---"
    grep -E '^(FUSE_PIPELINE=|FUSE_WCET_MODE=|FUSE_WCET_PER_FUNCTION=|PLC_FUSION_WCET_SCHEDULE_FILE=|FUSE_COLD_PASS_SEQUENCE=|FUSE_MODULE_PASS_SEQUENCE=)' \
        "$SUGGEST" || true
    if [ -f "$PER_FN" ]; then
        grep -E '^(FUSE_WCET_MODE=|FUSE_WCET_PER_FUNCTION=|PLC_FUSION_WCET_SCHEDULE_FILE=)' "$PER_FN" 2>/dev/null | \
            grep -vE '^(FUSE_WCET_MODE=|FUSE_WCET_PER_FUNCTION=|PLC_FUSION_WCET_SCHEDULE_FILE=)' || true
    fi
} > "${TMP}.merged"

if [ "$DRY" = "1" ]; then
    echo "--- dry-run merged tail ---"
    tail -20 "${TMP}.merged"
    rm -f "$TMP" "${TMP}.merged"
    exit 0
fi

mv "${TMP}.merged" "$MANIFEST"
rm -f "$TMP"
echo "✅ 已写回 manifest: $MANIFEST"
