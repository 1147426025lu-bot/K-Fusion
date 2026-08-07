#!/bin/bash
# ============================================================================
# plc_fusion_wcet_per_function__函数级WCET.sh — Lavinium 风格函数级 WCET 调度
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST"

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
PRE_LL="$WORK/${FUSE_NAME}_pre.ll"
SCHEDULE="$WORK/${FUSE_NAME}.wcet_schedule.json"
REPORT="$WORK/${FUSE_NAME}.wcet_per_function.json"
PARTITION_PY="$PROJECT_ROOT/scripts/plc_fusion_wcet_partition__函数级分区.py"
SEARCH_PY="$PROJECT_ROOT/scripts/plc_fusion_wcet_per_function_search__函数级Association.py"
GREEDY_PY="$PROJECT_ROOT/scripts/plc_fusion_wcet_greedy__Greedy搜索.py"
BUILD_DIR="$PROJECT_ROOT/build"

plc_require_file "$PRE_LL" "预清理 IR" \
    "先运行: bash scripts/fuse/plc_fuse__内核化主流程.sh $MANIFEST"

HOT="${FUSE_HOT_PATH_FUNCTIONS:-${FUSE_KTHREAD_ENTRY:-}}"
ROOTS="${FUSE_GLOBALIZE_SYMBOLS:-${FUSE_KTHREAD_ENTRY:-}}"
DEFAULT_COLD="${FUSE_COLD_PASS_SEQUENCE:-simplifycfg|sroa|instcombine|loop-mssa(loop-rotate,licm)|gvn|adce}"
DEFAULT_MOD="${FUSE_MODULE_PASS_SEQUENCE:-globaldce}"

echo "=== K-Fusion 函数级 WCET 调度 (Lavinium-style) ==="
echo "    manifest=$MANIFEST"
echo "    pre_ll=$PRE_LL"

python3 "$PARTITION_PY" "$PRE_LL" -o "$SCHEDULE" \
    --hot "$HOT" \
    --roots "$ROOTS" \
    --default-cold "$DEFAULT_COLD" \
    --module "$DEFAULT_MOD"

COLD_COUNT=$(python3 -c "import json; d=json.load(open('$SCHEDULE')); print(len(d.get('cold_sequences') or {}))")

if [ "${FUSE_WCET_ASSOC_SKIP:-0}" = "1" ] || [ "${FUSE_WCET_ASSOC_BUDGET:-0}" = "0" ] || [ "$COLD_COUNT" = "0" ]; then
    echo "    skip association (skip=${FUSE_WCET_ASSOC_SKIP:-0} budget=${FUSE_WCET_ASSOC_BUDGET:-0} cold=$COLD_COUNT)"
else
    BUDGET="${FUSE_WCET_ASSOC_BUDGET:-24}"
    MAX_FUNCS="${FUSE_WCET_ASSOC_MAX_FUNCS:-5}"
    BUDGET_PER_FN="${FUSE_WCET_ASSOC_BUDGET_PER_FN:-$(( BUDGET / (MAX_FUNCS > 0 ? MAX_FUNCS : 1) ))}"
    if [ "$BUDGET_PER_FN" -lt 6 ]; then
        BUDGET_PER_FN=6
    fi
    echo "    per-function association: cold=$COLD_COUNT max_funcs=$MAX_FUNCS budget_per_fn=$BUDGET_PER_FN"

    OPT_BIN="$(plc_resolve_tool OPT_BIN opt-19 opt-18 opt-17 opt)"
    LLC_BIN="$(plc_resolve_tool LLC_BIN llc-19 llc-18 llc-17 llc)"
    FUSION_SO="$(plc_fusion_pass_so "$PROJECT_ROOT" "$BUILD_DIR")"
    plc_require_file "$FUSION_SO" "Pass 插件" "cd build && make KFusionPass"

    LLC_ARCH="${FUSE_LLC_ARCH:-aarch64}"
    LLC_ATTR="${FUSE_LLC_ATTR:--fp-armv8,-neon}"
    SEARCH_WORK="$WORK/.${FUSE_NAME}_wcet_per_fn"
    CFG="$SEARCH_WORK/search_config.json"
    mkdir -p "$SEARCH_WORK"

    export PLC_FUSION_WCET_HOT_FUNCTIONS="$HOT"
    python3 - "$PRE_LL" "$SCHEDULE" "$CFG" "$REPORT" "$OPT_BIN" "$LLC_BIN" "$FUSION_SO" \
        "$LLC_ARCH" "$LLC_ATTR" "$SEARCH_WORK" "$BUDGET_PER_FN" "$MAX_FUNCS" \
        "${FUSE_WCET_ASSOC_SEED:-42}" "$HOT" <<'PY'
import json, sys
(pre_ll, schedule_path, cfg_path, report_path,
 opt_bin, llc_bin, fusion_so, llc_arch, llc_attr, work_dir,
 budget_per_fn, max_funcs, seed, hot) = sys.argv[1:15]
cfg = {
    "pre_ll": pre_ll,
    "schedule": json.load(open(schedule_path, encoding="utf-8")),
    "out_schedule": schedule_path,
    "out_report": report_path,
    "opt_bin": opt_bin,
    "llc_bin": llc_bin,
    "fusion_so": fusion_so,
    "llc_arch": llc_arch,
    "llc_attr": llc_attr,
    "pass_env": {
        "PLC_FUSION_WCET_HOT_FUNCTIONS": hot,
        "PLC_FUSION_HOT_PATH_FUNCTIONS": hot,
    },
    "hot_names": hot,
    "budget_per_fn": int(budget_per_fn),
    "max_funcs": int(max_funcs),
    "seed": int(seed),
    "work_dir": work_dir,
    "greedy_rounds": int(__import__("os").environ.get("FUSE_WCET_GREEDY_ROUNDS", "2")),
}
open(cfg_path, "w", encoding="utf-8").write(json.dumps(cfg, indent=2) + "\n")
PY

    WCET_POLICY="${FUSE_WCET_POLICY:-association}"
    echo "    search policy=$WCET_POLICY"
    case "$WCET_POLICY" in
        greedy)
            python3 "$GREEDY_PY" "$CFG"
            ;;
        association|*)
            python3 "$SEARCH_PY" "$CFG"
            ;;
    esac
fi

export PLC_FUSION_WCET_SCHEDULE_FILE="$SCHEDULE"
export FUSE_WCET_PER_FUNCTION=1

ENV_SNIP="$WORK/${FUSE_NAME}.wcet_per_function.env"
SUGGEST="$WORK/${FUSE_NAME}.wcet_suggest.env"
{
    echo "# K-Fusion per-function WCET — $(date -Iseconds)"
    echo "PLC_FUSION_WCET_SCHEDULE_FILE=$SCHEDULE"
    echo "FUSE_WCET_PER_FUNCTION=1"
    echo "FUSE_WCET_MODE=1"
    echo "FUSE_PIPELINE=wcet"
} >"$ENV_SNIP"

python3 - "$SCHEDULE" "$REPORT" "$SUGGEST" "$DEFAULT_MOD" <<'PY'
import json, sys
from pathlib import Path

schedule_path, report_path, suggest_path, default_mod = sys.argv[1:5]
schedule = json.load(open(schedule_path, encoding="utf-8"))
mod = schedule.get("module_passes") or [p.strip() for p in default_mod.split(",") if p.strip()]
# 取最长 cold 序列作为全局 fallback（per-fn 仍以 schedule JSON 为准）
cold_seqs = list((schedule.get("cold_sequences") or {}).values())
fallback = max(cold_seqs, key=len) if cold_seqs else []
cold_env = "|".join(fallback)
lines = [
    f"# K-Fusion WCET suggest — generated from {Path(schedule_path).name}",
    "FUSE_WCET_MODE=1",
    "FUSE_WCET_PER_FUNCTION=1",
    f"PLC_FUSION_WCET_SCHEDULE_FILE={schedule_path}",
    "FUSE_PIPELINE=wcet",
    f"FUSE_COLD_PASS_SEQUENCE={cold_env}",
    f"FUSE_MODULE_PASS_SEQUENCE={','.join(mod)}",
]
if Path(report_path).is_file():
    rep = json.load(open(report_path, encoding="utf-8"))
    lines.insert(1, f"# algorithm={rep.get('algorithm', '?')} metric={rep.get('metric', '?')}")
open(suggest_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY

echo "✅ schedule=$SCHEDULE"
[ -f "$REPORT" ] && echo "✅ report=$REPORT"
echo "✅ env_snippet=$ENV_SNIP"
echo "✅ suggest=$SUGGEST"
echo "    写回 manifest: bash scripts/fuse/plc_fuse_apply_wcet_per_function__应用函数级WCET.sh $MANIFEST"
