#!/bin/bash
# ============================================================================
# plc_fusion_analyze_ir__IR特征分析.sh — LLVM IR 特征分析（供 pipeline 自动选 profile）
# ============================================================================
# 功能: 扫描 pre.ll / kernel.ll，统计浮点 IR、外部调用、体量，预测未映射符号数
# 输入: manifest.env [ir.ll] [mode=pre|kernel]
# 输出（export）:
#   PLC_FUSION_IR_HAS_FLOAT       0|1
#   PLC_FUSION_IR_LINES           IR 行数
#   PLC_FUSION_IR_FUNC_COUNT      define 函数数
#   PLC_FUSION_IR_EXTERNAL_CALLS  活跃外部调用种类数
#   PLC_FUSION_IR_UNKNOWN_EXTERNS 无桩/无 plc_* 映射的外部符号数
#   PLC_FUSION_OBJ_BYTES          已有 kernel.o 字节数（0 表示不存在）
# 用法:
#   source scripts/plc_fusion_analyze_ir__IR特征分析.sh manifests/foo.env test/foo_pre.ll pre
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
IR_LL="${2:-}"
MODE="${3:-pre}"

if [ -z "$MANIFEST" ]; then
    plc_die "$PLC_E_USAGE" "缺少 manifest" \
        "用法: source scripts/plc_fusion_analyze_ir__IR特征分析.sh manifests/foo.env [ir.ll] [pre|kernel]"
fi
plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

IR_LL="${IR_LL:-$PROJECT_ROOT/test/${FUSE_NAME}_${MODE}.ll}"
if [ "$MODE" = "pre" ] && [ ! -f "$IR_LL" ]; then
    IR_LL="$PROJECT_ROOT/test/${FUSE_NAME}_pre.ll"
fi
if [ "$MODE" = "kernel" ] && [ ! -f "$IR_LL" ]; then
    IR_LL="$PROJECT_ROOT/test/${FUSE_NAME}_kernel.ll"
fi

OBJ_PATH="$PROJECT_ROOT/test/${FUSE_NAME}_kernel.o"
STUBS="${PLC_RUNTIME_STUBS:-$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c}"
APP_STUBS="$PROJECT_ROOT/test/${FUSE_NAME}_runtime_stubs.c"
[ -f "$APP_STUBS" ] && STUBS="$APP_STUBS"
RUNNER="${PLC_RUNNER_STUBS:-$PROJECT_ROOT/src/plc_runner_official__cyclictest宿主.c}"

if [ ! -f "$IR_LL" ]; then
    plc_die "$PLC_E_IR" "IR 文件不存在: $IR_LL" \
        "pre 模式需先完成 plc_fuse [4/6] 预清理" \
        "kernel 模式需先完成 [5/6] Pass"
fi

PASS_CPP="$PROJECT_ROOT/backend/pass/PLCFusionPass__内核化Pass.cpp"
load_remap_syms() {
    [ -f "$PASS_CPP" ] || return 0
    grep -E '^\s+\{"[a-zA-Z_]' "$PASS_CPP" | sed -E 's/.*\{"([^"]+)".*/\1/'
}
load_preserved_syms() {
    [ -f "$PASS_CPP" ] || return 0
    sed -n '/kKeep\[\]/,/\};/p' "$PASS_CPP" | grep -E '"[a-zA-Z_]' | sed -E 's/.*"([^"]+)".*/\1/'
}

is_remap_or_preserved() {
    local sym="$1" s
    for s in "${REMAP_SYMS[@]}"; do [ "$sym" = "$s" ] && return 0; done
    for s in "${PRESERVED_SYMS[@]}"; do [ "$sym" = "$s" ] && return 0; done
    return 1
}

REMAP_SYMS=()
while IFS= read -r s; do [ -n "$s" ] && REMAP_SYMS+=("$s"); done < <(load_remap_syms || true)
PRESERVED_SYMS=()
while IFS= read -r s; do [ -n "$s" ] && PRESERVED_SYMS+=("$s"); done < <(load_preserved_syms || true)

# --- 浮点 IR：Pass 内也会再判，此处供 shell pipeline 提前关 float_kill ---
has_float_ir() {
    grep -qE \
        ' (float|double) | fadd | fsub | fmul | fdiv | fcmp | fpext | fptrunc | sitofp | uitofp | fptosi | fptoui ' \
        "$IR_LL" 2>/dev/null
}

# --- 活跃外部符号（有 call/invoke）---
live_externals() {
    local sym
    grep -oE '@[A-Za-z_][A-Za-z0-9_]*' "$IR_LL" | sed 's/^@//' | sort -u | while read -r sym; do
        [[ "$sym" == llvm.* ]] && continue
        [[ "$sym" == plc_* ]] && continue
        grep -qE "(call|invoke)[^@]*@${sym}[( ]" "$IR_LL" || continue
        echo "$sym"
    done
}

has_impl() {
    local sym="$1"
    grep -qE "\\b${sym}\\b" "$STUBS" 2>/dev/null && return 0
    grep -qE "\\b${sym}\\b" "$RUNNER" 2>/dev/null && return 0
    return 1
}

PLC_FUSION_IR_HAS_FLOAT=0
has_float_ir && PLC_FUSION_IR_HAS_FLOAT=1

PLC_FUSION_IR_LINES=$(wc -l < "$IR_LL" | tr -d ' ')
PLC_FUSION_IR_FUNC_COUNT=$(grep -cE '^define ' "$IR_LL" 2>/dev/null || echo 0)

EXTERNALS=()
UNKNOWN=0
while IFS= read -r sym; do
    [ -z "$sym" ] && continue
    EXTERNALS+=("$sym")
    is_remap_or_preserved "$sym" && continue
    has_impl "$sym" || UNKNOWN=$((UNKNOWN + 1))
done < <(live_externals || true)

PLC_FUSION_IR_EXTERNAL_CALLS=${#EXTERNALS[@]}
PLC_FUSION_IR_UNKNOWN_EXTERNS=$UNKNOWN

PLC_FUSION_OBJ_BYTES=0
if [ -f "$OBJ_PATH" ]; then
    PLC_FUSION_OBJ_BYTES=$(stat -c%s "$OBJ_PATH" 2>/dev/null || stat -f%z "$OBJ_PATH" 2>/dev/null || echo 0)
fi

export PLC_FUSION_IR_HAS_FLOAT
export PLC_FUSION_IR_LINES
export PLC_FUSION_IR_FUNC_COUNT
export PLC_FUSION_IR_EXTERNAL_CALLS
export PLC_FUSION_IR_UNKNOWN_EXTERNS
export PLC_FUSION_OBJ_BYTES

LOG="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}/${FUSE_NAME}.ir_analysis.log"
{
    echo "# PLCFusion IR analysis $(date -Iseconds)"
    echo "ir=$IR_LL"
    echo "mode=$MODE"
    echo "has_float=$PLC_FUSION_IR_HAS_FLOAT"
    echo "lines=$PLC_FUSION_IR_LINES"
    echo "funcs=$PLC_FUSION_IR_FUNC_COUNT"
    echo "external_calls=$PLC_FUSION_IR_EXTERNAL_CALLS"
    echo "unknown_externs=$PLC_FUSION_IR_UNKNOWN_EXTERNS"
    echo "obj_bytes=$PLC_FUSION_OBJ_BYTES"
} >> "$LOG"

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    echo "ir=$IR_LL mode=$MODE"
    echo "has_float=$PLC_FUSION_IR_HAS_FLOAT lines=$PLC_FUSION_IR_LINES funcs=$PLC_FUSION_IR_FUNC_COUNT"
    echo "external_calls=$PLC_FUSION_IR_EXTERNAL_CALLS unknown_externs=$PLC_FUSION_IR_UNKNOWN_EXTERNS"
    echo "obj_bytes=$PLC_FUSION_OBJ_BYTES"
fi
