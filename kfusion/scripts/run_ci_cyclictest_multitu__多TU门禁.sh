#!/bin/bash
# ============================================================================
# run_ci_cyclictest_multitu__多TU门禁.sh — cyclictest + histogram 多 TU fuse CI smoke
# ============================================================================
# 用法: bash scripts/run_ci_cyclictest_multitu__多TU门禁.sh
# 环境: SKIP_FUSE=1  跳过 fuse（要求 test/*_pre.ll 已存在）
#       WCET_PARTITION_ONLY=1  只做分区 JSON，不跑 association
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
MANIFEST="$PRJ/manifests/manifest_cyclictest__多TU压测.env"
FUSE="$SCRIPT_DIR/fuse/plc_fuse__内核化主流程.sh"
CHECK="$SCRIPT_DIR/fuse/plc_fuse_check__覆盖率门禁.sh"
VALIDATE="$SCRIPT_DIR/fuse/plc_fuse_validate__安全验证器JSON.sh"
PARTITION_PY="$PRJ/scripts/plc_fusion_wcet_partition__函数级分区.py"
WCET_FN="$SCRIPT_DIR/plc_fusion_wcet_per_function__函数级WCET.sh"

plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST"

WORK="${FUSE_WORK_DIR:-$PRJ/test}"
PRE_LL="$WORK/${FUSE_NAME}_pre.ll"
KERNEL_O="$WORK/${FUSE_NAME}_kernel.o"

echo "=== CI cyclictest 多 TU 门禁 ==="
echo "    manifest=$MANIFEST"

if [ "${SKIP_FUSE:-0}" != "1" ]; then
    echo "    fuse (git sync + 2 TU llvm-link)..."
    bash "$FUSE" "$MANIFEST"
else
    echo "    SKIP_FUSE=1"
fi

plc_require_file "$PRE_LL" "pre.ll" "bash scripts/fuse/plc_fuse__内核化主流程.sh $MANIFEST"
plc_require_file "$KERNEL_O" "kernel.o"

echo "    coverage check..."
bash "$CHECK" "$MANIFEST"

echo "    validate JSON..."
bash "$VALIDATE" "$MANIFEST"

HOT="${FUSE_HOT_PATH_FUNCTIONS:-timerthread,fifothread}"
SCHEDULE="$WORK/${FUSE_NAME}.wcet_schedule.json"
python3 "$PARTITION_PY" "$PRE_LL" -o "$SCHEDULE" \
    --hot "$HOT" \
    --roots "${FUSE_GLOBALIZE_SYMBOLS:-}" \
    --default-cold "${FUSE_COLD_PASS_SEQUENCE:-simplifycfg|sroa|instcombine|loop-mssa(loop-rotate,licm)|gvn|adce}" \
    --module "${FUSE_MODULE_PASS_SEQUENCE:-globaldce}"

python3 - "$SCHEDULE" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
hot = doc.get("hot_functions") or []
cold = doc.get("cold_sequences") or {}
assert doc.get("version") == 1
assert len(hot) >= 1, "expected at least one hot function"
print(f"    wcet partition ok hot={len(hot)} cold={len(cold)}")
PY

if [ "${WCET_PARTITION_ONLY:-0}" != "1" ]; then
    export FUSE_WCET_ASSOC_SKIP=1
    export FUSE_WCET_ASSOC_BUDGET=0
    bash "$WCET_FN" "$MANIFEST"
fi

echo "✅ cyclictest 多 TU CI 门禁通过"
