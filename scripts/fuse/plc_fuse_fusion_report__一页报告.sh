#!/bin/bash
# ============================================================================
# plc_fuse_fusion_report__一页报告.sh — 融合一页摘要报告
# ============================================================================
# 功能: 汇总 pipeline、IR 特征、探测、产物体量、入口与覆盖率摘要
# 输入: manifest.env（需已跑过 plc_fuse__内核化主流程.sh）
# 输出: test/${FUSE_NAME}.fusion_report（Markdown，约一页）
# 用法: bash scripts/plc_fuse_fusion_report__一页报告.sh manifests/foo.env
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
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
OUT="$WORK/${FUSE_NAME}.fusion_report"
KLL="$WORK/${FUSE_NAME}_kernel.ll"
PRE="$WORK/${FUSE_NAME}_pre.ll"
OBJ="$WORK/${FUSE_NAME}_kernel.o"
PIPE_LOG="$WORK/${FUSE_NAME}.pipeline.log"
IR_LOG="$WORK/${FUSE_NAME}.ir_analysis.log"
DETECTED="$WORK/${FUSE_NAME}.detected.env"
ENTRIES="$WORK/${FUSE_NAME}.entries"

plc_require_file "$KLL" "kernel IR" \
    "先运行: bash scripts/plc_fuse__内核化主流程.sh $MANIFEST"

file_lines() {
    [ -f "$1" ] && wc -l < "$1" | tr -d ' ' || echo 0
}

file_bytes() {
    [ -f "$1" ] && stat -c%s "$1" 2>/dev/null || echo 0
}

kv_from_log() {
    local key="$1" file="$2"
    [ -f "$file" ] && grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

MISSING=0
if [ -f "$KLL" ]; then
    COV_OUT="$(bash "$SCRIPT_DIR/plc_fuse_report__覆盖率报告.sh" "$MANIFEST" 2>/dev/null | tail -5 || true)"
    MISSING=$(echo "$COV_OUT" | sed -n 's/.*缺少实现: \([0-9]*\).*/\1/p' | tail -1)
    [ -z "$MISSING" ] && MISSING=0
fi

PROFILE="$(kv_from_log profile "$PIPE_LOG")"
REASON="$(kv_from_log reason "$PIPE_LOG")"
OPT="$(kv_from_log opt "$PIPE_LOG")"
HOT="$(kv_from_log hot_path "$PIPE_LOG")"
WCET="$(kv_from_log wcet_mode "$PIPE_LOG")"
IR_UNK="$(kv_from_log ir_unknown "$PIPE_LOG")"
IR_FLOAT="$(kv_from_log float_skip_ir "$PIPE_LOG")"

DETECT_KTHREAD=""
DETECT_ROOTS=""
if [ -f "$DETECTED" ]; then
    # shellcheck disable=SC1090
    source "$DETECTED"
    DETECT_KTHREAD="${FUSE_DETECT_KTHREAD_ENTRY:-}"
    DETECT_ROOTS="${FUSE_DETECT_DCE_ROOTS:-}"
fi

ENTRY_LIST=""
[ -f "$ENTRIES" ] && ENTRY_LIST="$(tr '\n' ', ' < "$ENTRIES" | sed 's/,$//')"

EXTRA_SRC="${FUSE_EXTRA_SOURCES:-}"
TU_COUNT=1
[ -n "$EXTRA_SRC" ] && TU_COUNT=$((1 + $(echo "$EXTRA_SRC" | wc -w)))

{
    echo "# PLCFusion 融合报告 — ${FUSE_NAME}"
    echo
    echo "- **描述**: ${FUSE_DESC:-$FUSE_NAME}"
    echo "- **时间**: $(date -Iseconds)"
    echo "- **manifest**: \`$MANIFEST\`"
    echo
    echo "## Pipeline"
    echo
    echo "| 项 | 值 |"
    echo "|----|-----|"
    echo "| profile | ${PROFILE:-?} |"
    echo "| reason | ${REASON:-—} |"
    echo "| opt passes | \`${OPT:-?}\` |"
    echo "| WCET 模式 | ${WCET:-0} |"
    echo "| 热路径函数 | ${HOT:-${FUSE_HOT_PATH_FUNCTIONS:-${FUSE_KTHREAD_ENTRY:-—}}} |"
    echo "| 翻译单元 | ${TU_COUNT}（主: \`${FUSE_SOURCE:-?}\`${EXTRA_SRC:+, 附加: \`$EXTRA_SRC\`}） |"
    echo
    echo "## IR / 产物"
    echo
    echo "| 文件 | 行数 | 字节 |"
    echo "|------|------|------|"
    echo "| pre.ll | $(file_lines "$PRE") | $(file_bytes "$PRE") |"
    echo "| kernel.ll | $(file_lines "$KLL") | $(file_bytes "$KLL") |"
    echo "| kernel.o | — | $(file_bytes "$OBJ") |"
    echo "| kernel.o_shipped | — | $(file_bytes "${OBJ}_shipped") |"
    echo
    echo "- unknown externs（pre）: ${IR_UNK:-?}"
    echo "- IR 无浮点跳过 float_kill: ${IR_FLOAT:-?}"
    echo
    echo "## 入口 / DCE"
    echo
    echo "- manifest kthread: \`${FUSE_KTHREAD_ENTRY:-—}\`"
    echo "- 自动探测 kthread: \`${DETECT_KTHREAD:-—}\`"
    echo "- DCE roots: \`${FUSE_DCE_ROOTS:-${DETECT_ROOTS:-—}}\`"
    echo "- 融合后入口: \`${ENTRY_LIST:-—}\`"
    echo "- globalize: \`${FUSE_GLOBALIZE_SYMBOLS:-—}\`"
    echo
    echo "## 覆盖率摘要"
    echo
    echo "- **缺少实现**: ${MISSING}"
    if [ -n "$COV_OUT" ]; then
        echo
        echo '```'
        echo "$COV_OUT"
        echo '```'
    fi
    echo
    echo "## 下一步"
    echo
    echo "- 覆盖率: \`bash scripts/plc_fuse_report__覆盖率报告.sh $MANIFEST\`"
    echo "- cyclictest: \`cd scripts/deploy && bash ignite_official_cycletest__cyclictest主线.sh\`（勿 sudo 整脚本）"
    echo "- 对比 demo: \`bash scripts/demo_compare__用户态vs融合.sh\`"
} > "$OUT"

echo "    fusion_report=$OUT"
