#!/bin/bash
# ============================================================================
# plc_fusion_wcet_tail_pick__短测选tail.sh — wcet-benchmark 静态 3 组 tail 快选
# ============================================================================
# 功能: 在 pre.ll 上试 3 种 tail，按 .o 体积 + 热路径符号保留选优，写 .wcet_tail_pick.env
# 用法: bash scripts/fuse/plc_fusion_wcet_tail_pick__短测选tail.sh manifest.env pre.ll
# 环境: FUSE_WCET_TAIL_PROBE=1  由 plc_fuse [4c-] 触发
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-}"
PRE="${2:-}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""
plc_require_file "$PRE" "pre.ll"

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
OUT_ENV="$WORK/${FUSE_NAME}.wcet_tail_pick.env"
PICK_DIR="$WORK/.${FUSE_NAME}_tail_pick"
BUILD_DIR="$PROJECT_ROOT/build"
FUSION_SO="$BUILD_DIR/PLCFusionPass.so"
OPT_BIN="$(plc_resolve_tool OPT_BIN opt-19 opt-18 opt-17 opt)"
LLC_BIN="$(plc_resolve_tool LLC_BIN llc-19 llc-18 llc-17 llc)"
plc_require_file "$FUSION_SO" "PLCFusionPass.so"

HOT="${FUSE_HOT_PATH_FUNCTIONS:-${FUSE_KTHREAD_ENTRY:-}}"
HOT="${HOT%%,*}"

export PLC_FUSION_DCE="${PLC_FUSION_DCE:-1}"
export PLC_FUSION_FIXED_POINT="${PLC_FUSION_FIXED_POINT:-1}"
export PLC_FUSION_BLACKHOLE="${PLC_FUSION_BLACKHOLE:-1}"
export PLC_FUSION_WCET_HOT_FUNCTIONS="${FUSE_HOT_PATH_FUNCTIONS:-$FUSE_KTHREAD_ENTRY}"

rm -rf "$PICK_DIR"
mkdir -p "$PICK_DIR"

# name|kernel|cold_env|module_csv
VARIANTS=(
    "paper-o1|plc-kernelize-wcet|simplifycfg|sroa|early-cse|instcombine|globaldce"
    "paper-o2|plc-kernelize-wcet|simplifycfg|sroa|instcombine|loop-mssa(loop-rotate,licm)|loop-unroll|gvn|adce|instcombine|globaldce"
    "hot-none|plc-kernelize-hotpath||"
)

best_name=""
best_bytes=999999999
best_cold=""
best_mod="globaldce"

for spec in "${VARIANTS[@]}"; do
    IFS='|' read -r vname kernel cold_env mod_csv <<< "$spec"
    mod_csv="${mod_csv:-globaldce}"
    tail=""
    if [ -n "$cold_env" ]; then
        tail=$(python3 "$PROJECT_ROOT/scripts/plc_fusion_wcet_passes__Pass序列库.py" \
            --format-tail "$cold_env" "$mod_csv")
    fi
    opt_passes="$kernel"
    [ -n "$tail" ] && opt_passes="${opt_passes},${tail}"
    kll="$PICK_DIR/${vname}.ll"
    ko="$PICK_DIR/${vname}.o"
    if ! "$OPT_BIN" -load-pass-plugin="$FUSION_SO" -passes="$opt_passes" "$PRE" -S -o "$kll" 2>"$PICK_DIR/${vname}.err"; then
        continue
    fi
    if [ -n "$HOT" ] && ! grep -q "define.*@$HOT" "$kll" 2>/dev/null; then
        continue
    fi
    "$LLC_BIN" -filetype=obj "$kll" -o "$ko" 2>/dev/null || continue
    bytes=$(stat -c%s "$ko" 2>/dev/null || echo 999999999)
    if [ "$bytes" -lt "$best_bytes" ]; then
        best_bytes=$bytes
        best_name=$vname
        best_cold=$cold_env
        best_mod=$mod_csv
    fi
done

if [ -z "$best_name" ]; then
    plc_warn "WCET tail 短测：无可用 variant，保留 pipeline 默认"
    exit 1
fi

{
    echo "# wcet tail pick $(date -Iseconds) winner=$best_name bytes=$best_bytes"
    echo "FUSE_COLD_PASS_SEQUENCE=${best_cold}"
    echo "FUSE_MODULE_PASS_SEQUENCE=${best_mod}"
    echo "PLC_FUSION_TAIL_PICK=$best_name"
} > "$OUT_ENV"

echo "    tail_pick winner=$best_name bytes=$best_bytes"
exit 0
