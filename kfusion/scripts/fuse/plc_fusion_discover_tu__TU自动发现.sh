#!/bin/bash
# ============================================================================
# plc_fusion_discover_tu__TU自动发现.sh — 从主源 #include 推断额外翻译单元
# ============================================================================
# 功能: 扫描 FUSE_SOURCE 的 #include "local.h"，在同目录/子目录查找同名 .c
# 输出: 空格分隔的相对路径（相对 SRC_ROOT），写入 stdout
# 用法: bash scripts/plc_fusion_discover_tu__TU自动发现.sh manifests/foo.env
# 环境: FUSE_AUTO_DISCOVER_TU=1（plc_fuse 默认开启，已有 FUSE_EXTRA_SOURCES 时跳过）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_source_manifest "$MANIFEST" "FUSE_NAME" "FUSE_SOURCE"

if [ -n "${FUSE_SRC_ROOT:-}" ]; then
    [[ "$FUSE_SRC_ROOT" = /* ]] && SRC_ROOT="$FUSE_SRC_ROOT" || SRC_ROOT="$PROJECT_ROOT/$FUSE_SRC_ROOT"
else
    SRC_ROOT="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
fi
if [ -n "${FUSE_GIT_DIR:-}" ]; then
    SRC_ROOT="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}/$FUSE_GIT_DIR"
fi

PRIMARY="$SRC_ROOT/$FUSE_SOURCE"
DISCOVERED=()
LOG="$PROJECT_ROOT/test/${FUSE_NAME}.discover_tu.log"

if [ ! -f "$PRIMARY" ]; then
    exit 0
fi

search_dirs() {
    local d="$1"
    local base="$2"
    local cand

    for cand in "$d/${base}.c" "$d/src/${base}.c" "$d/lib/${base}.c"; do
        if [ -f "$cand" ]; then
            echo "$cand"
            return 0
        fi
    done
    return 1
}

{
    echo "# TU discover $(date -Iseconds)"
    echo "primary=$PRIMARY"
} > "$LOG"

while IFS= read -r inc; do
    [[ "$inc" != *.h ]] && continue
    base="${inc%.h}"
    base="$(basename "$base")"
    found=""
    for dir in "$(dirname "$PRIMARY")" "$SRC_ROOT/src/lib" "$SRC_ROOT/src" "$SRC_ROOT"; do
        [ -d "$dir" ] || continue
        if found="$(search_dirs "$dir" "$base")"; then
            rel="${found#"$SRC_ROOT"/}"
            case " ${DISCOVERED[*]} " in
                *" $rel "*) ;;
                *)
                    if [ "$rel" != "$FUSE_SOURCE" ]; then
                        DISCOVERED+=("$rel")
                        echo "found: $rel (from #include \"$inc\")" >> "$LOG"
                    fi
                    ;;
            esac
            break
        fi
    done
done < <(grep -E '^[[:space:]]*#include[[:space:]]+"[^"]+"' "$PRIMARY" \
    | sed -E 's/.*"([^"]+)".*/\1/')

# rt-tests 常见配对：cyclictest + histogram
if [[ "$FUSE_SOURCE" == *cyclictest/cyclictest.c ]] && [ -f "$SRC_ROOT/src/lib/histogram.c" ]; then
    rel="src/lib/histogram.c"
    case " ${DISCOVERED[*]} " in *" $rel "*) ;; *)
        DISCOVERED+=("$rel")
        echo "found: $rel (rt-tests pairing)" >> "$LOG"
        ;;
    esac
fi

if [ "${#DISCOVERED[@]}" -gt 0 ]; then
    echo "${DISCOVERED[*]}"
fi
