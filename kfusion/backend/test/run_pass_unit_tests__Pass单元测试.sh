#!/bin/bash
# ============================================================================
# run_pass_unit_tests__Pass单元测试.sh — Pass 回归（FileCheck 或 grep 回退）
# ============================================================================
# 每个 *.ll 可选头注释:
#   ; PASS: plc-fusion-remap|plc-fusion-wcet-mark|plc-fusion-dce
#   ; ENV: KEY=VAL（可多次出现，合并为 env 前缀）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KFUSION="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD="$KFUSION/build"
# shellcheck source=../../scripts/fuse/plc_fusion_common__公共库.sh
source "$KFUSION/scripts/fuse/plc_fusion_common__公共库.sh"

PASS_SO="$(plc_fusion_pass_so "$KFUSION" "$BUILD")"

if [ ! -f "$PASS_SO" ]; then
    echo "=== Pass 单元测试: 编译 KFusionPass ==="
    mkdir -p "$BUILD"
    (cd "$BUILD" && cmake .. >/dev/null && make KFusionPass -j"$(nproc)" >/dev/null)
    PASS_SO="$(plc_fusion_pass_so "$KFUSION" "$BUILD")"
fi

OPT="$(plc_resolve_tool OPT_BIN opt-19 opt-18 opt-17 opt)"
USE_FILECHECK=1
FILECHECK=""
for candidate in FileCheck-19 FileCheck-18 FileCheck-17 FileCheck; do
    if command -v "$candidate" >/dev/null 2>&1; then
        FILECHECK="$candidate"
        break
    fi
done
if [ -z "$FILECHECK" ]; then
    for ver in 19 18 17; do
        if [ -x "/usr/lib/llvm-${ver}/bin/FileCheck" ]; then
            FILECHECK="/usr/lib/llvm-${ver}/bin/FileCheck"
            break
        fi
    done
fi
[ -n "$FILECHECK" ] || USE_FILECHECK=0

ll_pass_pipeline() {
    local ll="$1"
    local pass envline
    pass="$(grep -m1 '^; PASS:' "$ll" 2>/dev/null | sed 's/^; PASS:[[:space:]]*//' || true)"
    [ -n "$pass" ] || pass="plc-fusion-remap"
    echo "$pass"
}

ll_pass_env() {
    local ll="$1"
    grep '^; ENV:' "$ll" 2>/dev/null | sed 's/^; ENV:[[:space:]]*//' || true
}

run_grep_fallback() {
    local ll="$1" name out passes
    name="$(basename "$ll")"
    passes="$(ll_pass_pipeline "$ll")"
    echo "   ▶ $name (grep fallback — passes=$passes)"
    out="$(run_ll_pass "$ll")"
    case "$name" in
        remap_printf.ll)
            echo "$out" | grep -qE 'call .*@printf' && { echo "❌ @printf 未 remap"; return 1; }
            echo "$out" | grep -qE 'call .*@plc_printk' || { echo "❌ 无 plc_printk"; return 1; }
            ;;
        remap_sem.ll)
            echo "$out" | grep -qE 'call .*@sem_wait' && return 1
            echo "$out" | grep -qE 'call .*@plc_sem_wait' || return 1
            ;;
        remap_vsnprintf.ll)
            echo "$out" | grep -qE 'call .*@vsnprintf' && return 1
            echo "$out" | grep -qE 'call .*@plc_snprintf' || return 1
            ;;
        remap_malloc.ll)
            echo "$out" | grep -qE 'call .*@malloc' && return 1
            echo "$out" | grep -qE 'call .*@plc_kmalloc' || return 1
            ;;
        remap_pthread_create.ll)
            echo "$out" | grep -qE 'call .*@plc_pthread_create' || return 1
            echo "$out" | grep -qE 'define .*@worker' || return 1
            ;;
        remap_exit.ll)
            echo "$out" | grep -qE 'call .*@exit' && return 1
            echo "$out" | grep -qE 'call .*@plc_exit' || return 1
            ;;
        indirect_bsearch.ll)
            echo "$out" | grep -qE 'call .*@bsearch' || { echo "❌ 无 @bsearch"; return 1; }
            echo "$out" | grep -qE 'call i32 (%fn|@compar)' || { echo "❌ compar 间接调用被 blackhole"; return 1; }
            ;;
        indirect_qsort.ll)
            echo "$out" | grep -qE 'call .*@qsort' || { echo "❌ 无 @qsort"; return 1; }
            echo "$out" | grep -qE 'call i32 (%cmp|@compar)' || { echo "❌ qsort compar 被 blackhole"; return 1; }
            ;;
        indirect_global_fnptr.ll)
            echo "$out" | grep -qE 'call void .*%fn' || { echo "❌ 全局 fn-ptr 间接调用被 blackhole"; return 1; }
            echo "$out" | grep -qE 'call void null' && return 1
            ;;
        indirect_store_alloca.ll)
            echo "$out" | grep -qE 'call void .*%fn' || { echo "❌ alloca store/load 间接调用被 blackhole"; return 1; }
            echo "$out" | grep -qE 'call void null' && return 1
            ;;
        remap_sched_getaffinity.ll)
            echo "$out" | grep -qE 'call .*@sched_getaffinity' && return 1
            echo "$out" | grep -qE 'call .*@plc_sched_getaffinity' || return 1
            ;;
        wcet_mark_hot.ll)
            echo "$out" | grep -q 'optnone' || return 1
            echo "$out" | grep -q 'plc-wcet-hot' || return 1
            echo "$out" | grep -q 'define void @hot_fn' || return 1
            echo "$out" | grep -q 'define void @cold_fn' || return 1
            ;;
        dce_roots.ll)
            echo "$out" | grep -q 'define i32 @main' || return 1
            if echo "$out" | grep -q 'define void @dead_helper'; then
                echo "❌ dead_helper 未删除" >&2
                return 1
            fi
            ;;
        *)
            echo "❌ 未知测试 $name（无 grep 回退）" >&2
            return 1
            ;;
    esac
    return 0
}

run_ll_pass() {
    local ll="$1"
    local passes envlines envargs=()
    passes="$(ll_pass_pipeline "$ll")"
    while IFS= read -r line; do
        [ -n "$line" ] && envargs+=("$line")
    done < <(ll_pass_env "$ll")
    if [ "${#envargs[@]}" -gt 0 ]; then
        env "${envargs[@]}" "$OPT" -load-pass-plugin "$PASS_SO" \
            -passes="$passes" "$ll" -S
    else
        "$OPT" -load-pass-plugin "$PASS_SO" -passes="$passes" "$ll" -S
    fi
}

run_wcet_schedule_negative() {
    local ll="$SCRIPT_DIR/wcet_schedule_smoke.ll"
    local bad="$SCRIPT_DIR/.wcet_schedule_bad.json"
    local err="$SCRIPT_DIR/.wcet_schedule_bad.err"
    local out
    [ -f "$ll" ] || return 0
    echo "   ▶ wcet_schedule negative (bad pipeline)"
    cat >"$bad" <<'JSON'
{
  "version": 1,
  "cold_sequences": {
    "cold_fn": ["this-pass-does-not-exist-xyz"]
  },
  "module_passes": []
}
JSON
    out="$(env PLC_FUSION_WCET_SCHEDULE_FILE="$bad" \
        PLC_FUSION_WCET_SCHEDULE_ERR="$err" \
        PLC_FUSION_WCET_SCHEDULE_STRICT=1 \
        "$OPT" -load-pass-plugin "$PASS_SO" \
        -passes=plc-fusion-wcet-schedule "$ll" -S 2>&1)" || true
    if ! echo "$out" | grep -qE '\[KFusionWCETSchedule\] ERROR'; then
        echo "❌ 预期 WCET schedule 错误日志" >&2
        echo "$out" >&2
        return 1
    fi
    if [ ! -s "$err" ]; then
        echo "❌ 预期 PLC_FUSION_WCET_SCHEDULE_ERR 文件 (path=$err)" >&2
        return 1
    fi
    rm -f "$bad" "$err"
}

echo "=== K-Fusion Pass 单元测试 ==="
echo "    pass=$PASS_SO"
echo "    opt=$OPT"
echo "    filecheck=$([ "$USE_FILECHECK" = 1 ] && echo "$FILECHECK" || echo 'grep-fallback')"

FAIL=0
for ll in "$SCRIPT_DIR"/*.ll; do
    name="$(basename "$ll")"
    case "$name" in
        wcet_schedule_smoke.ll) continue ;;
    esac
    if [ "$USE_FILECHECK" = 1 ]; then
        passes="$(ll_pass_pipeline "$ll")"
        echo "   ▶ $name (FileCheck passes=$passes)"
        if ! run_ll_pass "$ll" | "$FILECHECK" "$ll"; then
            echo "❌ FileCheck 失败: $name" >&2
            FAIL=1
        fi
    else
        run_grep_fallback "$ll" || FAIL=1
    fi
done

run_wcet_schedule_negative || FAIL=1

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
echo "✅ Pass 单元测试通过 (${SCRIPT_DIR}/*.ll + wcet schedule negative)"
