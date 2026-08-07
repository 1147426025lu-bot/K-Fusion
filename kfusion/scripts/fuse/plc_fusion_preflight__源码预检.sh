#!/bin/bash
# ============================================================================
# plc_fusion_preflight__源码预检.sh — 融合前源码可行性预检
# ============================================================================
# 功能: 扫描 manifest 源文件，标记 C++/动态加载/网络/fork 等高风险模式
# 输入: manifest.env
# 输出: test/${FUSE_NAME}.preflight.log；终端摘要
# 用法: bash scripts/plc_fusion_preflight__源码预检.sh manifests/foo.env
# 环境: FUSE_PREFLIGHT_STRICT=1 时遇 critical 问题 exit 1
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

if [ -n "${FUSE_SRC_ROOT:-}" ]; then
    [[ "$FUSE_SRC_ROOT" = /* ]] && SRC_ROOT="$FUSE_SRC_ROOT" || SRC_ROOT="$PROJECT_ROOT/$FUSE_SRC_ROOT"
else
    SRC_ROOT="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
fi
if [ -n "${FUSE_GIT_DIR:-}" ]; then
    SRC_ROOT="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}/$FUSE_GIT_DIR"
fi

SOURCE_PATHS=("$SRC_ROOT/$FUSE_SOURCE")
for rel in ${FUSE_EXTRA_SOURCES:-}; do
    SOURCE_PATHS+=("$SRC_ROOT/$rel")
done

OUT="$PROJECT_ROOT/test/${FUSE_NAME}.preflight.log"
WARN=0
CRIT=0

check_pattern() {
    local level="$1" label="$2" pattern="$3" file="$4"
    if grep -qE "$pattern" "$file" 2>/dev/null; then
        echo "[$level] $label in $file" >> "$OUT"
        if [ "$level" = CRIT ]; then
            CRIT=$((CRIT + 1))
        else
            WARN=$((WARN + 1))
        fi
    fi
}

plc_ensure_dir "$(dirname "$OUT")"
: > "$OUT"
echo "# preflight: $MANIFEST" >> "$OUT"
echo "# scanned: $(date -Iseconds)" >> "$OUT"

echo "=== 源码预检: ${FUSE_NAME} ==="

if [ "${FUSE_AST_PREFLIGHT:-1}" = "1" ]; then
    bash "$SCRIPT_DIR/plc_fusion_ast_preflight__AST融合预检.sh" "$MANIFEST" || {
        if [ "${FUSE_PREFLIGHT_STRICT:-0}" = "1" ] || [ "${FUSE_AST_PREFLIGHT_STRICT:-1}" = "1" ]; then
            exit 1
        fi
    }
fi

for src in "${SOURCE_PATHS[@]}"; do
    if [ ! -f "$src" ]; then
        echo "[CRIT] 源文件不存在: $src" >> "$OUT"
        CRIT=$((CRIT + 1))
        continue
    fi
    echo "    scan $(basename "$src")"
    case "$src" in
        *.cpp|*.cc|*.cxx|*.C)
            if [ "${FUSE_ALLOW_CXX:-1}" = "1" ]; then
                check_pattern WARN "C++ keyword (AST 复核)" \
                    '\b(class|namespace|template|operator new|operator delete)\b' "$src"
            else
                echo "[CRIT] C++ 源文件: $src" >> "$OUT"
                CRIT=$((CRIT + 1))
            fi
            ;;
    esac
    if [[ "$src" != *.cpp && "$src" != *.cc && "$src" != *.cxx && "$src" != *.C ]]; then
        check_pattern CRIT "C++ keyword" '\b(class|namespace|template|operator new|operator delete)\b' "$src"
    fi
    check_pattern CRIT "iostream" '#include[[:space:]]*<(iostream|ostream|istream|fstream)>' "$src"
    check_pattern CRIT "dynamic load" '\b(dlopen|dlsym|LoadLibrary)\b' "$src"
    check_pattern CRIT "fork/exec" '\b(fork|vfork|execve|execvp|system)\(' "$src"
    check_pattern WARN "BSD socket" '\b(socket|connect|bind|listen|accept|sendto|recvfrom)\(' "$src"
    check_pattern WARN "CUDA/OpenCL" '\b(cuda|clCreate|clEnqueue)\b' "$src"
    check_pattern WARN "userspace-only API" '\b(pthread_cancel|sem_open|mq_open|inotify_init)\b' "$src"
    check_pattern WARN "file IO heavy" '\b(fopen|fread|fwrite|fscanf|fgets)\(' "$src"
    check_pattern INFO "float/double IR" '\b(double|float|long double)\b' "$src"
done

echo >> "$OUT"
echo "summary: critical=$CRIT warnings=$WARN" >> "$OUT"

echo "    critical=$CRIT warnings=$WARN → $OUT"
if [ "$CRIT" -gt 0 ]; then
    plc_warn "${CRIT} 个 critical 问题（见 $OUT）" \
        "C++/dlopen/fork 通常无法直接内核化" \
        "可设 FUSE_PREFLIGHT=0 跳过预检"
    if [ "${FUSE_PREFLIGHT_STRICT:-0}" = "1" ]; then
        exit 1
    fi
elif [ "$WARN" -gt 0 ]; then
    plc_warn "${WARN} 个 warning（见 $OUT）" \
        "Pass 可能黑洞或生成 noop 桩，需人工验证行为"
else
    echo "✅ 预检通过（无 critical/warning）"
fi
