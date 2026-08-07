#!/bin/bash
# ============================================================================
# plc_fusion_ast_preflight__AST融合预检.sh — AST 融合可行性门禁
# ============================================================================
# 功能: 对 manifest 全部 .c 源跑 plc_ast（--no-shim --fusion-strict）
# 输出: test/${FUSE_NAME}.fusion_ast.json（主源）+ 终端摘要
# 用法: bash scripts/fuse/plc_fusion_ast_preflight__AST融合预检.sh manifests/foo.env
# 环境: FUSE_AST_PREFLIGHT=0 跳过；FUSE_AST_PREFLIGHT_STRICT=0 仅报告不 exit 1
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
plc_source_manifest "$MANIFEST"

if [ "${FUSE_AST_PREFLIGHT:-1}" != "1" ]; then
    echo "=== AST 融合预检: 跳过（FUSE_AST_PREFLIGHT=0）==="
    exit 0
fi

FUSE_WORK_DIR="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
SRC_ROOT="$(plc_fusion_resolve_src_root "$PROJECT_ROOT")"
plc_fusion_ast_extra_clang "$PROJECT_ROOT" "$SRC_ROOT"

SOURCE_PATHS=("$SRC_ROOT/$FUSE_SOURCE")
for rel in ${FUSE_EXTRA_SOURCES:-}; do
    SOURCE_PATHS+=("$SRC_ROOT/$rel")
done

PLC_AST="$PROJECT_ROOT/build/plc_ast"
AST_SRC="$PROJECT_ROOT/frontend/ast/ast_tool__AST工具.cpp"
if [ ! -x "$PLC_AST" ] || { [ -f "$AST_SRC" ] && [ "$AST_SRC" -nt "$PLC_AST" ]; }; then
    echo "    编译 plc_ast..."
    (cd "$PROJECT_ROOT/build" && cmake .. >/dev/null && make plc_ast -j"$(nproc)" >/dev/null)
fi
plc_require_file "$PLC_AST" "plc_ast"

MAIN_JSON="$FUSE_WORK_DIR/${FUSE_NAME}.fusion_ast.json"
CRIT=0
WARN=0
FAIL=0

echo "=== AST 融合预检: ${FUSE_NAME} ==="

for src in "${SOURCE_PATHS[@]}"; do
    if [ ! -f "$src" ]; then
        echo "    [CRIT] 源不存在: $src"
        CRIT=$((CRIT + 1))
        FAIL=1
        continue
    fi
    case "$src" in
        *.cpp|*.cc|*.cxx|*.C)
            if [ "${FUSE_ALLOW_CXX:-1}" != "1" ]; then
                echo "    [CRIT] C++ 源（FUSE_ALLOW_CXX=0）: $(basename "$src")"
                CRIT=$((CRIT + 1))
                FAIL=1
                continue
            fi
            echo "    scan $(basename "$src") (C++ extern-C 子集)"
            ;;
        *.c)
            ;;
        *)
            echo "    跳过非 C/C++: $(basename "$src")"
            continue
            ;;
    esac

    json="$FUSE_WORK_DIR/.fusion_ast_$(basename "$src").json"
    ast_args=(--analyze-only --fusion-strict --json="$json")
    if plc_fusion_ast_use_no_shim; then
        ast_args+=(--no-shim)
    fi
    set +e
    out=$("$PLC_AST" "${ast_args[@]}" "$src" -- \
        "${PLC_FUSION_AST_EXTRA_CLANG[@]}" 2>&1)
    rc=$?
    set -e
    echo "$out" | sed 's/^/      /'

    if [ "$src" = "$SRC_ROOT/$FUSE_SOURCE" ]; then
        cp -f "$json" "$MAIN_JSON" 2>/dev/null || true
    fi

    if [ "$rc" -ne 0 ]; then
        parse_ok=0
        if [ -f "$json" ] && command -v python3 >/dev/null; then
            if [ "$(plc_fusion_ast_json_parse_ok "$json")" = "1" ]; then
                parse_ok=1
                plc_warn "Clang 退出非 0 但 AST 已解析 entry/main: $(basename "$src")"
                rc=0
            fi
        fi
        if [ "$parse_ok" = "0" ]; then
            echo "    [CRIT] Clang/AST 解析失败（flags/include 与融合不一致或源不可编译）: $(basename "$src")"
            CRIT=$((CRIT + 1))
            FAIL=1
        fi
    fi
    if command -v python3 >/dev/null && [ -f "$json" ]; then
        read -r fc fw sched_crit sched_warn <<< "$(python3 - "$json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(
    d.get("fusion_critical_count", 0),
    d.get("fusion_warn_count", 0),
    d.get("sched_critical_count", 0),
    d.get("sched_warn_count", 0),
)
PY
)"
        CRIT=$((CRIT + fc))
        WARN=$((WARN + fw))
        if [ "${sched_crit:-0}" -gt 0 ] || [ "${sched_warn:-0}" -gt 0 ]; then
            echo "    sched_crit=$sched_crit sched_warn=$sched_warn"
        fi
    fi
done

echo "    fusion_critical=$CRIT fusion_warn=$WARN → $MAIN_JSON"
if command -v python3 >/dev/null && [ -f "$MAIN_JSON" ]; then
    read -r ires iun <<< "$(python3 - "$MAIN_JSON" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("indirect_resolved_count", 0), d.get("indirect_unresolved_count", 0))
PY
)"
    [ "${ires:-0}" -gt 0 ] && echo "    indirect_resolved=$ires indirect_unresolved=${iun:-0}"
fi

if [ "$FAIL" -ne 0 ] || [ "$CRIT" -gt 0 ]; then
    plc_warn "AST 融合预检未通过（critical=$CRIT）" \
        "见 fusion_issues / F-CRIT 行" \
        "可设 FUSE_AST_PREFLIGHT=0 跳过"
    if [ "${FUSE_AST_PREFLIGHT_STRICT:-1}" = "1" ]; then
        exit 1
    fi
elif [ "$WARN" -gt 0 ]; then
    plc_warn "AST 融合预检: $WARN 个 warning（可继续，需人工确认）"
else
    echo "✅ AST 融合预检通过"
fi

# 间接调用门禁（bsearch 等）：允许名单 FUSE_AST_INDIRECT_ALLOW，严格模式 FUSE_AST_INDIRECT_STRICT=1
if command -v python3 >/dev/null; then
    read -r indirect_bad indirect_total <<< "$(python3 - "$FUSE_WORK_DIR" "$FUSE_SOURCE" ${FUSE_EXTRA_SOURCES:-} \
        "${FUSE_AST_INDIRECT_ALLOW:-}" <<'PY'
import json, sys, os
from pathlib import Path
work, main_rel = sys.argv[1], sys.argv[2]
extra = sys.argv[3:-1]
allow_raw = sys.argv[-1]
allow = {x.strip() for x in allow_raw.split(",") if x.strip()}
paths = [Path(work) / f".fusion_ast_{Path(main_rel).name}.json"]
for rel in extra:
    paths.append(Path(work) / f".fusion_ast_{Path(rel).name}.json")
bad = []
total = 0
for p in paths:
    if not p.is_file():
        continue
    d = json.load(open(p, encoding="utf-8"))
    for issue in d.get("fusion_issues") or []:
        if issue.get("symbol") != "indirect_call":
            continue
        total += 1
        ctx = issue.get("context") or issue.get("message") or "?"
        if ctx not in allow:
            bad.append(f"{p.name}:{issue.get('line', '?')}:{ctx}")
print(len(bad), total)
if bad:
    for line in bad:
        print(line, file=sys.stderr)
PY
)" 2>/dev/null || echo "0 0"
    if [ "${indirect_total:-0}" -gt 0 ]; then
        echo "    indirect_call total=$indirect_total unallowed=${indirect_bad:-0} allow=${FUSE_AST_INDIRECT_ALLOW:-<none>}"
    fi
    if [ "${indirect_bad:-0}" -gt 0 ]; then
        plc_warn "未在白名单的 indirect_call: ${indirect_bad} 处" \
            "设置 FUSE_AST_INDIRECT_ALLOW=sym1,sym2 或修复源码"
        if [ "${FUSE_AST_INDIRECT_STRICT:-0}" = "1" ]; then
            exit 1
        fi
    fi
fi
