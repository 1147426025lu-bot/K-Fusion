#!/bin/bash
# ============================================================================
# plc_fusion_modpost_fix__ko链接修复.sh — modpost 未解析符号自动桩
# ============================================================================
# 功能: 解析 Kbuild 日志中的 undefined symbol，追加 C 桩到 modpost_stubs.c
# 输入: manifest.env, kbuild.log
# 输出: test/${FUSE_NAME}_modpost_stubs.c；stdout 新增桩数量
# 用法: bash scripts/plc_fusion_modpost_fix__ko链接修复.sh manifests/foo.env test/kbuild.log
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
KBUILD_LOG="${2:-}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

if [ -z "$KBUILD_LOG" ] || [ ! -f "$KBUILD_LOG" ]; then
    plc_die "$PLC_E_NOFILE" "缺少 Kbuild 日志" \
        "用法: $0 manifests/foo.env test/kbuild.log"
fi

KLL="$PROJECT_ROOT/test/${FUSE_NAME}_kernel.ll"
OUT="$PROJECT_ROOT/test/${FUSE_NAME}_modpost_stubs.c"
BASE_STUBS="$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c"
APP_STUBS="$PROJECT_ROOT/test/${FUSE_NAME}_runtime_stubs.c"
KERNEL_O="$PROJECT_ROOT/test/${FUSE_NAME}_kernel.o"
MARKER="/* PLCFUSION_MODPOST_STUBS */"
NEED_COMPILER_RT=0

sym_in_file() {
    local sym="$1" file="$2"
    [ -f "$file" ] && grep -qE "\\b${sym}\\b" "$file"
}

sym_defined() {
    local sym="$1"
    plc_is_kernel_libc_sym "$sym" && return 0
    sym_in_file "$sym" "$BASE_STUBS" && return 0
    sym_in_file "$sym" "$APP_STUBS" && return 0
    sym_in_file "$sym" "$OUT" && return 0
    if [ -f "$KERNEL_O" ] && nm "$KERNEL_O" 2>/dev/null | awk '{print $3}' | grep -qx "$sym"; then
        return 0
    fi
    if [ -f "$KLL" ] && grep -qE "^define .* @${sym}\\(" "$KLL" 2>/dev/null; then
        return 0
    fi
    [[ "$sym" == plc_* ]] && return 0
    return 1
}

gen_modpost_stub() {
    local sym="$1"
    local hint
    hint="$(plc_remap_hint_for_sym "$sym" 2>/dev/null || true)"
    case "$hint" in
        stub:noop)
            printf 'void %s(void)\n{\n}\n\n' "$sym"
            return
            ;;
        stub:null)
            printf 'void *%s(const char *name)\n{\n\t(void)name;\n\treturn NULL;\n}\n\n' "$sym"
            return
            ;;
        plc_*)
            plc_warn "modpost 缺 ${sym}，建议 Pass remap → ${hint} 后重跑 plc_fuse"
            ;;
    esac
    case "$sym" in
        *_destroy|hist_destroy|hset_destroy)
            printf 'void %s(void *p)\n{\n\t(void)p;\n}\n\n' "$sym"
            ;;
        sched_yield)
            printf 'int %s(void)\n{\n\tyield();\n\treturn 0;\n}\n\n' "$sym"
            ;;
        abort|raise)
            printf 'void %s(void)\n{\n\tplc_exit(134);\n}\n\n' "$sym"
            ;;
        *)
            printf 'int %s(void)\n{\n\treturn 0;\n}\n\n' "$sym"
            ;;
    esac
}

syms="$(plc_parse_modpost_undefined "$KBUILD_LOG" || true)"
if [ -z "$syms" ]; then
    plc_die "$PLC_E_BUILD" "日志中无 modpost undefined symbol" \
        "确认 $KBUILD_LOG 含 'undefined symbol:' 行"
fi

added=0
NEW_BLOCK=""
while IFS= read -r sym; do
    [ -z "$sym" ] && continue
    if plc_is_compiler_rt_sym "$sym"; then
        plc_die "$PLC_E_BUILD" "kernel.o 仍引用软浮点符号: $sym" \
            "应启用 Q 定点 Pass（FUSE_FIXED_POINT=1）" \
            "勿再使用已移除的 compiler-rt 桩"
    fi
    sym_defined "$sym" && continue
    hint="$(plc_remap_hint_for_sym "$sym" 2>/dev/null || true)"
    [[ "$hint" == plc_* ]] && continue
    NEW_BLOCK="${NEW_BLOCK}/* modpost: ${sym} */
$(gen_modpost_stub "$sym")"
    added=$((added + 1))
done <<< "$syms"

if [ "$added" -eq 0 ] && [ "$NEED_COMPILER_RT" = "0" ]; then
    plc_warn "无新 modpost 桩可生成（符号已在 runtime/kernel 中或需 Pass remap）" \
        "见: bash scripts/plc_fusion_remap_hints__映射建议.sh $MANIFEST"
    exit 1
fi

if [ ! -f "$OUT" ]; then
    cat > "$OUT" <<EOF
${MARKER}
/* manifest: $MANIFEST — auto-generated for Kbuild link */
#ifdef __KERNEL__
#include <linux/module.h>
#include <linux/sched.h>
#include "../include/plc_abi__运行时ABI.h"
#endif

EOF
fi

if [ "$added" -gt 0 ]; then
    printf '%s' "$NEW_BLOCK" >> "$OUT"
    echo "✅ modpost fix: +${added} stub(s) → $OUT"
else
    echo "✅ modpost fix: 0 new stubs (compiler-rt=${NEED_COMPILER_RT})"
fi

if [ "$NEED_COMPILER_RT" = "1" ]; then
    echo "NEED_COMPILER_RT=1"
fi
echo "ADDED=${added}"
