#!/bin/bash
# ============================================================================
# plc_fusion_ast_plan__AST方案读取.sh — 加载 AST JSON → fusion_plan + export
# ============================================================================
# 用法: source scripts/fuse/plc_fusion_ast_plan__AST方案读取.sh manifests/foo.env
# 环境: FUSE_AST_PLAN=0 跳过
# 输出: PLC_FUSION_AST_* / test/${FUSE_NAME}.fusion_plan.json
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"

if [ "${FUSE_AST_PLAN:-1}" != "1" ]; then
    PLC_FUSION_AST_LOADED=0
    export PLC_FUSION_AST_LOADED
    return 0 2>/dev/null || exit 0
fi

if [ -z "$MANIFEST" ]; then
    plc_die "$PLC_E_USAGE" "缺少 manifest 参数"
fi
plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

FUSE_WORK_DIR="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
PLC_AST_PLAN_PY="$SCRIPT_DIR/plc_fusion_ast_plan__AST方案.py"

if ! command -v python3 >/dev/null; then
    PLC_FUSION_AST_LOADED=0
    export PLC_FUSION_AST_LOADED
    return 0 2>/dev/null || exit 0
fi

eval "$(
    python3 "$PLC_AST_PLAN_PY" \
        --fuse-name "$FUSE_NAME" \
        --work-dir "$FUSE_WORK_DIR" \
        --manifest "$MANIFEST" \
        --plan-out "$FUSE_WORK_DIR/${FUSE_NAME}.fusion_plan.json" \
        --export
)"

apply_ast_manifest_hints() {
    if [ "${PLC_FUSION_AST_LOADED:-0}" != "1" ]; then
        return 0
    fi
    if [ -z "${FUSE_KTHREAD_ENTRY:-}" ] && [ -n "${PLC_FUSION_AST_ENTRY:-}" ]; then
        export FUSE_KTHREAD_ENTRY="$PLC_FUSION_AST_ENTRY"
    fi
    if [ -f "${PLC_FUSION_AST_PLAN_JSON:-}" ] && command -v python3 >/dev/null; then
        read -r hot dce <<< "$(python3 - "${PLC_FUSION_AST_PLAN_JSON}" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
h = (p.get("manifest_hints") or {}).get("FUSE_HOT_PATH_FUNCTIONS", "")
d = (p.get("manifest_hints") or {}).get("FUSE_DCE_ROOTS", "")
print(h, d)
PY
)"
        if [ -z "${FUSE_HOT_PATH_FUNCTIONS:-}" ] && [ -n "$hot" ]; then
            export FUSE_HOT_PATH_FUNCTIONS="$hot"
        fi
        if [ -z "${FUSE_DCE_ROOTS:-}" ] && [ -n "$dce" ]; then
            export FUSE_DCE_ROOTS="$dce"
        fi
    fi
}

apply_ast_manifest_hints

export PLC_FUSION_AST_LOADED PLC_FUSION_AST_JSON PLC_FUSION_AST_PLAN_JSON
export PLC_FUSION_AST_ENTRY PLC_FUSION_AST_FUSION_ELIGIBLE
export PLC_FUSION_AST_FUSION_CRIT PLC_FUSION_AST_FUSION_WARN
export PLC_FUSION_AST_FLOAT_IN_CYCLE PLC_FUSION_AST_FLOAT_ANYWHERE
export PLC_FUSION_AST_SUGGEST_PROFILE PLC_FUSION_AST_PLAN_REASON
export PLC_FUSION_AST_SUGGEST_LOW_JITTER PLC_FUSION_AST_SUGGEST_FLOAT_KILL
