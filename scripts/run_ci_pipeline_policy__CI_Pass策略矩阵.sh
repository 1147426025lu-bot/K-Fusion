#!/bin/bash
# ============================================================================
# run_ci_pipeline_policy__CI_Pass策略矩阵.sh — 13 manifest Pass 策略汇总
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(plc_project_root)"
POLICY="$SCRIPT_DIR/fuse/plc_fusion_pipeline_policy__Pass策略解析.sh"
CSV="${PIPELINE_POLICY_CSV:-$PROJECT_ROOT/test/ci_pipeline_policy_matrix.csv}"

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

mkdir -p "$(dirname "$CSV")"
echo "manifest,fuse_name,policy,ast_plan,wcet_search,wcet_autotune,reason" > "$CSV"

printf "=== Pass 策略矩阵 (%d) ===\n" "${#MANIFEST_LIST[@]}"
printf "%-32s %-12s %4s %4s %4s %s\n" "manifest" "policy" "ast" "srch" "auto" "reason"
printf "%-32s %-12s %4s %4s %4s %s\n" "--------" "------" "---" "----" "----" "------"

BENCH=0
AUTO=0

for m in "${MANIFEST_LIST[@]}"; do
    [[ "$m" != /* ]] && m="$PROJECT_ROOT/$m"
    base="$(basename "$m")"
    if [ ! -f "$m" ]; then
        printf "%-32s %-12s %4s %4s %4s %s\n" "$base" "-" "-" "-" "-" "SKIP"
        continue
    fi
    bash "$POLICY" "$m" >/dev/null
    # shellcheck disable=SC1090
    source "$m"
    log="$PROJECT_ROOT/test/${FUSE_NAME}.pipeline_policy.log"
    if [ ! -f "$log" ]; then
        printf "%-32s %-12s %4s %4s %4s %s\n" "$base" "?" "?" "?" "?" "no log"
        continue
    fi
    pol=$(grep '^policy=' "$log" 2>/dev/null | cut -d= -f2- || echo "?")
    reason=$(grep '^reason=' "$log" 2>/dev/null | cut -d= -f2- || echo "")
    ast=$(grep '^FUSE_AST_PLAN=' "$log" 2>/dev/null | cut -d= -f2- || echo "?")
    srch=$(grep '^FUSE_WCET_SEARCH=' "$log" 2>/dev/null | cut -d= -f2- || echo "?")
    auto=$(grep '^FUSE_WCET_AUTOTUNE=' "$log" 2>/dev/null | cut -d= -f2- || echo "0")
    printf "%-32s %-12s %4s %4s %4s %s\n" "$base" "$pol" "$ast" "$srch" "$auto" "$reason"
    echo "$base,${FUSE_NAME:-?},$pol,$ast,$srch,$auto,$reason" >> "$CSV"
    case "$pol" in
        wcet-benchmark) BENCH=$((BENCH + 1)) ;;
        ast-auto) AUTO=$((AUTO + 1)) ;;
    esac
done

echo ""
echo "    ast-auto=$AUTO wcet-benchmark=$BENCH csv=$CSV"
echo "✅ Pass 策略矩阵完成"
