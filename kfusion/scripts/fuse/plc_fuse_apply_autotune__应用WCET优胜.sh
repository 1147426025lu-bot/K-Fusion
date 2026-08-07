#!/bin/bash
# ============================================================================
# plc_fuse_apply_autotune__应用WCET优胜.sh — 将 autotune/genetic 结果写回 manifest
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
SOURCE="${WCET_APPLY_SOURCE:-autotune}"
DRY="${WCET_APPLY_DRY_RUN:-0}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
case "$SOURCE" in
    autotune) ENV_FILE="$WORK/${FUSE_NAME}.autotune.env" ;;
    genetic)  ENV_FILE="$WORK/${FUSE_NAME}.genetic.env" ;;
    *) plc_die "$PLC_E_USAGE" "未知 WCET_APPLY_SOURCE=$SOURCE" "autotune|genetic" ;;
esac

plc_require_file "$ENV_FILE" "autotune env ($ENV_FILE)" \
    "先运行 plc_fusion_wcet_autotune 或 plc_fusion_wcet_genetic"

echo "=== 应用 WCET 优胜 → $MANIFEST ==="
echo "    source=$SOURCE env=$ENV_FILE"

STRIP='^(FUSE_PIPELINE=|FUSE_KERNEL_PASS=|FUSE_TAIL_PASSES=|FUSE_COLD_TAIL_PASSES=|FUSE_COLD_PASS_SEQUENCE=|FUSE_MODULE_PASS_SEQUENCE=|FUSE_WCET_MODE=|FUSE_PRE_PASSES=)'
EXTRACT='^(FUSE_PIPELINE=|FUSE_KERNEL_PASS=|FUSE_TAIL_PASSES=|FUSE_COLD_TAIL_PASSES=|FUSE_COLD_PASS_SEQUENCE=|FUSE_MODULE_PASS_SEQUENCE=|FUSE_WCET_MODE=|FUSE_PRE_PASSES=)'

if [ "$DRY" = "1" ]; then
    TMP="$(mktemp)"
    grep -vE "$STRIP" "$MANIFEST" > "$TMP" || true
    echo "--- dry-run merged tail ---"
    {
        tail -5 "$TMP"
        echo ""
        grep -E "$EXTRACT" "$ENV_FILE" || true
    }
    rm -f "$TMP"
    exit 0
fi

plc_manifest_merge_env_snippet "$MANIFEST" "$STRIP" "$ENV_FILE" "$EXTRACT" "WCET autotune"
echo "✅ 已写回 manifest: $MANIFEST"
