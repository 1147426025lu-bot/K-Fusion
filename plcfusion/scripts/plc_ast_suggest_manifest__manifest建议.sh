#!/bin/bash
# ============================================================================
# plc_ast_suggest_manifest__manifest建议.sh — 从源码生成 manifest 建议片段
# ============================================================================
# 用法:
#   bash scripts/plc_ast_suggest_manifest__manifest建议.sh path/to/app.c
#   bash scripts/plc_ast_suggest_manifest__manifest建议.sh app.c my_app.env
# 输出:
#   test/suggest_<base>.plc_ast.json
#   test/suggest_<base>.manifest.suggest.env  （或第二参数路径）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
SOURCE="${1:-}"
OUT_MANIFEST="${2:-}"

if [ -z "$SOURCE" ]; then
    plc_die "$PLC_E_ARGS" "用法: $0 <source.c> [out.manifest.env]"
fi
[[ "$SOURCE" != /* ]] && SOURCE="$PROJECT_ROOT/$SOURCE"
plc_require_file "$SOURCE" "源文件"

BASE="$(basename "$SOURCE" .c)"
PLC_AST="$PROJECT_ROOT/build/plc_ast"
if [ ! -x "$PLC_AST" ]; then
    (cd "$PROJECT_ROOT/build" && cmake .. >/dev/null && make plc_ast -j"$(nproc)" >/dev/null)
fi

JSON="$PROJECT_ROOT/test/suggest_${BASE}.plc_ast.json"
SUGGEST="${OUT_MANIFEST:-$PROJECT_ROOT/test/suggest_${BASE}.manifest.suggest.env}"

USE_SHIM=()
if [[ "$SOURCE" == *plc-cc* ]] || [[ "$SOURCE" == *examples/plc-cc* ]]; then
    :
else
    USE_SHIM=(--no-shim)
fi

echo "=== manifest 建议: $(basename "$SOURCE") ==="
"$PLC_AST" --analyze-only "${USE_SHIM[@]}" \
    --json="$JSON" --suggest-manifest="$SUGGEST" "$SOURCE" --

echo "    json=$JSON"
echo "    suggest=$SUGGEST"
echo ""
echo "--- 建议片段 ---"
cat "$SUGGEST"
echo ""
echo "下一步: bash scripts/plc_fuse_add__接入应用.sh --local $SOURCE --name <app> [--insmod]"
echo "        或: cp manifests/manifest_template__清单模板.env manifests/manifest_${BASE}.env"
