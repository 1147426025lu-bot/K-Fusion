#!/bin/bash
# ============================================================================
# run_deploy_symlink_check__deploy符号链接门禁.sh — deploy/profiles 顶层 symlink
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"

DEPLOY="$SCRIPT_DIR/deploy"
PROFILES="$DEPLOY/profiles"
MISSING=()
WRONG=()

for f in "$PROFILES"/*; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    case "$base" in
        README.md|*.md) continue ;;
    esac
    top="$DEPLOY/$base"
    want="profiles/$base"
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
    echo "=== deploy profile 符号链接门禁 FAIL ==="
    for m in "${MISSING[@]}"; do
        echo "    MISSING: scripts/deploy/$m -> profiles/$m"
    done
    for w in "${WRONG[@]}"; do
        echo "    WRONG: scripts/deploy/$w"
    done
    plc_die "$PLC_E_NOFILE" "deploy profile symlink 不完整"
fi

echo "✅ deploy profile 符号链接门禁通过"
