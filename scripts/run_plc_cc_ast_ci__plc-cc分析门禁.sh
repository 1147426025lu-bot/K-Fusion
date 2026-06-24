#!/bin/bash
# ============================================================================
# run_plc_cc_ast_ci__plc-cc分析门禁.sh — 6 个 plc-cc 示例静态分析 CI
# ============================================================================
# 功能: 全部示例 analyze-only（无 error）；干净示例额外 --strict（无 warn）
# 用法: bash scripts/run_plc_cc_ast_ci__plc-cc分析门禁.sh
# 环境: SKIP_AST_BUILD=1  跳过 plc_ast 编译
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
PLC_AST="$PRJ/build/plc_ast"
EX_DIR="$PRJ/examples/plc-cc__低抖动示例"

ALL=(
    "gpio_blink__GPIO闪烁.c"
    "hello_plc__入门示例.c"
    "pure_logic__纯逻辑.c"
    "temp_control__温控.c"
    "isolation_test__隔离测试.c"
    "dither_test__抖动测试.c"
)
STRICT=(
    "gpio_blink__GPIO闪烁.c"
    "hello_plc__入门示例.c"
)

if [ "${SKIP_AST_BUILD:-0}" != "1" ]; then
    echo "🛠️  编译 plc_ast..."
    if ! (cd "$PRJ/build" && cmake .. >/dev/null && make plc_ast -j"$(nproc)" >/dev/null); then
        plc_die "$PLC_E_BUILD" "plc_ast 编译失败"
    fi
fi
plc_require_file "$PLC_AST" "plc_ast"

run_ast() {
    local src="$1"
    local strict="${2:-0}"
    local base
    base="$(basename "$src" .c)"
    local json="$PRJ/test/ci_plc_ast_${base}.json"
    local args=(--analyze-only --json="$json")
    [ "$strict" = "1" ] && args+=(--strict)
    echo "    -> $(basename "$src") strict=$strict"
    if ! "$PLC_AST" "${args[@]}" "$src" --; then
        plc_die "$PLC_E_BUILD" "plc_ast 失败: $src"
    fi
}

echo "=== plc-cc AST CI（${#ALL[@]} 示例）==="
for f in "${ALL[@]}"; do
    run_ast "$EX_DIR/$f" 0
done

echo "=== plc-cc AST CI strict（${#STRICT[@]} 干净示例）==="
for f in "${STRICT[@]}"; do
    run_ast "$EX_DIR/$f" 1
done

echo "✅ plc-cc AST CI 通过（${#ALL[@]} analyze + ${#STRICT[@]} strict）"

CPP_EX="$PRJ/examples/cpp_extern_c__extern-C示例子.cpp"
CPP_JSON="$PRJ/test/ci_plc_ast_cpp_extern_c.json"
echo "=== C++ extern-C 子集 + 间接调用 1 层 ==="
plc_require_file "$CPP_EX" "C++ smoke 示例"
if ! "$PLC_AST" --analyze-only --no-shim --json="$CPP_JSON" "$CPP_EX" --; then
    plc_die "$PLC_E_BUILD" "plc_ast C++ smoke 失败"
fi
python3 - "$CPP_JSON" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("cpp_subset_eligible"), "cpp_subset_eligible 应为 true"
assert d.get("fusion_eligible"), "fusion_eligible 应为 true"
assert int(d.get("indirect_resolved_count", 0)) >= 1, "应解析 1 层函数指针调用"
print(f"    cpp_ok entry={d.get('entry')} indirect_resolved={d.get('indirect_resolved_count')}")
PY
echo "✅ C++ extern-C / indirect 1-layer smoke 通过"
