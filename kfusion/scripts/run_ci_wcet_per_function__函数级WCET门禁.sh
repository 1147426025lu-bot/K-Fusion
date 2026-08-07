#!/bin/bash
# ============================================================================
# run_ci_wcet_per_function__函数级WCET门禁.sh — Lavinium 函数级 schedule CI smoke
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
MANIFEST="${1:-$PRJ/manifests/manifest_github_rt_periodic__周期demo.env}"
plc_resolve_manifest "$MANIFEST" "$PRJ"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST"

FUSE="$SCRIPT_DIR/fuse/plc_fuse__内核化主流程.sh"
WORK="${FUSE_WORK_DIR:-$PRJ/test}"
PRE_LL="$WORK/${FUSE_NAME}_pre.ll"

echo "=== CI 函数级 WCET 门禁 ==="
echo "    manifest=$MANIFEST"

if [ ! -f "$PRE_LL" ]; then
    echo "    fuse → 生成 pre.ll"
    bash "$FUSE" "$MANIFEST"
fi
plc_require_file "$PRE_LL" "pre.ll"

export FUSE_WCET_ASSOC_BUDGET="${FUSE_WCET_ASSOC_BUDGET:-10}"
export FUSE_WCET_ASSOC_MAX_FUNCS="${FUSE_WCET_ASSOC_MAX_FUNCS:-2}"
export FUSE_WCET_ASSOC_BUDGET_PER_FN="${FUSE_WCET_ASSOC_BUDGET_PER_FN:-8}"
export FUSE_WCET_ASSOC_SKIP=0

bash "$SCRIPT_DIR/plc_fusion_wcet_per_function__函数级WCET.sh" "$MANIFEST"

SCHEDULE="$WORK/${FUSE_NAME}.wcet_schedule.json"
REPORT="$WORK/${FUSE_NAME}.wcet_per_function.json"
VALIDATE_PY="$SCRIPT_DIR/plc_fusion_wcet_schedule_validate__调度JSON校验.py"

plc_require_file "$SCHEDULE" "wcet schedule"
python3 "$VALIDATE_PY" "$SCHEDULE" --require-cold

echo "    schema negative smoke..."
python3 - "$VALIDATE_PY" <<'PY'
import json, subprocess, sys, tempfile
from pathlib import Path
validate = Path(sys.argv[1])
bad = {"version": 2, "hot_functions": [], "cold_sequences": {}}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(bad, f)
    path = f.name
rc = subprocess.run([sys.executable, str(validate), path], capture_output=True).returncode
Path(path).unlink(missing_ok=True)
if rc == 0:
    print("expected validate failure for bad schedule", file=sys.stderr)
    sys.exit(1)
print("    schema negative ok")
PY

if [ -f "$REPORT" ]; then
    tuned=$(python3 -c "import json; print(json.load(open('$REPORT'))['cold_functions_tuned'])")
    fails=$(python3 -c "import json; print(json.load(open('$REPORT')).get('compile_failures', 0))")
    echo "    per-fn tuned=$tuned compile_failures=$fails"
    if [ "$fails" -gt 0 ]; then
        plc_die "$PLC_E_BUILD" "WCET 搜索 compile_failures=$fails（见 test/.wcet_per_fn_search/*.err）"
    fi
    python3 - "$REPORT" <<'PY'
import json, sys
rep = json.load(open(sys.argv[1], encoding="utf-8"))
for row in rep.get("results") or []:
    cold = row.get("winner_cold") or []
    assert cold, f"empty winner_cold for {row.get('function')}"
    print(f"    winner {row.get('function')}: {len(cold)} cold passes")
PY
fi

BUILD="$PRJ/build"
PASS_SO="$(plc_fusion_pass_so "$PRJ" "$BUILD")"
OPT="$(plc_resolve_tool OPT_BIN opt-19 opt-18 opt-17 opt)"
OUT_LL="$WORK/${FUSE_NAME}.wcet_ci_smoke.ll"
export PLC_FUSION_WCET_SCHEDULE_FILE="$SCHEDULE"
export PLC_FUSION_WCET_HOT_FUNCTIONS="${FUSE_HOT_PATH_FUNCTIONS:-${FUSE_KTHREAD_ENTRY:-}}"

echo "    opt smoke: plc-kernelize-wcet,plc-fusion-wcet-schedule"
if ! "$OPT" -load-pass-plugin "$PASS_SO" \
    -passes=plc-kernelize-wcet,plc-fusion-wcet-schedule \
    "$PRE_LL" -S -o "$OUT_LL" 2>"$WORK/${FUSE_NAME}.wcet_ci_smoke.err"; then
    cat "$WORK/${FUSE_NAME}.wcet_ci_smoke.err" >&2
    plc_die "$PLC_E_BUILD" "函数级 WCET opt smoke 失败"
fi
plc_require_file "$OUT_LL" "wcet smoke ll"

echo "✅ 函数级 WCET CI 门禁通过"
