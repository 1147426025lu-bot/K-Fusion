#!/bin/bash
# ============================================================================
# run_ci_ast_manifest_matrix__CI_AST矩阵.sh — 全 manifest AST 融合预检汇总表
# ============================================================================
# 用法: bash scripts/run_ci_ast_manifest_matrix__CI_AST矩阵.sh
# 环境: MANIFESTS="..."  自定义列表；AST_MATRIX_STRICT=1  任一 critical>0 则 exit 1
# 输出: test/ci_ast_manifest_matrix.csv + 终端表格
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
PREFLIGHT="$SCRIPT_DIR/fuse/plc_fusion_ast_preflight__AST融合预检.sh"
CSV="${AST_MATRIX_CSV:-$PROJECT_ROOT/test/ci_ast_manifest_matrix.csv}"

DEFAULT_MANIFESTS=(
    "$PROJECT_ROOT/manifests/manifest_cyclictest__主线压测.env"
    "$PROJECT_ROOT/manifests/manifest_cyclictest__多TU压测.env"
    "$PROJECT_ROOT/manifests/manifest_signaltest__信号测试.env"
    "$PROJECT_ROOT/manifests/manifest_ptsematest__互斥锁测试.env"
    "$PROJECT_ROOT/manifests/manifest_github_rt_periodic__周期demo.env"
    "$PROJECT_ROOT/manifests/manifest_github_rt_periodic_multitu__多TU.env"
    "$PROJECT_ROOT/manifests/manifest_github_stb_sprintf__sprintf_demo.env"
    "$PROJECT_ROOT/manifests/manifest_plc_cc_gpio__PLC示例.env"
    "$PROJECT_ROOT/manifests/manifest_plc_cc_pure_logic__纯逻辑.env"
    "$PROJECT_ROOT/manifests/manifest_plc_cc_temp_control__温控.env"
    "$PROJECT_ROOT/manifests/manifest_plc_cc_isolation__隔离测试.env"
    "$PROJECT_ROOT/manifests/manifest_plc_cc_dither__抖动测试.env"
    "$PROJECT_ROOT/manifests/manifest_plc_cc_hello__入门.env"
)

if [ -n "${MANIFESTS:-}" ]; then
    # shellcheck disable=SC2206
    MANIFEST_LIST=($MANIFESTS)
else
    MANIFEST_LIST=("${DEFAULT_MANIFESTS[@]}")
fi

PLC_AST="$PROJECT_ROOT/build/plc_ast"
if [ ! -x "$PLC_AST" ]; then
    (cd "$PROJECT_ROOT/build" && cmake .. >/dev/null && make plc_ast -j"$(nproc)" >/dev/null)
fi

mkdir -p "$(dirname "$CSV")"
echo "manifest,fuse_name,eligible,fusion_crit,fusion_warn,entry,errors,status" > "$CSV"

export FUSE_AST_PREFLIGHT=1
export FUSE_AST_PREFLIGHT_STRICT=0

TOTAL_CRIT=0
FAIL_NAMES=()

printf "=== AST manifest 矩阵 (%d) ===\n" "${#MANIFEST_LIST[@]}"
printf "%-28s %-6s %5s %5s %5s %-12s %s\n" "manifest" "elig" "crit" "warn" "err" "entry" "status"
printf "%-28s %-6s %5s %5s %5s %-12s %s\n" "--------" "----" "----" "----" "---" "------" "------"

for m in "${MANIFEST_LIST[@]}"; do
  [[ "$m" != /* ]] && m="$PROJECT_ROOT/$m"
  base="$(basename "$m")"
  if [ ! -f "$m" ]; then
    printf "%-28s %-6s %5s %5s %5s %-12s %s\n" "$base" "-" "-" "-" "-" "-" "SKIP(no file)"
    echo "$base,,-,-,-,-,-,SKIP" >> "$CSV"
    continue
  fi

  # shellcheck disable=SC1090
  source "$m"
  name="${FUSE_NAME:-?}"
  work="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
  json="$work/${name}.fusion_ast.json"

  set +e
  bash "$PREFLIGHT" "$m" >/dev/null 2>&1
  set -e

  if [ ! -f "$json" ]; then
    printf "%-28s %-6s %5s %5s %5s %-12s %s\n" "$base" "-" "-" "-" "-" "-" "SKIP(no json)"
    echo "$base,$name,-,-,-,-,-,SKIP" >> "$CSV"
    continue
  fi

  read -r elig crit warn entry errs status <<< "$(python3 - "$json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
elig = "yes" if d.get("fusion_eligible") else "no"
crit = int(d.get("fusion_critical_count", 0))
warn = int(d.get("fusion_warn_count", 0))
entry = d.get("entry")
if not entry and d.get("has_main"):
    entry = "main"
entry = entry or "-"
errs = int(d.get("error_count", 0))
status = "PASS"
if crit > 0:
    status = "FAIL_crit"
elif not d.get("fusion_eligible"):
    status = "FAIL_elig"
elif warn > 0:
    status = "WARN"
print(f"{elig}\t{crit}\t{warn}\t{entry}\t{errs}\t{status}")
PY
)"

  printf "%-28s %-6s %5s %5s %5s %-12s %s\n" "$base" "$elig" "$crit" "$warn" "$errs" "$entry" "$status"
  echo "$base,$name,$elig,$crit,$warn,$entry,$errs,$status" >> "$CSV"

  if [ "$crit" -gt 0 ] || [ "$elig" = "no" ]; then
    FAIL_NAMES+=("$name")
    TOTAL_CRIT=$((TOTAL_CRIT + crit))
  fi
done

echo ""
echo "    csv=$CSV"

if [ "${AST_MATRIX_STRICT:-1}" = "1" ] && [ "${#FAIL_NAMES[@]}" -gt 0 ]; then
  plc_die "$PLC_E_VALIDATE" "AST 矩阵未通过: ${FAIL_NAMES[*]}" "见 fusion_critical / fusion_eligible"
fi

if [ "${#FAIL_NAMES[@]}" -eq 0 ]; then
  echo "✅ AST manifest 矩阵通过（无 critical / ineligible）"
else
  echo "⚠️  AST manifest 矩阵: ${#FAIL_NAMES[@]} 项 FAIL（AST_MATRIX_STRICT=0 仅报告）"
fi
