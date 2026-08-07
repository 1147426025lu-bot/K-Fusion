#!/bin/bash
# ============================================================================
# run_pass_unit_tests__Pass单元测试.sh — Pass 回归（FileCheck 或 grep 回退）
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

echo "=== K-Fusion Pass 单元测试 ==="
echo "    pass=$PASS_SO"
echo "    opt=$OPT"
echo "    filecheck=$([ "$USE_FILECHECK" = 1 ] && echo "$FILECHECK" || echo 'grep-fallback')"

run_filecheck() {
    local ll="$1"
    local name
    name="$(basename "$ll")"
    echo "   ▶ $name (FileCheck)"
    if ! "$OPT" -load-pass-plugin "$PASS_SO" -passes=plc-fusion-remap "$ll" -S \
        | "$FILECHECK" "$ll"; then
        echo "❌ FileCheck 失败: $name" >&2
        return 1
    fi
}

run_grep_fallback() {
    local ll="$1"
    local name out
    name="$(basename "$ll")"
    echo "   ▶ $name (grep fallback — 安装 llvm-*-tools 可启用 FileCheck)"
    out="$("$OPT" -load-pass-plugin "$PASS_SO" -passes=plc-fusion-remap "$ll" -S)"
    if echo "$out" | grep -E 'call .*@printf' >/dev/null; then
        echo "❌ 仍有 call @printf: $name" >&2
        return 1
    fi
    if ! echo "$out" | grep -E 'call .*@plc_printk' >/dev/null; then
        echo "❌ 未找到 call @plc_printk: $name" >&2
        return 1
    fi
}

FAIL=0
for ll in "$SCRIPT_DIR"/*.ll; do
    [ -f "$ll" ] || continue
    if [ "$USE_FILECHECK" = 1 ]; then
        run_filecheck "$ll" || FAIL=1
    else
        run_grep_fallback "$ll" || FAIL=1
    fi
done

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
echo "✅ Pass 单元测试通过 (${SCRIPT_DIR}/*.ll)"
