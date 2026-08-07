#!/bin/bash
# ============================================================================
# run_ci_wcet_probe__板级探针门禁.sh — WCET 板级探针（可选 insmod）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
PROBE="$SCRIPT_DIR/fuse/plc_fusion_wcet_probe__短测探针.sh"
CYCLIC="$PRJ/manifests/manifest_cyclictest__主线压测.env"
WORK="$PRJ/test"
REPORT="$WORK/ci_wcet_probe.json"
MAX_NS="${WCET_PROBE_MAX_NS:-5000000}"
PROBE_SEC="${WCET_PROBE_SEC:-8}"

echo "=== CI WCET 板级探针门禁 ==="
plc_require_file "$PROBE" "wcet probe script"

if [ "${CI_WCET_PROBE:-0}" != "1" ]; then
    echo "    CI_WCET_PROBE=0 → 跳过 insmod（probe 脚本就绪）"
    printf '%s\n' '{"mode":"dry","ci_wcet_probe":0}' > "$REPORT"
    echo "✅ WCET 探针门禁（dry）通过"
    exit 0
fi

if ! sudo -n true 2>/dev/null; then
    echo "    ⏭️  CI_WCET_PROBE=1 但无免密 sudo，跳过 insmod"
    printf '%s\n' '{"mode":"skip_sudo","ci_wcet_probe":1}' > "$REPORT"
    exit 0
fi

plc_require_file "$CYCLIC" "cyclictest manifest"
# shellcheck disable=SC1090
source "$CYCLIC"
OBJ="$WORK/${FUSE_NAME}_kernel.o"
if [ ! -f "$OBJ" ]; then
    echo "    fuse cyclictest 主线..."
    bash "$SCRIPT_DIR/fuse/plc_fuse__内核化主流程.sh" "$CYCLIC"
    source "$CYCLIC"
    OBJ="$WORK/${FUSE_NAME}_kernel.o"
fi
plc_require_file "$OBJ" "cyclictest kernel.o"

echo "    insmod probe sec=$PROBE_SEC max_ns=$MAX_NS"
PROBE_OUT="$WORK/.ci_wcet_probe.out"
if ! WCET_PROBE_SEC="$PROBE_SEC" WCET_PROBE_TAG=ci_probe \
    bash "$PROBE" "$CYCLIC" "$OBJ" "$PROBE_SEC" >"$PROBE_OUT" 2>&1; then
    cat "$PROBE_OUT" >&2
    plc_die "$PLC_E_KMOD" "WCET 板级探针失败"
fi
cat "$PROBE_OUT" | tail -3

ABS_MAX=$(sed -n 's/.*abs_max_ns=\([0-9-]*\).*/\1/p' "$PROBE_OUT" | tail -1)
python3 - "$REPORT" "$ABS_MAX" "$MAX_NS" "$PROBE_SEC" <<'PY'
import json, sys
report, abs_max, max_ns, sec = sys.argv[1:5]
if not abs_max or not abs_max.lstrip("-").isdigit():
    raise SystemExit("missing abs_max_ns")
val = int(abs_max)
if val > int(max_ns):
    raise SystemExit(f"abs_max_ns {val} > threshold {max_ns}")
doc = {
    "mode": "insmod",
    "abs_max_ns": val,
    "max_ns_threshold": int(max_ns),
    "probe_sec": int(sec),
}
open(report, "w", encoding="utf-8").write(json.dumps(doc, indent=2) + "\n")
print(f"    abs_max_ns={val} (threshold={max_ns})")
PY

echo "✅ WCET 板级探针门禁通过"
