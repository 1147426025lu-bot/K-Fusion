#!/bin/bash
# ============================================================================
# plc_fuse_apply_autotune__应用WCET优胜.sh — 将 autotune/genetic 结果写回 manifest
# ============================================================================
# 用法:
#   bash scripts/plc_fuse_apply_autotune__应用WCET优胜.sh \
#     manifests/manifest_cyclictest__主线压测.env
# 环境:
#   WCET_APPLY_SOURCE=autotune|genetic   默认 autotune（.autotune.env）
#   WCET_APPLY_DRY_RUN=1                 只打印不写文件
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

TMP="$(mktemp)"
grep -vE '^(FUSE_PIPELINE=|FUSE_KERNEL_PASS=|FUSE_TAIL_PASSES=|FUSE_COLD_TAIL_PASSES=|FUSE_COLD_PASS_SEQUENCE=|FUSE_MODULE_PASS_SEQUENCE=|FUSE_WCET_MODE=|FUSE_PRE_PASSES=)' \
    "$MANIFEST" > "$TMP" || true

{
    cat "$TMP"
    echo ""
    echo "# --- WCET autotune applied $(date -Iseconds) from $ENV_FILE ---"
    grep -E '^(FUSE_PIPELINE=|FUSE_KERNEL_PASS=|FUSE_TAIL_PASSES=|FUSE_COLD_TAIL_PASSES=|FUSE_COLD_PASS_SEQUENCE=|FUSE_MODULE_PASS_SEQUENCE=|FUSE_WCET_MODE=|FUSE_PRE_PASSES=)' \
        "$ENV_FILE" || true
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
