#!/bin/bash
# ============================================================================
# plc_fusion_pipeline_policy__Pass策略解析.sh — manifest Pass 策略（ast-auto / wcet-benchmark / fixed）
# ============================================================================
# 策略:
#   ast-auto        — AST + pipeline auto 一次定案；不跑 WCET 搜索（plc-cc / demo / CI 默认）
#   wcet-benchmark  — 同上完成首次融合；允许后续 WCET sweep/autotune（cyclictest 等标杆）
#   fixed           — 仅用 manifest 中 FUSE_PIPELINE；AST 不改 profile（FUSE_AST_PLAN=0）
#
# 用法: source scripts/fuse/plc_fusion_pipeline_policy__Pass策略解析.sh manifests/foo.env
# 输出: PLC_FUSION_PIPELINE_POLICY / FUSE_AST_PLAN / FUSE_WCET_SEARCH / .pipeline_policy.log
# 环境:
#   FUSE_PIPELINE_POLICY=auto|ast-auto|wcet-benchmark|fixed
#   FUSE_WCET_AUTOTUNE=1  仅 wcet-benchmark：plc_fuse 结束后跑 autotune（默认 0，耗时长）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"

if [ -z "$MANIFEST" ]; then
    plc_die "$PLC_E_USAGE" "缺少 manifest 参数" \
        "用法: source scripts/fuse/plc_fusion_pipeline_policy__Pass策略解析.sh manifests/foo.env"
fi
plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

infer_pipeline_policy() {
    local name="${FUSE_NAME:-}"
    case "$name" in
        official_cycletest|official_cycletest_multitu|signaltest|ptsematest)
            echo wcet-benchmark
            return
            ;;
        plc_cc_*|github_*)
            echo ast-auto
            return
            ;;
    esac
    if [[ "${FUSE_DESC:-}" == *cyclictest* ]] || [[ "${FUSE_SOURCE:-}" == *cyclictest* ]]; then
        echo wcet-benchmark
        return
    fi
    echo ast-auto
}

resolve_pipeline_policy() {
    local req="${FUSE_PIPELINE_POLICY:-auto}"
    local inferred reason

    if [ "$req" = auto ] || [ -z "$req" ]; then
        if [ "${FUSE_PIPELINE:-auto}" != auto ] && [ -n "${FUSE_PIPELINE:-}" ]; then
            PLC_FUSION_PIPELINE_POLICY=fixed
            reason="FUSE_PIPELINE=${FUSE_PIPELINE} (non-auto)"
        else
            inferred="$(infer_pipeline_policy)"
            PLC_FUSION_PIPELINE_POLICY="$inferred"
            reason="inferred:${inferred}"
        fi
    else
        PLC_FUSION_PIPELINE_POLICY="$req"
        reason="manifest:FUSE_PIPELINE_POLICY=$req"
    fi

    case "$PLC_FUSION_PIPELINE_POLICY" in
        ast-auto)
            FUSE_AST_PLAN="${FUSE_AST_PLAN:-1}"
            FUSE_AST_APPLY_SUGGEST="${FUSE_AST_APPLY_SUGGEST:-1}"
            FUSE_WCET_SEARCH="${FUSE_WCET_SEARCH:-0}"
            FUSE_WCET_AUTOTUNE="${FUSE_WCET_AUTOTUNE:-0}"
            FUSE_WCET_TAIL_PROBE="${FUSE_WCET_TAIL_PROBE:-0}"
            ;;
        wcet-benchmark)
            FUSE_AST_PLAN="${FUSE_AST_PLAN:-1}"
            FUSE_AST_APPLY_SUGGEST="${FUSE_AST_APPLY_SUGGEST:-0}"
            FUSE_WCET_SEARCH="${FUSE_WCET_SEARCH:-1}"
            FUSE_WCET_AUTOTUNE="${FUSE_WCET_AUTOTUNE:-0}"
            FUSE_WCET_TAIL_PROBE="${FUSE_WCET_TAIL_PROBE:-0}"
            ;;
        fixed)
            FUSE_AST_PLAN=0
            FUSE_AST_APPLY_SUGGEST=0
            FUSE_WCET_SEARCH=0
            FUSE_WCET_AUTOTUNE=0
            FUSE_WCET_TAIL_PROBE=0
            ;;
        *)
            plc_die "$PLC_E_MANIFEST" "未知 FUSE_PIPELINE_POLICY=$PLC_FUSION_PIPELINE_POLICY" \
                "可选: ast-auto | wcet-benchmark | fixed | auto"
            ;;
    esac

    PLC_FUSION_POLICY_REASON="$reason"
    export PLC_FUSION_PIPELINE_POLICY PLC_FUSION_POLICY_REASON
    export FUSE_AST_PLAN FUSE_WCET_SEARCH FUSE_WCET_AUTOTUNE FUSE_AST_APPLY_SUGGEST FUSE_WCET_TAIL_PROBE
}

resolve_pipeline_policy

LOG="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}/${FUSE_NAME}.pipeline_policy.log"
{
    echo "# pipeline policy $(date -Iseconds)"
    echo "policy=$PLC_FUSION_PIPELINE_POLICY"
    echo "reason=$PLC_FUSION_POLICY_REASON"
    echo "FUSE_PIPELINE=${FUSE_PIPELINE:-auto}"
    echo "FUSE_AST_PLAN=$FUSE_AST_PLAN"
    echo "FUSE_WCET_SEARCH=$FUSE_WCET_SEARCH"
    echo "FUSE_WCET_AUTOTUNE=$FUSE_WCET_AUTOTUNE"
    echo "FUSE_AST_APPLY_SUGGEST=$FUSE_AST_APPLY_SUGGEST"
    echo "FUSE_WCET_TAIL_PROBE=$FUSE_WCET_TAIL_PROBE"
    echo "wcet_search_hint=$(
        if [ "$FUSE_WCET_SEARCH" = "1" ]; then
            echo "bash scripts/fuse/plc_fusion_wcet_autotune__WCET自动调优.sh $MANIFEST"
        else
            echo "n/a"
        fi
    )"
} > "$LOG"

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    echo "policy=$PLC_FUSION_PIPELINE_POLICY"
    echo "reason=$PLC_FUSION_POLICY_REASON"
    echo "FUSE_AST_PLAN=$FUSE_AST_PLAN FUSE_WCET_SEARCH=$FUSE_WCET_SEARCH"
    echo "log=$LOG"
fi
