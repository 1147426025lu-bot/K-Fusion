#!/bin/bash
# ============================================================================
# plc_fusion_wcet_genetic__遗传WCET调优.sh — RTSS 2025 风格遗传 WCET 搜索
# ============================================================================
# 功能:
#   在 pre.ll 上对 (kernel, tail, cold_tail) 做种群进化，以 WCET 为适应度
#   参考 Magnani et al. RTSS 2025 LLVM WCET autotuning（函数级热/冷分离）
# 用法:
#   WCET_GENETIC_SKIP_INSMOD=1 bash scripts/plc_fusion_wcet_genetic__遗传WCET调优.sh \
#     manifests/manifest_cyclictest__主线压测.env
# 环境:
#   WCET_GENETIC_POP=8        种群大小（默认 8）
#   WCET_GENETIC_GEN=4        代数（默认 4）
#   WCET_GENETIC_ELITE=2      精英保留（默认 2）
#   WCET_GENETIC_SKIP_INSMOD=1  无 sudo 时用 obj_bytes 代理
#   WCET_PROBE_SEC=30
#   WCET_GENETIC_APPLY=1      将 winner 复制为 _kernel.o_shipped
#   WCET_GENETIC_SEED=42
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
SCRIPTS_ROOT="$(plc_scripts_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-manifests/manifest_cyclictest__主线压测.env}}"
PROBE="$SCRIPT_DIR/plc_fusion_wcet_probe__短测探针.sh"
SEARCH_PY="$SCRIPTS_ROOT/plc_fusion_wcet_search__WCET搜索核心.py"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
PRE="$WORK/${FUSE_NAME}_pre.ll"
OUT_JSON="$WORK/${FUSE_NAME}.wcet_genetic.json"
OUT_ENV="$WORK/${FUSE_NAME}.genetic.env"
GEN_DIR="$WORK/.${FUSE_NAME}_wcet_genetic"
CFG="$GEN_DIR/search_config.json"
BUILD_DIR="$PROJECT_ROOT/build"
FUSION_SO="$BUILD_DIR/PLCFusionPass.so"

POP="${WCET_GENETIC_POP:-8}"
GEN="${WCET_GENETIC_GEN:-4}"
ELITE="${WCET_GENETIC_ELITE:-2}"
SKIP="${WCET_GENETIC_SKIP_INSMOD:-1}"
PROBE_SEC="${WCET_PROBE_SEC:-30}"
APPLY="${WCET_GENETIC_APPLY:-1}"
SEED="${WCET_GENETIC_SEED:-42}"

if [ ! -f "$PRE" ]; then
    echo "ℹ️ 无 pre.ll，先运行融合..."
    bash "$SCRIPT_DIR/plc_fuse__内核化主流程.sh" "$MANIFEST"
fi
plc_require_file "$FUSION_SO" "Pass 插件" "cd build && cmake .. && make PLCFusionPass"
plc_require_file "$SEARCH_PY" "genetic search"

OPT_BIN="$(plc_resolve_tool OPT_BIN opt-19 opt-18 opt-17 opt)"
LLC_BIN="$(plc_resolve_tool LLC_BIN llc-19 llc-18 llc-17 llc)"

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

mkdir -p "$GEN_DIR"

export PRE_LL="$PRE" GEN_DIR="$GEN_DIR" OUT_JSON="$OUT_JSON" OUT_ENV="$OUT_ENV"
export MANIFEST="$MANIFEST" FUSE_NAME="$FUSE_NAME" OPT_BIN="$OPT_BIN" LLC_BIN="$LLC_BIN"
export FUSION_SO="$FUSION_SO" PROBE="$PROBE" WORK="$WORK"
export LLC_ARCH="${FUSE_LLC_ARCH:-aarch64}" LLC_ATTR="${FUSE_LLC_ATTR:--fp-armv8,-neon}"
export POP="$POP" GEN="$GEN" ELITE="$ELITE" SEED="$SEED" PROBE_SEC="$PROBE_SEC"

echo "=== WCET genetic search: ${FUSE_NAME} ==="
echo "    pop=$POP gen=$GEN elite=$ELITE seed=$SEED"
echo "    insmod=$([ "$SKIP" = 1 ] && echo skip || echo on) probe_sec=$PROBE_SEC"

python3 - "$CFG" "$SKIP" "$APPLY" << 'PY'
import json, os, sys
cfg_path, skip, apply = sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1"
cfg = {
    "pre_ll": os.environ["PRE_LL"],
    "out_dir": os.environ["GEN_DIR"],
    "out_json": os.environ["OUT_JSON"],
    "out_env": os.environ["OUT_ENV"],
    "manifest": os.environ["MANIFEST"],
    "fuse_name": os.environ["FUSE_NAME"],
    "opt_bin": os.environ["OPT_BIN"],
    "llc_bin": os.environ["LLC_BIN"],
    "fusion_so": os.environ["FUSION_SO"],
    "llc_arch": os.environ.get("LLC_ARCH", "aarch64"),
    "llc_attr": os.environ.get("LLC_ATTR", "-fp-armv8,-neon"),
    "pass_env": {
        "PLC_FUSION_DCE": os.environ.get("PLC_FUSION_DCE", "1"),
        "PLC_FUSION_FIXED_POINT": os.environ.get("PLC_FUSION_FIXED_POINT", "1"),
        "PLC_FUSION_BLACKHOLE": os.environ.get("PLC_FUSION_BLACKHOLE", "1"),
        "PLC_FUSION_KEEP_GLOBALS": os.environ.get("PLC_FUSION_KEEP_GLOBALS", ""),
        "PLC_FUSION_ROOTS": os.environ.get("PLC_FUSION_ROOTS", ""),
        "PLC_FUSION_HOT_PATH_FUNCTIONS": os.environ.get("PLC_FUSION_HOT_PATH_FUNCTIONS", ""),
        "PLC_FUSION_WCET_HOT_FUNCTIONS": os.environ.get("PLC_FUSION_WCET_HOT_FUNCTIONS", ""),
    },
    "probe_sh": os.environ["PROBE"],
    "skip_insmod": skip,
    "probe_sec": int(os.environ.get("PROBE_SEC", "30")),
    "population": int(os.environ.get("POP", "8")),
    "generations": int(os.environ.get("GEN", "4")),
    "elite": int(os.environ.get("ELITE", "2")),
    "seed": int(os.environ.get("SEED", "42")),
    "apply": apply,
    "work_dir": os.environ["WORK"],
}
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PY

python3 "$SEARCH_PY" "$CFG"
echo "✅ WCET genetic 完成 → $OUT_JSON"
