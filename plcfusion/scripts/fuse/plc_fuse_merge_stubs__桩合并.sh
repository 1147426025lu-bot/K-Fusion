#!/bin/bash
# ============================================================================
# plc_fuse_merge_stubs__桩合并.sh — per-app runtime 桩自动合并
# ============================================================================
# 功能: 解析 kernel.ll / .unmapped 中缺失的外部符号，生成 C 桩片段，
#       合并到 test/${FUSE_NAME}_runtime_stubs.c（基于 src/plc_runtime_stubs__POSIX桩.c）
# 输入: manifest.env（须已存在 _kernel.ll）
# 输出: test/${FUSE_NAME}_runtime_stubs.c, _stubs.generated.c
# 用法: bash scripts/plc_fuse_merge_stubs__桩合并.sh manifests/foo.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST"

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
KLL="$WORK/${FUSE_NAME}_kernel.ll"
UNMAPPED="$WORK/${FUSE_NAME}.unmapped"
BASE_STUBS="$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c"
OUT_STUBS="$WORK/${FUSE_NAME}_runtime_stubs.c"
GEN_SECTION="$WORK/${FUSE_NAME}_stubs.generated.c"
MARKER="/* PLCFUSION_GENERATED_STUBS */"

plc_require_file "$KLL" "kernel.ll" \
    "先运行: bash scripts/plc_fuse__内核化主流程.sh $MANIFEST"
plc_require_file "$BASE_STUBS" "基础桩文件" \
    "确认 src/plc_runtime_stubs__POSIX桩.c 存在"

llvm_type_to_c() {
    case "$1" in
        void) echo void ;;
        i1|i8|i16|i32) echo int ;;
        i64) echo "long long" ;;
        ptr*|ptr) echo "void *" ;;
        float) echo float ;;
        double) echo double ;;
        *) echo "void *" ;;
    esac
}

parse_declare_line() {
    local line="$1"
    local sym rettype inner params="" has_variadic=0 i=0 p ct

    sym="$(echo "$line" | sed -n 's/declare[^@]*@\([^ (]*\).*/\1/p')"
    rettype="$(echo "$line" | sed -n 's/declare \([^ ]*\).*/\1/p')"
    echo "$line" | grep -q '\.\.\.' && has_variadic=1

    inner="$(echo "$line" | sed -n 's/declare[^(]*(\(.*\)).*/\1/p')"
    if [ -n "$inner" ] && [ "$inner" != "$line" ]; then
        local old_ifs="$IFS"
        IFS=','
        for p in $inner; do
            p="$(echo "$p" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            [ "$p" = "..." ] && continue
            ct="$(llvm_type_to_c "$(echo "$p" | awk '{print $1}')")"
            case "$sym" in
                printf|fprintf|dprintf|warn|info|fatal|err_msg|err_msg_n|puts)
                    [ "$i" -eq 0 ] && ct="const char *"
                    ;;
            esac
            params="${params:+$params, }${ct} arg${i}"
            i=$((i + 1))
        done
        IFS="$old_ifs"
    fi
    [ "$has_variadic" = "1" ] && params="${params:+$params, }..."

    local cret
    cret="$(llvm_type_to_c "$rettype")"
    printf '%s|%s|%s|%s\n' "$sym" "$cret" "$params" "$has_variadic"
}

gen_stub_body() {
    local sym="$1" cret="$2" params="$3"
    local cast="" ret="return 0;" i

    [ "$cret" = void ] && ret=""
    [ "$cret" = "void *" ] && ret="return NULL;"

    for i in 0 1 2 3 4 5 6 7; do
        echo "$params" | grep -q "arg${i}" && cast="${cast}	(void)arg${i};
"
    done

    case "$sym" in
        *_destroy|hist_destroy|hset_destroy|numa_bitmask_free|numa_free)
            printf 'void %s(%s)\n{\n%b}\n' "$sym" "${params:-void}" "$cast"
            return
            ;;
        sched_yield|pthread_yield)
            printf 'void %s(void)\n{\n}\n' "$sym"
            return
            ;;
        getenv)
            printf 'char *%s(const char *name)\n{\n\t(void)name;\n\treturn NULL;\n}\n' "$sym"
            return
            ;;
        abort|raise)
            printf 'void %s(void)\n{\n\tplc_exit(134);\n}\n' "$sym"
            return
            ;;
        atexit)
            printf 'int %s(void (*fn)(void))\n{\n\t(void)fn;\n\treturn 0;\n}\n' "$sym"
            return
            ;;
    esac

    if [ "$cret" = void ]; then
        printf 'void %s(%s)\n{\n%b}\n' "$sym" "${params:-void}" "$cast"
    else
        printf '%s %s(%s)\n{\n%b	%s\n}\n' "$cret" "$sym" "${params:-void}" "$cast" "$ret"
    fi
}

has_live_call() {
    grep -qE "(call|invoke)[^@]*@${1}[( ]" "$KLL"
}

has_impl() {
    local sym="$1"
    local ko="$PROJECT_ROOT/test/${FUSE_NAME}_kernel.o"
    local modpost="$PROJECT_ROOT/test/${FUSE_NAME}_modpost_stubs.c"
    case "$sym" in
        memcpy|memmove|memset|memcmp|strlen|strcpy|strcat)
            return 0
            ;;
    esac
    hint="$(plc_remap_hint_for_sym "$sym" 2>/dev/null || true)"
    [[ "$hint" == plc_* ]] && grep -qE "@${hint}[( ]" "$KLL" 2>/dev/null && return 0
    grep -qE "^define .* @${sym}\\(" "$KLL" 2>/dev/null && return 0
    if [ -f "$ko" ] && nm "$ko" 2>/dev/null | awk '{print $3}' | grep -qx "$sym"; then
        return 0
    fi
    grep -qE "\\b${sym}\\b" "$BASE_STUBS" 2>/dev/null && return 0
    [ -f "$OUT_STUBS" ] && grep -qE "\\b${sym}\\b" "$OUT_STUBS" 2>/dev/null && return 0
    [ -f "$modpost" ] && grep -qE "\\b${sym}\\b" "$modpost" 2>/dev/null && return 0
    grep -qE "\\b${sym}\\b" "$PROJECT_ROOT/src/plc_runner_official__cyclictest宿主.c" 2>/dev/null && return 0
    [[ "$sym" == plc_* ]] && return 0
    return 1
}

collect_missing() {
    local sym
    if [ -f "$UNMAPPED" ]; then
        sort -u "$UNMAPPED"
    fi
    grep '^declare ' "$KLL" 2>/dev/null | grep -v '@llvm\.' | \
        sed -E 's/declare .* @([^ (]+).*/\1/' | sort -u | while read -r sym; do
        [[ "$sym" == plc_* ]] && continue
        has_live_call "$sym" && echo "$sym"
    done
}

added=0
skipped_parse=0
{
    echo "$MARKER"
    echo "/* manifest: $MANIFEST */"
    echo "#ifdef __KERNEL__"
    echo "#include <linux/module.h>"
    echo "#include <linux/stdarg.h>"
    echo "#endif"
    echo

    while IFS= read -r sym; do
        [ -z "$sym" ] && continue
        has_impl "$sym" && continue
        hint="$(plc_remap_hint_for_sym "$sym" 2>/dev/null || true)"
        if [[ "$hint" == plc_* ]]; then
            skipped_parse=$((skipped_parse + 1))
            continue
        fi

        line="$(grep -F "declare" "$KLL" | grep -F "@${sym}(" | head -1 || true)"
        if [ -z "$line" ]; then
            skipped_parse=$((skipped_parse + 1))
            continue
        fi

        parsed="$(parse_declare_line "$line")"
        psym="$(echo "$parsed" | cut -d'|' -f1)"
        pcret="$(echo "$parsed" | cut -d'|' -f2)"
        pparams="$(echo "$parsed" | cut -d'|' -f3)"

        echo "/* auto: ${psym} */"
        gen_stub_body "$psym" "$pcret" "$pparams"
        echo
        added=$((added + 1))
    done < <(collect_missing | sort -u)
} > "$GEN_SECTION"

if ! cp "$BASE_STUBS" "$OUT_STUBS"; then
    plc_die "$PLC_E_BUILD" "无法复制基础桩到 $OUT_STUBS" "检查 test/ 写权限"
fi

if [ "$added" -gt 0 ]; then
    echo "" >> "$OUT_STUBS"
    cat "$GEN_SECTION" >> "$OUT_STUBS"
    echo "✅ merged $added stub(s) -> $OUT_STUBS"
else
    echo "✅ no new stubs; $OUT_STUBS (= base copy)"
    rm -f "$GEN_SECTION"
fi

if [ "$skipped_parse" -gt 0 ]; then
    plc_warn "${skipped_parse} 个符号无法在 kernel.ll 解析 declare 签名" \
        "手动添加到 src/plc_runtime_stubs__POSIX桩.c 或扩展 Pass remap"
fi
