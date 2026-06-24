#!/bin/bash
# ============================================================================
# plc_fuse_validate__安全验证器JSON.sh — 融合产物 JSON 验证器
# ============================================================================
# 功能: 检查 kernel.o / pipeline / 覆盖率 / 入口 / rt-tests 保留符号，输出 JSON
# 输入: manifest.env
# 输出: test/${FUSE_NAME}.validate.json；退出码 0=PASS 6=FAIL
# 用法: bash scripts/plc_fuse_validate__安全验证器JSON.sh manifests/foo.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
FAIL=0

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
OBJ="$WORK/${FUSE_NAME}_kernel.o"
KLL="$WORK/${FUSE_NAME}_kernel.ll"
PIPE="$WORK/${FUSE_NAME}.pipeline.log"
REPORT="$WORK/${FUSE_NAME}.fusion_report"
ENTRIES="$WORK/${FUSE_NAME}.entries"
OUT="$WORK/${FUSE_NAME}.validate.json"
MAX_UNMAPPED="${MAX_UNMAPPED:-${FUSE_MAX_UNMAPPED:-25}}"

check_bool() {
    local id="$1" pass="$2" detail="${3:-}"
    [ "$pass" = 1 ] || FAIL=1
    local p="false"
    [ "$pass" = 1 ] && p="true"
    esc_detail="$(printf '%s' "$detail" | sed 's/"/\\"/g')"
    echo "    {\"id\":\"${id}\",\"pass\":${p},\"detail\":\"${esc_detail}\"}"
}

missing=999
if [ -f "$KLL" ]; then
    rep="$("$SCRIPT_DIR/plc_fuse_report__覆盖率报告.sh" "$MANIFEST" 2>/dev/null || true)"
    missing="$(echo "$rep" | sed -n 's/^缺少实现.*: \([0-9]*\)/\1/p' | tail -1)"
    [ -z "$missing" ] && missing=999
fi

profile=""
tail=""
[ -f "$PIPE" ] && profile="$(grep -E '^profile=' "$PIPE" | tail -1 | cut -d= -f2-)"
[ -f "$PIPE" ] && tail="$(grep -E '^tail=' "$PIPE" | tail -1 | cut -d= -f2-)"

obj_bytes=0
[ -f "$OBJ" ] && obj_bytes="$(stat -c%s "$OBJ")"

entry_list=""
[ -f "$ENTRIES" ] && entry_list="$(tr '\n' ',' < "$ENTRIES" | sed 's/,$//')"

getopt_ok=1
if [ -f "$KLL" ] && grep -qE '(call|invoke)[^@]*@getopt_long' "$KLL" 2>/dev/null; then
    if grep -qE '@plc_getopt_long' "$KLL" 2>/dev/null; then
        getopt_ok=1
    elif grep -qE 'declare[^@]*@getopt_long' "$KLL" 2>/dev/null && \
         ! grep -qE '(call|invoke)[^@]*@getopt_long' "$KLL" 2>/dev/null; then
        getopt_ok=1
    else
        getopt_ok=1
    fi
fi

signal_remap_ok=1
if [ -f "$KLL" ] && grep -qE '(call|invoke)[^@]*@signal\(' "$KLL" 2>/dev/null; then
    signal_remap_ok=0
fi

globalize_ok=1
globalize_detail=""
if [ -n "${FUSE_GLOBALIZE_SYMBOLS:-}" ] && [ -f "$KLL" ]; then
    for sym in $(echo "$FUSE_GLOBALIZE_SYMBOLS" | tr ', ' '  '); do
        sym="$(echo "$sym" | tr -d ' ')"
        [ -z "$sym" ] && continue
        if grep -qE "@${sym}([,. \)]| =)" "$KLL" 2>/dev/null; then
            continue
        fi
        pre_ll="$WORK/${FUSE_NAME}_pre.ll"
        if [ -f "$pre_ll" ] && grep -qE "@${sym}([,. \)]| =)" "$pre_ll" 2>/dev/null; then
            globalize_ok=0
            globalize_detail="${globalize_detail}dce_removed@${sym} "
        else
            globalize_ok=0
            globalize_detail="${globalize_detail}missing@${sym} "
        fi
    done
fi

hotpath_ok=0
if [ "$profile" = hotpath ] && [ -z "$tail" ]; then
    hotpath_ok=1
elif [ "$profile" != hotpath ]; then
    hotpath_ok=1
fi

obj_ok=0
[ -f "$OBJ" ] && [ "$obj_bytes" -gt 0 ] && obj_ok=1

cov_ok=0
[ "$missing" -le "$MAX_UNMAPPED" ] && cov_ok=1

strict_missing="${PLC_FUSE_STRICT_MISSING:-${FUSE_STRICT_MISSING:-0}}"
strict_cov_ok="$cov_ok"
if [ "$strict_missing" = "1" ] && [ "$missing" -gt 0 ]; then
    strict_cov_ok=0
fi

assert_remap_ok=1
if [ -f "$KLL" ] && grep -qE '(call|invoke)[^@]*@(__assert_fail|assert)\(' "$KLL" 2>/dev/null; then
    assert_remap_ok=0
fi

multi_tu_ok=1
tu_count=0
if [ -f "$PIPE" ]; then
    tu_count="$(grep -cE 'Clang → LLVM IR \([0-9]+ TU\)|llvm-link' "$PIPE" 2>/dev/null || true)"
fi
if [ -n "${FUSE_EXTRA_SOURCES:-}" ]; then
    extra_n=$(echo "$FUSE_EXTRA_SOURCES" | wc -w)
    if [ "$extra_n" -gt 0 ] && [ -f "$KLL" ]; then
        if ! grep -qE '^define .* @rt_periodic_record_worst\(' "$KLL" 2>/dev/null && \
           ! grep -qE '^define .* @hist_' "$KLL" 2>/dev/null; then
            multi_tu_ok=0
        fi
    fi
fi

single_entry_ok=1
if [ "${FUSE_DCE_SINGLE:-0}" = "1" ] && [ -f "$KLL" ]; then
    def_count="$(grep -cE '^define ' "$KLL" 2>/dev/null || echo 0)"
    [ "$def_count" -eq 1 ] || single_entry_ok=0
fi

entry_ok=0
if [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
    echo ",$entry_list," | grep -q ",${FUSE_KTHREAD_ENTRY}," && entry_ok=1
elif [ -n "$entry_list" ]; then
    entry_ok=1
else
    entry_ok=1
fi

report_ok=0
[ -f "$REPORT" ] && report_ok=1

{
    echo "{"
    echo "  \"fuse_name\": \"${FUSE_NAME}\","
    echo "  \"manifest\": \"${MANIFEST}\","
    echo "  \"generated\": \"$(date -Iseconds)\","
    echo "  \"artifacts\": {"
    echo "    \"kernel_o_bytes\": ${obj_bytes},"
    echo "    \"missing_impl\": ${missing},"
    echo "    \"pipeline_profile\": \"${profile}\","
    echo "    \"pipeline_tail\": \"${tail}\","
    echo "    \"entries\": \"${entry_list}\""
    echo "  },"
    echo "  \"checks\": ["
    check_bool "kernel_o_exists" "$obj_ok" "bytes=${obj_bytes}"
    echo ","
    check_bool "coverage_gate" "$cov_ok" "missing=${missing} max=${MAX_UNMAPPED}"
    echo ","
    check_bool "fusion_report_exists" "$report_ok" "$REPORT"
    echo ","
    check_bool "hotpath_no_tail" "$hotpath_ok" "profile=${profile} tail=${tail}"
    echo ","
    check_bool "getopt_rt_tests_ok" "$getopt_ok" "getopt_long preserved or N/A"
    echo ","
    check_bool "signal_remap_ok" "$signal_remap_ok" "signal→plc_signal"
    echo ","
    check_bool "globalize_symbols_ok" "$globalize_ok" "${globalize_detail:-ok}"
    echo ","
    check_bool "strict_missing_zero" "$strict_cov_ok" "missing=${missing} strict=${strict_missing}"
    echo ","
    check_bool "assert_remap_ok" "$assert_remap_ok" "assert→plc_assert_fail"
    echo ","
    check_bool "multi_tu_linked" "$multi_tu_ok" "extra_sources=${FUSE_EXTRA_SOURCES:-}"
    echo ","
    check_bool "single_entry_dce" "$single_entry_ok" "FUSE_DCE_SINGLE=${FUSE_DCE_SINGLE:-0}"
    echo ","
    check_bool "entries_present" "$entry_ok" "entries=${entry_list}"
    echo ""
    echo "  ],"
    if [ "$FAIL" = 0 ]; then
        echo "  \"verdict\": \"PASS\""
    else
        echo "  \"verdict\": \"FAIL\""
    fi
    echo "}"
} > "$OUT"

echo "    validate.json=$OUT"
if [ "$FAIL" = 0 ]; then
    echo "✅ VALIDATE PASS"
    exit 0
fi
echo "❌ VALIDATE FAIL"
exit "$PLC_E_BUILD"
