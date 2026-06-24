#!/bin/bash
# ============================================================================
# run_paper_feasibility__论文可行性扫描.sh — 13 manifest 覆盖率 / 缺符号扫描
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=paper_common__论文公共.sh
source "$SCRIPT_DIR/paper_common__论文公共.sh"
# shellcheck source=../plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(paper_root)"

RUN_ID="paper_feasibility_$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$PROJECT_ROOT/results/paper/feasibility"
CSV="$OUT_DIR/${RUN_ID}.csv"
REPORT="$SCRIPT_DIR/../plc_fuse_report__覆盖率报告.sh"
CHECK="$SCRIPT_DIR/../plc_fuse_check__覆盖率门禁.sh"
mkdir -p "$OUT_DIR"

echo "manifest,fuse_name,has_kernel_ll,unmapped_count,check_pass,host_hint,category" >"$CSV"

category_of() {
    case "$1" in
        *cyclictest*|*rt_periodic*) echo "periodic_timer" ;;
        *signaltest*) echo "signal_wait" ;;
        *ptsematest*) echo "mutex_lock" ;;
        *gpio*|*temp*|*dither*|*isolation*) echo "plc_logic" ;;
        *hello*|*pure_logic*) echo "pure_logic" ;;
        *sprintf*) echo "stdio_libc" ;;
        *) echo "other" ;;
    esac
}

for m in "$PROJECT_ROOT"/manifests/manifest_*.env; do
    [ -f "$m" ] || continue
    case "$(basename "$m")" in
        manifest_template__*) continue ;;
    esac
    # shellcheck source=/dev/null
    source "$m"
    name="${FUSE_NAME:-unknown}"
    work="$PROJECT_ROOT/test"
    kll="$work/${name}_kernel.ll"
    obj="$work/${name}_kernel.o"
    obj_ship="$work/${name}_kernel.o_shipped"
    unmapped="$work/${name}.unmapped"
    has_ll=0
    um=na
    pass=na
    host="${FUSE_HOST:-generic}"

    if [ ! -f "$kll" ] && [ ! -f "$obj" ] && [ ! -f "$obj_ship" ] && [ "${FUSE_IF_MISSING:-0}" = "1" ]; then
        echo "  → fuse $(basename "$m")（缺失 kernel.ll）..."
        if ! bash "$SCRIPT_DIR/../plc_fuse__内核化主流程.sh" "$m"; then
            echo "  ⚠️  fuse 失败: $(basename "$m")"
        fi
    fi
    [ -f "$kll" ] || [ -f "$obj" ] || [ -f "$obj_ship" ] && has_ll=1
    if [ -f "$unmapped" ]; then
        um=$(grep -c . "$unmapped" 2>/dev/null || echo 0)
    fi
    if [ -f "$kll" ]; then
        if bash "$CHECK" "$m" >/dev/null 2>&1; then pass=1; else pass=0; fi
    elif [ -f "$obj" ] || [ -f "$obj_ship" ]; then
        pass=1
    fi
    cat="${FUSE_HOST:-auto}"
    echo "$(basename "$m"),$name,$has_ll,$um,$pass,$host,$(category_of "$(basename "$m")")" >>"$CSV"
    echo "  $(basename "$m"): ll=$has_ll unmapped=$um pass=$pass host=$host"
done

{
    echo "# 可行性扫描 $RUN_ID"
    echo ""
    column -t -s, "$CSV" 2>/dev/null || cat "$CSV"
} >"$OUT_DIR/${RUN_ID}_summary.md"

echo "✅ feasibility → $CSV"
if grep -q ',0,na' "$CSV" 2>/dev/null; then
    echo "ℹ️  部分 manifest 无 kernel.ll — 正式论文表前请先:"
    echo "    bash scripts/run_ci__CI门禁.sh"
    echo "    或 FUSE_IF_MISSING=1 bash scripts/paper/run_paper_feasibility__论文可行性扫描.sh"
fi
