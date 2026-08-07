#!/bin/bash
# ============================================================================
# run_ci_wcet_probe__板级探针门禁.sh — WCET 板级探针（可选 insmod）
# ============================================================================
# 环境:
#   CI_WCET_PROBE=1              启用 insmod 短测
#   WCET_PROBE_MAX_NS=           覆盖 golden 阈值
#   WCET_PROBE_UPDATE_GOLDEN=1   用本次 abs_max 更新 golden（需显式开启）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
# shellcheck source=platform/plc_source_platform__加载平台.sh
source "$SCRIPT_DIR/platform/plc_source_platform__加载平台.sh"

PROBE="$SCRIPT_DIR/fuse/plc_fusion_wcet_probe__短测探针.sh"
GOLDEN_PY="$SCRIPT_DIR/plc_fusion_wcet_probe_golden__探针阈值.py"
CYCLIC="$PRJ/manifests/manifest_cyclictest__主线压测.env"
WORK="$PRJ/test"
REPORT="$WORK/ci_wcet_probe.json"
PLATFORM="${PLATFORM_ID:-generic}"

PROBE_SEC="${WCET_PROBE_SEC:-$(python3 "$GOLDEN_PY" --platform "$PLATFORM" --field probe_sec 2>/dev/null || echo 8)}"
MAX_NS="${WCET_PROBE_MAX_NS:-$(python3 "$GOLDEN_PY" --platform "$PLATFORM" --field max_abs_max_ns 2>/dev/null || echo 5000000)}"
MIN_CYCLES="${WCET_PROBE_MIN_CYCLES:-$(python3 "$GOLDEN_PY" --platform "$PLATFORM" --field min_cycles 2>/dev/null || echo 0)}"

echo "=== CI WCET 板级探针门禁 ==="
echo "    platform=$PLATFORM probe_sec=$PROBE_SEC max_ns=$MAX_NS (golden)"

plc_require_file "$PROBE" "wcet probe script"
plc_require_file "$GOLDEN_PY" "golden loader"

if [ "${CI_WCET_PROBE:-0}" != "1" ]; then
    echo "    CI_WCET_PROBE=0 → 跳过 insmod（probe 脚本就绪）"
    printf '%s\n' "{\"mode\":\"dry\",\"ci_wcet_probe\":0,\"platform\":\"$PLATFORM\"}" > "$REPORT"
    echo "✅ WCET 探针门禁（dry）通过"
    exit 0
fi

if ! sudo -n true 2>/dev/null; then
    echo "    ⏭️  CI_WCET_PROBE=1 但无免密 sudo，跳过 insmod"
    printf '%s\n' "{\"mode\":\"skip_sudo\",\"ci_wcet_probe\":1,\"platform\":\"$PLATFORM\"}" > "$REPORT"
    exit 0
fi

plc_require_file "$CYCLIC" "cyclictest manifest"
# shellcheck disable=SC1090
source "$CYCLIC"
OBJ="$WORK/${FUSE_NAME}_kernel.o"
if [ ! -f "$OBJ" ]; then
    echo "    fuse cyclictest 主线（FUSE_STRICT=${FUSE_STRICT:-0}）..."
    bash "$SCRIPT_DIR/fuse/plc_fuse__内核化主流程.sh" "$CYCLIC"
    source "$CYCLIC"
    OBJ="$WORK/${FUSE_NAME}_kernel.o"
fi
plc_require_file "$OBJ" "cyclictest kernel.o"

echo "    insmod probe..."
PROBE_OUT="$WORK/.ci_wcet_probe.out"
if ! WCET_PROBE_SEC="$PROBE_SEC" WCET_PROBE_TAG="ci_probe_${PLATFORM}" \
    bash "$PROBE" "$CYCLIC" "$OBJ" "$PROBE_SEC" >"$PROBE_OUT" 2>&1; then
    cat "$PROBE_OUT" >&2
    plc_die "$PLC_E_KMOD" "WCET 板级探针失败"
fi
cat "$PROBE_OUT" | tail -3

ABS_MAX=$(sed -n 's/.*abs_max_ns=\([0-9-]*\).*/\1/p' "$PROBE_OUT" | tail -1)
CYCLES=$(sed -n 's/.*cycles=\([0-9]*\).*/\1/p' "$PROBE_OUT" | tail -1)

if [ "${WCET_PROBE_UPDATE_GOLDEN:-0}" = "1" ] && [ -n "$ABS_MAX" ]; then
    python3 "$GOLDEN_PY" --platform "$PLATFORM" --update --abs-max-ns "$ABS_MAX"
    echo "    golden updated (WCET_PROBE_UPDATE_GOLDEN=1)"
fi

python3 - "$REPORT" "$ABS_MAX" "$CYCLES" "$MAX_NS" "$MIN_CYCLES" "$PROBE_SEC" "$PLATFORM" <<'PY'
import json, sys
report, abs_max, cycles, max_ns, min_cycles, sec, platform = sys.argv[1:8]
if not abs_max or not abs_max.lstrip("-").isdigit():
    raise SystemExit("missing abs_max_ns")
val = int(abs_max)
cyc = int(cycles) if cycles and cycles.isdigit() else 0
ceil = int(max_ns)
floor = int(min_cycles)
if val > ceil:
    raise SystemExit(f"abs_max_ns {val} > golden threshold {ceil}")
if floor > 0 and cyc < floor:
    raise SystemExit(f"cycles {cyc} < min_cycles {floor} (probe too short?)")
doc = {
    "mode": "insmod",
    "platform": platform,
    "abs_max_ns": val,
    "cycles": cyc,
    "max_ns_threshold": ceil,
    "min_cycles": floor,
    "probe_sec": int(sec),
}
open(report, "w", encoding="utf-8").write(json.dumps(doc, indent=2) + "\n")
print(f"    abs_max_ns={val} cycles={cyc} (threshold={ceil})")
PY

echo "✅ WCET 板级探针门禁通过"
