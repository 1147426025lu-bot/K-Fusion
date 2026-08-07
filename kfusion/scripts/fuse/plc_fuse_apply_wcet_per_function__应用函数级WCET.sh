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

plc_require_file "$SUGGEST" "wcet suggest env" \
    "先运行: bash scripts/plc_fusion_wcet_per_function__函数级WCET.sh $MANIFEST"

echo "=== 应用函数级 WCET → $MANIFEST ==="
echo "    suggest=$SUGGEST"

STRIP='^(FUSE_PIPELINE=|FUSE_WCET_MODE=|FUSE_WCET_PER_FUNCTION=|PLC_FUSION_WCET_SCHEDULE_FILE=|FUSE_COLD_PASS_SEQUENCE=|FUSE_MODULE_PASS_SEQUENCE=)'
EXTRACT='^(FUSE_PIPELINE=|FUSE_WCET_MODE=|FUSE_WCET_PER_FUNCTION=|PLC_FUSION_WCET_SCHEDULE_FILE=|FUSE_COLD_PASS_SEQUENCE=|FUSE_MODULE_PASS_SEQUENCE=)'

if [ "$DRY" = "1" ]; then
    TMP="$(mktemp)"
    grep -vE "$STRIP" "$MANIFEST" > "$TMP" || true
    echo "--- dry-run merged tail ---"
    {
        tail -5 "$TMP"
        echo ""
        grep -E "$EXTRACT" "$SUGGEST" || true
    }
    rm -f "$TMP"
    exit 0
fi

plc_manifest_merge_env_snippet "$MANIFEST" "$STRIP" "$SUGGEST" "$EXTRACT" "per-function WCET"
echo "✅ 已写回 manifest: $MANIFEST"
