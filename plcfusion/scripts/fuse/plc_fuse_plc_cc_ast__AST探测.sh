#!/bin/bash
# ============================================================================
# plc_fuse_plc_cc_ast__AST探测.sh — plc-cc 静态分析 + JSON → .plc_ast.env
# ============================================================================
# 功能: 运行 plc_ast，生成 test/${FUSE_NAME}.plc_ast.json 与 .plc_ast.env
# 输入: manifest.env [source.c]
# 输出: FUSE_DETECT_PLC_CC_* 变量（可被 plc_fuse source）
# 用法: bash scripts/fuse/plc_fuse_plc_cc_ast__AST探测.sh manifests/manifest_plc_cc_foo.env
# 环境: FUSE_PLC_CC_AST_STRICT=1  周期内 malloc/浮点也视为 error
#       FUSE_PLC_CC_AST=0         跳过（manifest 可设）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
SOURCE_OVERRIDE="${2:-}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

if [ "${FUSE_PLC_CC_AST:-1}" != "1" ]; then
    echo "=== plc-cc AST: 跳过（FUSE_PLC_CC_AST=0）==="
    exit 0
fi

FUSE_WORK_DIR="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
if [ -n "${FUSE_SRC_ROOT:-}" ]; then
    SRC_ROOT="$FUSE_SRC_ROOT"
    [[ "$SRC_ROOT" != /* ]] && SRC_ROOT="$PROJECT_ROOT/$SRC_ROOT"
else
    SRC_ROOT="$PROJECT_ROOT"
fi

if [ -n "$SOURCE_OVERRIDE" ]; then
    SOURCE_PATH="$SOURCE_OVERRIDE"
else
    SOURCE_PATH="$SRC_ROOT/${FUSE_SOURCE:-}"
fi
plc_require_file "$SOURCE_PATH" "plc-cc 源文件"

plc_fusion_ast_extra_clang "$PROJECT_ROOT" "$SRC_ROOT"
AST_CLANG_EXTRA=("${PLC_FUSION_AST_EXTRA_CLANG[@]}")
AST_CLANG_EXTRA+=(-I"$PROJECT_ROOT/include")

PLC_AST="$PROJECT_ROOT/build/plc_ast"
AST_SRC="$PROJECT_ROOT/frontend/ast/ast_tool__AST工具.cpp"
if [ ! -x "$PLC_AST" ] || { [ -f "$AST_SRC" ] && [ "$AST_SRC" -nt "$PLC_AST" ]; }; then
    echo "    编译 plc_ast..."
    if ! (cd "$PROJECT_ROOT/build" && cmake .. >/dev/null && make plc_ast -j"$(nproc)" >/dev/null); then
        plc_die "$PLC_E_BUILD" "plc_ast 编译失败" "cd build && cmake .. && make plc_ast"
    fi
fi
plc_require_file "$PLC_AST" "plc_ast"

JSON_OUT="$FUSE_WORK_DIR/${FUSE_NAME}.plc_ast.json"
ENV_OUT="$FUSE_WORK_DIR/${FUSE_NAME}.plc_ast.env"

AST_ARGS=(--analyze-only --json="$JSON_OUT" --suggest-manifest="$FUSE_WORK_DIR/${FUSE_NAME}.manifest.suggest.env")
if [ "${FUSE_PLC_CC_AST_STRICT:-0}" = "1" ]; then
    AST_ARGS+=(--strict)
fi

echo "=== plc-cc AST: ${FUSE_NAME} ==="
echo "    source=$SOURCE_PATH"
echo "    json=$JSON_OUT"

if ! "$PLC_AST" "${AST_ARGS[@]}" "$SOURCE_PATH" -- "${AST_CLANG_EXTRA[@]}"; then
    plc_die "$PLC_E_BUILD" "plc_ast 分析失败（见上方 ERROR）" \
        "修复周期函数内阻塞调用，或查看 $JSON_OUT"
fi

if ! command -v python3 >/dev/null 2>&1; then
    plc_die "$PLC_E_NOCMD" "需要 python3 解析 plc_ast JSON"
fi

python3 - "$JSON_OUT" "$ENV_OUT" <<'PY'
import json, sys
from pathlib import Path

jpath, env_path = sys.argv[1], sys.argv[2]
data = json.loads(Path(jpath).read_text(encoding="utf-8"))
entry = data.get("entry") or ""
globals_list = [g.get("name", "") for g in data.get("globals", []) if g.get("name")]
globals_csv = ",".join(globals_list)
ok = 1 if data.get("ok") else 0
errs = int(data.get("error_count", 0))
warns = int(data.get("warn_count", 0))
float_flag = 1 if data.get("float_in_cycle") else 0
fusion_ok = 1 if data.get("fusion_eligible") else 0

lines = [
    f"# plc_ast env — generated from {jpath}",
    f"FUSE_DETECT_PLC_CC_ENTRY={entry}",
    f"FUSE_DETECT_PLC_CC_GLOBALS={globals_csv}",
    f"FUSE_DETECT_PLC_CC_FLOAT_IN_CYCLE={float_flag}",
    f"FUSE_DETECT_PLC_CC_AST_OK={ok}",
    f"FUSE_DETECT_PLC_CC_FUSION_ELIGIBLE={fusion_ok}",
    f"FUSE_DETECT_PLC_CC_ERROR_COUNT={errs}",
    f"FUSE_DETECT_PLC_CC_WARN_COUNT={warns}",
]
# manifest 建议见 ${FUSE_NAME}.manifest.suggest.env（已正确引号）；勿写入本 env，避免 source 时空格被当命令
Path(env_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"    entry={entry} globals={len(globals_list)} float={float_flag} ok={ok} errs={errs} warns={warns}")
PY

echo "    -> $ENV_OUT"
