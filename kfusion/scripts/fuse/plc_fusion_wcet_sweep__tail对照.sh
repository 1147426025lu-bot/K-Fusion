#!/bin/bash
# ============================================================================
# plc_fusion_wcet_sweep__tail对照.sh — 6 组 tail/kernel 对照（WCET 前置实验）
# ============================================================================
# 功能: 在同一 pre.ll 上跑 6 种 kernel+tail 组合，比较 .o 体量 / IR 行数 / 入口保留
# 输入: manifest.env（须已有 ${FUSE_NAME}_pre.ll；无则先跑 plc_fuse）
# 输出: test/${FUSE_NAME}.wcet_sweep.json、.wcet_sweep.tsv
# 用法:
#   bash scripts/plc_fusion_wcet_sweep__tail对照.sh manifests/manifest_cyclictest__主线压测.env
# 环境:
#   WCET_SWEEP_VARIANTS=  覆盖默认 6 组（name|kernel_pass|tail_passes，分号分隔）
#   WCET_SWEEP_RUN_FUSE=1 无 pre.ll 时先融合
#   WCET_SWEEP_INCLUDE_PAPER=1  追加 RTSS 2025 论文对齐 preset（默认 1）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
SCRIPTS_ROOT="$(plc_scripts_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-manifests/manifest_cyclictest__主线压测.env}}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
PRE="$WORK/${FUSE_NAME}_pre.ll"
OUT_JSON="$WORK/${FUSE_NAME}.wcet_sweep.json"
OUT_TSV="$WORK/${FUSE_NAME}.wcet_sweep.tsv"
SWEEP_DIR="$WORK/.${FUSE_NAME}_wcet_sweep"
BUILD_DIR="$PROJECT_ROOT/build"
FUSION_SO="$(plc_fusion_pass_so "$PROJECT_ROOT" "$BUILD_DIR")"

if [ ! -f "$PRE" ]; then
    if [ "${WCET_SWEEP_RUN_FUSE:-1}" = "1" ]; then
        echo "ℹ️ 无 pre.ll，先运行融合..."
        bash "$SCRIPT_DIR/plc_fuse__内核化主流程.sh" "$MANIFEST"
    else
        plc_die "$PLC_E_NOFILE" "未找到 pre.ll: $PRE" \
            "先: bash scripts/plc_fuse__内核化主流程.sh $MANIFEST"
    fi
fi

plc_require_file "$FUSION_SO" "Pass 插件" "cd build && cmake .. && make PLCFusionPass"
OPT_BIN="$(plc_resolve_tool OPT_BIN opt-19 opt-18 opt-17 opt)"
LLC_BIN="$(plc_resolve_tool LLC_BIN llc-19 llc-18 llc-17 llc)"

rm -rf "$SWEEP_DIR"
mkdir -p "$SWEEP_DIR"

# name|kernel|tail（tail 空 = 无 tail）
DEFAULT_VARIANTS=(
    "hotpath|plc-kernelize-hotpath|"
    "hotpath+globaldce|plc-kernelize-hotpath|globaldce"
    "hotpath+globalopt|plc-kernelize-hotpath|globalopt"
    "hotpath+full-tail|plc-kernelize-hotpath|globaldce,globalopt"
    "mainline|plc-kernelize-mainline|"
    "mainline+full-tail|plc-kernelize-mainline|globaldce,globalopt"
)

IFS=';' read -ra VARIANTS <<< "${WCET_SWEEP_VARIANTS:-}"
if [ "${#VARIANTS[@]}" -eq 0 ] || [ -z "${VARIANTS[0]:-}" ]; then
    VARIANTS=("${DEFAULT_VARIANTS[@]}")
    if [ "${WCET_SWEEP_INCLUDE_PAPER:-1}" = "1" ]; then
        while IFS= read -r line; do
            [ -n "$line" ] && VARIANTS+=("$line")
        done < <(python3 "$SCRIPTS_ROOT/plc_fusion_wcet_passes__Pass序列库.py" --sweep-specs)
    fi
fi

export PLC_FUSION_DCE="${FUSE_DCE:-1}"
export PLC_FUSION_FIXED_POINT="${PLC_FUSION_FIXED_POINT:-1}"
export PLC_FUSION_BLACKHOLE="${PLC_FUSION_BLACKHOLE:-1}"
export PLC_FUSION_KEEP_GLOBALS="${FUSE_GLOBALIZE_SYMBOLS:-}"
if [ -n "${FUSE_DCE_ROOTS:-}" ]; then
    export PLC_FUSION_ROOTS="$FUSE_DCE_ROOTS"
elif [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
    export PLC_FUSION_ROOTS="$FUSE_KTHREAD_ENTRY"
fi
if [ -n "${FUSE_HOT_PATH_FUNCTIONS:-}" ]; then
    export PLC_FUSION_HOT_PATH_FUNCTIONS="$FUSE_HOT_PATH_FUNCTIONS"
    export PLC_FUSION_WCET_HOT_FUNCTIONS="$FUSE_HOT_PATH_FUNCTIONS"
elif [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
    export PLC_FUSION_HOT_PATH_FUNCTIONS="$FUSE_KTHREAD_ENTRY"
    export PLC_FUSION_WCET_HOT_FUNCTIONS="$FUSE_KTHREAD_ENTRY"
fi

count_entry() {
    grep -cE '^define .* @timerthread\(' "$1" 2>/dev/null || echo 0
}

count_hot_calls() {
    local f="$1"
    local hot="${FUSE_HOT_PATH_FUNCTIONS:-${FUSE_KTHREAD_ENTRY:-timerthread}}"
    local n=0 sym
    IFS=',' read -ra syms <<< "$hot"
    for sym in "${syms[@]}"; do
        sym="$(echo "$sym" | tr -d ' ')"
        [ -z "$sym" ] && continue
        c=$(grep -cE "define .* @${sym}\(" "$f" 2>/dev/null || echo 0)
        n=$((n + c))
    done
    echo "$n"
}

echo "=== WCET tail 对照: ${FUSE_NAME} ==="
echo "    pre=$PRE"
echo "    variants=${#VARIANTS[@]}"

{
    echo -e "name\tkernel\ttail\tir_lines\tobj_bytes\thot_entries\ttimerthread_ok"
} > "$OUT_TSV"

json_rows=()
best_bytes=999999999
best_name=""
wcet_bytes=999999999
wcet_name=""

for spec in "${VARIANTS[@]}"; do
    name="${spec%%|*}"
    rest="${spec#*|}"
    kernel="${rest%%|*}"
    tail="${rest#*|}"

    opt_passes="$kernel"
    [ -n "$tail" ] && opt_passes="${opt_passes},${tail}"

    kll="$SWEEP_DIR/${name}.ll"
    obj="$SWEEP_DIR/${name}.o"

    echo "    ▶ $name → $opt_passes"
    if ! "$OPT_BIN" -load-pass-plugin="$FUSION_SO" \
        -passes="$opt_passes" "$PRE" -S -o "$kll" 2>"$SWEEP_DIR/${name}.opt.err"; then
        plc_warn "variant $name opt 失败" "见 $SWEEP_DIR/${name}.opt.err"
        continue
    fi
    if ! "$LLC_BIN" -O3 -relocation-model=pic \
        -march="${FUSE_LLC_ARCH:-aarch64}" -mattr="${FUSE_LLC_ATTR:--fp-armv8,-neon}" \
        -filetype=obj "$kll" -o "$obj" 2>"$SWEEP_DIR/${name}.llc.err"; then
        plc_warn "variant $name llc 失败" "见 $SWEEP_DIR/${name}.llc.err"
        continue
    fi

    ir_lines="$(wc -l < "$kll" | tr -d ' ')"
    obj_bytes="$(stat -c%s "$obj" 2>/dev/null || echo 0)"
    hot_ent="$(count_hot_calls "$kll")"
    tt_ok=0
    [ "$(count_entry "$kll")" -ge 1 ] && tt_ok=1

    echo -e "${name}\t${kernel}\t${tail}\t${ir_lines}\t${obj_bytes}\t${hot_ent}\t${tt_ok}" >> "$OUT_TSV"

    json_rows+=("${name}|${kernel}|${tail}|${ir_lines}|${obj_bytes}|${hot_ent}|${tt_ok}")

    if [ "$obj_bytes" -lt "$best_bytes" ]; then
        best_bytes="$obj_bytes"
        best_name="$name"
    fi
    if [ -z "$tail" ] && [ "$tt_ok" -eq 1 ] && [ "$obj_bytes" -lt "$wcet_bytes" ]; then
        wcet_bytes="$obj_bytes"
        wcet_name="$name"
    fi
done

[ -z "$wcet_name" ] && wcet_name="$best_name" && wcet_bytes="$best_bytes"

python3 - "$OUT_JSON" "$FUSE_NAME" "$MANIFEST" "$PRE" "$best_name" "$best_bytes" "$wcet_name" "$wcet_bytes" "${json_rows[@]}" << 'PY'
import json, sys
from datetime import datetime, timezone

out_path, fuse_name, manifest, pre, best_name, best_bytes, wcet_name, wcet_bytes = sys.argv[1:9]
rows = sys.argv[9:]
variants = []
for row in rows:
    name, kernel, tail, ir_lines, obj_bytes, hot_ent, tt_ok = row.split("|")
    variants.append({
        "name": name,
        "kernel": kernel,
        "tail": tail,
        "ir_lines": int(ir_lines),
        "obj_bytes": int(obj_bytes),
        "hot_entries": int(hot_ent),
        "timerthread_ok": int(tt_ok),
    })
pre_lines = sum(1 for _ in open(pre, encoding="utf-8", errors="replace"))
doc = {
    "fuse_name": fuse_name,
    "manifest": manifest,
    "generated": datetime.now(timezone.utc).astimezone().isoformat(),
    "pre_ll_lines": pre_lines,
    "recommended_size": best_name,
    "recommended_size_obj_bytes": int(best_bytes),
    "recommended_wcet": wcet_name,
    "recommended_wcet_obj_bytes": int(wcet_bytes),
    "variants": variants,
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

echo ""
echo "=== 对照表 ==="
column -t -s $'\t' "$OUT_TSV" 2>/dev/null || cat "$OUT_TSV"
echo ""
echo "    最小 .o: ${best_name} (${best_bytes} bytes)"
echo "    WCET 推荐（无 tail）: ${wcet_name} (${wcet_bytes} bytes)"
echo "    json=$OUT_JSON"
echo "    tsv=$OUT_TSV"
