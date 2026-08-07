#!/bin/bash
# ============================================================================
# run_fuse_symlink_check__fuse符号链接门禁.sh — scripts/fuse 顶层 symlink 完整性
# ============================================================================
# 用法: bash scripts/run_fuse_symlink_check__fuse符号链接门禁.sh
# 说明: plc_fuse 等经 scripts/ 调用时 SCRIPT_DIR=scripts；fuse/ 下每个可执行脚本
#       须有 scripts/<name> -> fuse/<name> 符号链接。
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"

FUSE_DIR="$SCRIPT_DIR/fuse"
MISSING=()
WRONG=()

for f in "$FUSE_DIR"/*; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    case "$base" in
        README.md|*.md) continue ;;
    esac
    top="$SCRIPT_DIR/$base"
    want="fuse/$base"
    if [ ! -e "$top" ]; then
        MISSING+=("$base")
        continue
    fi
    if [ ! -L "$top" ]; then
        WRONG+=("$base (not a symlink)")
        continue
    fi
    target="$(readlink "$top")"
    if [ "$target" != "$want" ]; then
        WRONG+=("$base (-> $target, want $want)")
    fi
done

if [ "${#MISSING[@]}" -gt 0 ] || [ "${#WRONG[@]}" -gt 0 ]; then
    echo "=== fuse 符号链接门禁 FAIL ==="
    for m in "${MISSING[@]}"; do
        echo "    MISSING: scripts/$m -> fuse/$m"
        echo "    修复: ln -sf fuse/$m $SCRIPT_DIR/$m"
    done
    for w in "${WRONG[@]}"; do
        echo "    WRONG: scripts/$w"
    done
    plc_die "$PLC_E_NOFILE" "fuse 顶层 symlink 不完整" \
        "每个 scripts/fuse/* 脚本（除 README）需 ln -sf fuse/<name> scripts/<name>"
fi

echo "✅ fuse 符号链接门禁通过 ($(find "$FUSE_DIR" -maxdepth 1 -type f ! -name 'README.md' | wc -l | tr -d ' ') 项)"
