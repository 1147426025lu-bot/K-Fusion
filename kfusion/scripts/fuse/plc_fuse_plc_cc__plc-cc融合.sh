#!/bin/bash
# ============================================================================
# plc_fuse_plc_cc__plc-cc融合.sh — plc_ast 预检 + PLCFusion（shim，无 .kernel.c）
# ============================================================================
# 用法:
#   bash scripts/fuse/plc_fuse_plc_cc__plc-cc融合.sh examples/plc-cc__低抖动示例/foo.c
#   bash scripts/fuse/plc_fuse_plc_cc__plc-cc融合.sh foo.c manifests/manifest_plc_cc_foo.env
# 流程: plc_fuse 内 [2c] 自动跑 plc_ast → JSON → 融合
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
SOURCE="${1:-}"
MANIFEST="${2:-}"

if [ -z "$SOURCE" ]; then
    plc_die "$PLC_E_ARGS" "用法: $0 <source.c> [manifest.env]"
fi

if [[ "$SOURCE" != /* ]]; then
    SOURCE="$PROJECT_ROOT/$SOURCE"
fi
plc_require_file "$SOURCE" "plc-cc 源文件"

BASE="$(basename "$SOURCE" .c)"
if [ -z "$MANIFEST" ]; then
    MANIFEST="$(find "$PROJECT_ROOT/manifests" -maxdepth 1 -name "manifest_plc_cc_*${BASE}*.env" 2>/dev/null | head -1)"
    if [ -z "$MANIFEST" ]; then
        MANIFEST="$PROJECT_ROOT/manifests/manifest_plc_cc_gpio__PLC示例.env"
        plc_warn "未找到专用 manifest，回退: $MANIFEST"
    fi
fi
plc_require_file "$MANIFEST" "manifest"

echo ">>> [plc-cc] 融合 manifest=$(basename "$MANIFEST")（plc_fuse 内含 AST 分析）"
bash "$SCRIPT_DIR/plc_fuse__内核化主流程.sh" "$MANIFEST"
