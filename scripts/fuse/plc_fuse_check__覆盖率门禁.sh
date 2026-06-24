#!/bin/bash
# ============================================================================
# plc_fuse_check__覆盖率门禁.sh — 融合覆盖率门禁
# ============================================================================
# 功能: 调用 plc_fuse_report__覆盖率报告.sh，缺桩数 > MAX_UNMAPPED 则失败（CI/门禁）
# 输入: manifest.env
# 环境: MAX_UNMAPPED / FUSE_MAX_UNMAPPED（默认 25）
# 用法: bash scripts/plc_fuse_check__覆盖率门禁.sh manifests/foo.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

MANIFEST="${1:-${PLC_FUSE_MANIFEST:-manifests/manifest_cyclictest__主线压测.env}}"
plc_resolve_manifest "$MANIFEST" "$(plc_project_root)"
MANIFEST="$PLC_MANIFEST"

if [ ! -f "$MANIFEST" ]; then
    plc_die "$PLC_E_NOFILE" "manifest 不存在: $MANIFEST"
fi
# shellcheck disable=SC1090
source "$MANIFEST"
MAX_UNMAPPED="${MAX_UNMAPPED:-${FUSE_MAX_UNMAPPED:-25}}"

REPORT=""
REPORT_RC=0
REPORT="$("$SCRIPT_DIR/plc_fuse_report__覆盖率报告.sh" "$MANIFEST" 2>&1)" || REPORT_RC=$?

if [ "$REPORT_RC" -ne 0 ]; then
    echo "$REPORT" >&2
    plc_die "$REPORT_RC" "覆盖率报告生成失败" \
        "先成功运行: bash scripts/plc_fuse__内核化主流程.sh $MANIFEST"
fi

COUNT=$(echo "$REPORT" | sed -n 's/^缺少实现.*: \([0-9]*\)/\1/p')

if [ -z "$COUNT" ]; then
    plc_die "$PLC_E_IR" "无法解析覆盖率报告中的「缺少实现」计数" \
        "确认 plc_fuse_report__覆盖率报告.sh 输出格式未变" \
        "手动: bash scripts/plc_fuse_report__覆盖率报告.sh $MANIFEST"
fi

echo "$REPORT" | tail -8

if [ "$COUNT" -le "$MAX_UNMAPPED" ]; then
    echo "✅ PASS: unmapped=$COUNT <= $MAX_UNMAPPED"
    exit 0
fi

echo ""
plc_die "$PLC_E_BUILD" "FAIL: unmapped=$COUNT > $MAX_UNMAPPED" \
    "扩展 backend/pass/PLCFusionPass__内核化Pass.cpp 的 kRemap[]" \
    "或在 src/plc_runtime_stubs__POSIX桩.c 添加桩" \
    "运行: bash scripts/plc_fuse_merge_stubs__桩合并.sh $MANIFEST"
