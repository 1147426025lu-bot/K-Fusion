#!/bin/bash
# ============================================================================
# run_manifest_onboarding_check__manifest清单门禁.sh — 新 manifest 必填项检查
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(plc_project_root)"
POLICY="$SCRIPT_DIR/fuse/plc_fusion_pipeline_policy__Pass策略解析.sh"

MANIFEST="${1:-}"
if [ -z "$MANIFEST" ]; then
    MANIFESTS=("$PROJECT_ROOT/manifests"/manifest_*.env)
else
    [[ "$MANIFEST" != /* ]] && MANIFEST="$PROJECT_ROOT/$MANIFEST"
    MANIFESTS=("$MANIFEST")
fi

FAIL=0
check_key() {
    local file="$1" key="$2"
    grep -qE "^${key}=" "$file" 2>/dev/null
}

for m in "${MANIFESTS[@]}"; do
    [ -f "$m" ] || continue
    base="$(basename "$m")"
    [[ "$base" == *template* ]] && continue
    # shellcheck disable=SC1090
    source "$m"
    name="${FUSE_NAME:-?}"
    errs=()

    check_key "$m" FUSE_NAME || errs+=("missing FUSE_NAME")
    check_key "$m" FUSE_SOURCE || errs+=("missing FUSE_SOURCE")
    check_key "$m" FUSE_DESC || errs+=("missing FUSE_DESC")

    bash "$POLICY" "$m" >/dev/null
    log="$PROJECT_ROOT/test/${name}.pipeline_policy.log"
    pol=$(grep '^policy=' "$log" 2>/dev/null | cut -d= -f2- || echo "?")

    case "$pol" in
        wcet-benchmark)
            check_key "$m" FUSE_WCET_MODE || errs+=("wcet-benchmark 建议 FUSE_WCET_MODE=1")
            if ! check_key "$m" FUSE_HOT_PATH_FUNCTIONS \
                && ! check_key "$m" FUSE_KTHREAD_ENTRY \
                && [ "${FUSE_RUN_MAIN:-0}" != "1" ]; then
                errs+=("wcet-benchmark 需 FUSE_HOT_PATH_FUNCTIONS 或 entry/RUN_MAIN")
            fi
            ;;
        ast-auto)
            if [[ "$name" == plc_cc_* ]]; then
                check_key "$m" FUSE_HOST || errs+=("plc-cc 建议 FUSE_HOST")
            fi
            ;;
    esac

    if [ "${#errs[@]}" -gt 0 ]; then
        FAIL=1
        echo "❌ $base ($name policy=$pol)"
        for e in "${errs[@]}"; do echo "      $e"; done
    else
        echo "✅ $base ($name policy=$pol)"
    fi
done

[ "$FAIL" -eq 0 ] || plc_die "$PLC_E_VALIDATE" "manifest onboarding 检查未通过"
echo "✅ manifest onboarding 全部通过"
