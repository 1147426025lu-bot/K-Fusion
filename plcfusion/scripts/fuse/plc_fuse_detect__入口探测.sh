#!/bin/bash
# ============================================================================
# plc_fuse_detect__入口探测.sh — 融合入口 / DCE roots 自动探测
# ============================================================================
# 功能: 从 pre.ll 解析 pthread_create、signal handler、线程函数，推断
#       FUSE_KTHREAD_ENTRY、FUSE_DCE_ROOTS、FUSE_RUN_MAIN
# 输入: manifest.env [pre.ll] [source.c]
# 输出: test/${FUSE_NAME}.detected.env（逗号分隔列表，可安全 source）
# 用法: bash scripts/plc_fuse_detect__入口探测.sh manifests/foo.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
PRE_LL="${2:-}"
SOURCE_PATH="${3:-}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

PRE_LL="${PRE_LL:-$PROJECT_ROOT/test/${FUSE_NAME}_pre.ll}"
OUT="$PROJECT_ROOT/test/${FUSE_NAME}.detected.env"

if [ ! -f "$PRE_LL" ]; then
    plc_die "$PLC_E_NOFILE" "未找到 pre.ll: $PRE_LL" \
        "先运行: bash scripts/plc_fuse__内核化主流程.sh $MANIFEST" \
        "或传入第二参数指定 IR 路径"
fi

if [ ! -s "$PRE_LL" ]; then
    plc_die "$PLC_E_IR" "pre.ll 为空: $PRE_LL" \
        "Clang/opt 预清理可能失败，检查上游源文件"
fi

# --- 从 IR 提取 pthread_create 第 3 个实参（start_routine）---
pthread_workers() {
    grep -E 'call .*@pthread_create\(' "$PRE_LL" 2>/dev/null | \
        sed -n 's/.*@pthread_create([^,]*,[^,]*,[[:space:]]*[^@]*@\([A-Za-z_][A-Za-z0-9_]*\).*/\1/p' | \
        sort -u || true
}

signal_handlers() {
    grep -E 'call .*@signal\(' "$PRE_LL" 2>/dev/null | \
        sed -n 's/.*@signal([^,]*,[[:space:]]*[^@]*@\([A-Za-z_][A-Za-z0-9_]*\).*/\1/p' | \
        sort -u || true
}

defined_thread_like() {
    grep -E '^define .* @(.*thread|.*handler|worker|plc_cycle|plc_main|plc_logic)\(' "$PRE_LL" 2>/dev/null | \
        sed -E 's/^define .* @([^ (]+).*/\1/' | sort -u || true
}

volatile_globals_from_source() {
    local src="$1"
    [ -z "$src" ] || [ ! -f "$src" ] && return
    grep -E 'volatile[[:space:]]+(int|long|unsigned|short|char|_Bool)' "$src" 2>/dev/null | \
        sed -n 's/.*volatile[[:space:]]\+[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]\+\([a-zA-Z_][a-zA-Z0-9_]*\).*/\1/p' | \
        sort -u | paste -sd, - || true
}

has_main() {
    grep -qE '^define .* @main\(' "$PRE_LL" || false
}

pick_kthread_entry() {
    local manual="${FUSE_KTHREAD_ENTRY:-}"
    local run_main="${FUSE_RUN_MAIN:-0}"
    local candidates=""

    if [ "$run_main" = "1" ]; then
        echo ""
        return
    fi
    if [ -n "$manual" ]; then
        echo "$manual"
        return
    fi

    candidates="$(defined_thread_like)"
    if [ -z "$candidates" ]; then
        candidates="$(pthread_workers)"
    fi

    if [ -n "$candidates" ]; then
        if echo "$candidates" | grep -qx timerthread; then
            echo timerthread
            return
        fi
        echo "$candidates" | head -1
        return
    fi
    echo ""
}

pick_dce_roots() {
    local manual="${FUSE_DCE_ROOTS:-}"
    local run_main="${FUSE_RUN_MAIN:-0}"
    local roots="" w h d sym

    if [ -n "$manual" ]; then
        echo "$manual"
        return
    fi

    if [ "$run_main" = "1" ]; then
        roots="main"
    elif [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
        roots="$FUSE_KTHREAD_ENTRY"
    fi

    w="$(pthread_workers)"
    h="$(signal_handlers)"
    d="$(defined_thread_like)"

    for sym in $w $h $d; do
        [ -z "$sym" ] && continue
        [ "$sym" = main ] && continue
        echo "$roots" | grep -qw "$sym" && continue
        roots="${roots:+$roots,}$sym"
    done

    echo "$roots"
}

suggest_run_main() {
    local run_main="${FUSE_RUN_MAIN:-0}"
    if [ "$run_main" = "1" ]; then
        echo 1
        return
    fi
    if [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
        echo 0
        return
    fi
    if has_main && [ -n "$(pthread_workers)" ]; then
        echo 1
        return
    fi
    if has_main && [ -z "$(pick_kthread_entry)" ]; then
        echo 1
        return
    fi
    echo 0
}

DETECT_KTHREAD="$(pick_kthread_entry)"
DETECT_DCE="$(pick_dce_roots)"
DETECT_RUN_MAIN="$(suggest_run_main)"
DETECT_PTHREAD="$(pthread_workers | paste -sd, - || true)"
DETECT_SIGNAL="$(signal_handlers | paste -sd, - || true)"
DETECT_DEFINED="$(defined_thread_like | paste -sd, - || true)"
DETECT_GLOBALS="$(volatile_globals_from_source "$SOURCE_PATH")"

plc_ensure_dir "$(dirname "$OUT")"
{
    echo "# PLCFusion auto-detect — $(date -Iseconds)"
    echo "# source: $PRE_LL"
    echo "FUSE_DETECT_KTHREAD_ENTRY=${DETECT_KTHREAD:-}"
    echo "FUSE_DETECT_DCE_ROOTS=${DETECT_DCE:-}"
    echo "FUSE_DETECT_RUN_MAIN=${DETECT_RUN_MAIN}"
    echo "FUSE_DETECT_PTHREAD_WORKERS=${DETECT_PTHREAD:-}"
    echo "FUSE_DETECT_SIGNAL_HANDLERS=${DETECT_SIGNAL:-}"
    echo "FUSE_DETECT_THREAD_LIKE=${DETECT_DEFINED:-}"
    echo "FUSE_DETECT_GLOBALS=${DETECT_GLOBALS:-}"
} > "$OUT"

AST_ENV="$PROJECT_ROOT/test/${FUSE_NAME}.plc_ast.env"
if [ -f "$AST_ENV" ]; then
    echo "# --- plc_ast ---" >> "$OUT"
    grep '^FUSE_DETECT_PLC_CC_' "$AST_ENV" >> "$OUT" || true
fi

echo "=== PLCFusion detect: ${FUSE_NAME} ==="
echo "    kthread_entry=${DETECT_KTHREAD:-<none>}"
echo "    dce_roots=${DETECT_DCE:-<none>}"
echo "    suggest_run_main=${DETECT_RUN_MAIN}"
[ -n "$DETECT_PTHREAD" ] && echo "    pthread_workers: $DETECT_PTHREAD"
[ -n "$DETECT_SIGNAL" ] && echo "    signal_handlers: $DETECT_SIGNAL"
[ -n "$DETECT_GLOBALS" ] && echo "    volatile_globals: $DETECT_GLOBALS"
echo "    -> $OUT"

if [ -z "$DETECT_KTHREAD" ] && [ "$DETECT_RUN_MAIN" != "1" ]; then
    plc_warn "未能自动推断 kthread 入口" \
        "在 manifest 设置 FUSE_KTHREAD_ENTRY=your_thread 或 FUSE_RUN_MAIN=1"
fi
