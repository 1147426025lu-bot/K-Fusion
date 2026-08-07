#!/bin/bash
# ============================================================================
# plc_ast_apply_manifest__应用manifest建议.sh — 将 AST 建议合并进 manifest
# ============================================================================
# 用法:
#   bash scripts/plc_ast_apply_manifest__应用manifest建议.sh manifests/foo.env
#   bash scripts/plc_ast_apply_manifest__应用manifest建议.sh manifests/foo.env --dry-run
#   bash scripts/plc_ast_apply_manifest__应用manifest建议.sh manifests/foo.env --force
#
# 默认仅填充 manifest 中缺失或为空的键；--force 覆盖已有值。
# 等价于 plc_ast 分析后手动合并 .manifest.suggest.env。
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST=""
DRY_RUN=0
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --force) FORCE=1 ;;
        -h|--help)
            echo "用法: $0 <manifest.env> [--dry-run] [--force]"
            exit 0
            ;;
        *)
            if [ -z "$MANIFEST" ]; then
                MANIFEST="$1"
            else
                plc_die "$PLC_E_ARGS" "未知参数: $1"
            fi
            ;;
    esac
    shift
done

if [ -z "$MANIFEST" ]; then
    plc_die "$PLC_E_ARGS" "用法: $0 <manifest.env> [--dry-run] [--force]"
fi

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST"

FUSE_WORK_DIR="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
if [ -n "${FUSE_SRC_ROOT:-}" ]; then
    [[ "$FUSE_SRC_ROOT" = /* ]] && SRC_ROOT="$FUSE_SRC_ROOT" || SRC_ROOT="$PROJECT_ROOT/$FUSE_SRC_ROOT"
else
    SRC_ROOT="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
fi
if [ -n "${FUSE_GIT_DIR:-}" ]; then
    SRC_ROOT="$FUSE_WORK_DIR/$FUSE_GIT_DIR"
fi

SOURCE_PATH="$SRC_ROOT/$FUSE_SOURCE"
plc_require_file "$SOURCE_PATH" "主源文件"

PLC_AST="$PROJECT_ROOT/build/plc_ast"
AST_SRC="$PROJECT_ROOT/frontend/ast/ast_tool__AST工具.cpp"
if [ ! -x "$PLC_AST" ] || { [ -f "$AST_SRC" ] && [ "$AST_SRC" -nt "$PLC_AST" ]; }; then
    echo "    编译 plc_ast..."
    (cd "$PROJECT_ROOT/build" && cmake .. >/dev/null && make plc_ast -j"$(nproc)" >/dev/null)
fi
plc_require_file "$PLC_AST" "plc_ast"

JSON="$FUSE_WORK_DIR/${FUSE_NAME}.apply_ast.json"
SUGGEST="$FUSE_WORK_DIR/${FUSE_NAME}.manifest.suggest.env"

AST_ARGS=(--analyze-only --json="$JSON" --suggest-manifest="$SUGGEST")
extra_clang=()
if [[ "${FUSE_NAME:-}" == plc_cc_* ]] || [[ "${FUSE_SOURCE:-}" == *plc-cc* ]]; then
    # plc_ast 自带 shim 路径探测；勿再传 manifest 里的 -include shim
    :
else
    AST_ARGS+=(--no-shim)
    if [ -n "${FUSE_CLANG_FLAGS:-}" ]; then
        # shellcheck disable=SC2206
        extra_clang=($FUSE_CLANG_FLAGS)
    fi
fi
if [ -n "${FUSE_INCLUDE_DIRS:-}" ]; then
    for d in ${FUSE_INCLUDE_DIRS}; do
        if [[ "$d" = /* ]]; then
            extra_clang+=(-I"$d")
        elif [ -n "${FUSE_SRC_ROOT:-}" ]; then
            extra_clang+=(-I"$SRC_ROOT/$d")
        else
            extra_clang+=(-I"$PROJECT_ROOT/$d")
        fi
    done
fi

echo "=== apply manifest 建议: ${FUSE_NAME} ==="
echo "    manifest=$MANIFEST"
echo "    source=$SOURCE_PATH"
if [ "$DRY_RUN" = "1" ]; then
    echo "    mode=dry-run"
elif [ "$FORCE" = "1" ]; then
    echo "    mode=force"
else
    echo "    mode=fill-empty"
fi

set +e
out=$("$PLC_AST" "${AST_ARGS[@]}" "$SOURCE_PATH" -- "${extra_clang[@]}" 2>&1)
rc=$?
set -e
echo "$out" | sed 's/^/      /'
if [ "$rc" -ne 0 ] && [ ! -f "$JSON" ]; then
    plc_die "$PLC_E_BUILD" "plc_ast 分析失败，无法生成建议"
fi

if ! command -v python3 >/dev/null; then
    plc_die "$PLC_E_NOCMD" "需要 python3 合并 manifest"
fi

python3 - "$MANIFEST" "$JSON" "$FORCE" "$DRY_RUN" <<'PY'
import json, re, sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
json_path = Path(sys.argv[2])
force = sys.argv[3] == "1"
dry = sys.argv[4] == "1"

PROTECTED = {
    "FUSE_NAME", "FUSE_SOURCE", "FUSE_DESC", "FUSE_SRC_ROOT",
    "FUSE_GIT_DIR", "FUSE_GIT_URL", "FUSE_GIT_BRANCH", "FUSE_EXTRA_SOURCES",
    "FUSE_WORK_DIR", "FUSE_GIT_DIR",
}

data = json.loads(json_path.read_text(encoding="utf-8"))
suggestions = data.get("manifest_suggestions") or {}
if not suggestions:
    print("    无 manifest_suggestions，跳过")
    sys.exit(0)

lines = manifest_path.read_text(encoding="utf-8").splitlines(keepends=True)
key_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

current = {}
for line in lines:
    m = key_re.match(line.strip())
    if m:
        current[m.group(1)] = m.group(2)

def norm_val(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)

def is_empty(val):
    if val is None:
        return True
    s = val.strip()
    return s in ("", "''", '""')

def fmt_line(key, val):
    val_s = norm_val(val)
    if "'" in val_s or " " in val_s or "," in val_s:
        esc = val_s.replace("'", "'\\''")
        return f"{key}='{esc}'\n"
    return f"{key}={val_s}\n"

changes = []
for key, val in suggestions.items():
    if key in PROTECTED:
        continue
    val_s = norm_val(val)
    old = current.get(key)
    if force or is_empty(old):
        if old is None or old.strip() != val_s:
            changes.append((key, old, val_s))

if not changes:
    print("    无变更（manifest 已包含建议或无可写字段）")
    sys.exit(0)

if dry:
    print("    将变更:")
    for key, old, new in changes:
        print(f"      {key}: {old or '<missing>'} -> {new}")
    sys.exit(0)

def dedupe_env_lines(raw_lines):
    """同一 key 只保留最后一次出现（去掉 manifest 重复行）。"""
    seen = set()
    out = []
    for line in reversed(raw_lines):
        m = key_re.match(line.strip())
        if m:
            k = m.group(1)
            if k in seen:
                continue
            seen.add(k)
        out.append(line if line.endswith("\n") else line + "\n")
    out.reverse()
    return out

keys_seen = set()
new_lines = []
for line in lines:
    m = key_re.match(line.strip())
    if m:
        key = m.group(1)
        keys_seen.add(key)
        if key in suggestions and key not in PROTECTED:
            old = current.get(key)
            if force or is_empty(old):
                new_lines.append(fmt_line(key, suggestions[key]))
                continue
    new_lines.append(line if line.endswith("\n") else line + "\n")

missing = [k for k in suggestions if k not in keys_seen and k not in PROTECTED]
if missing:
    new_lines.append("\n# plc_ast auto-applied suggestions\n")
    for key in sorted(missing):
        new_lines.append(fmt_line(key, suggestions[key]))

new_lines = dedupe_env_lines(new_lines)
manifest_path.write_text("".join(new_lines), encoding="utf-8")
print(f"    已写入 {len(changes)} 项 -> {manifest_path}")
for key, old, new in changes:
    print(f"      {key}: {old or '<missing>'} -> {new}")
PY

if [ "$DRY_RUN" != "1" ]; then
    echo "✅ manifest 已更新"
fi
