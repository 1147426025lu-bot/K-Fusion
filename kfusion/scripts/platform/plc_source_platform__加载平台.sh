#!/bin/bash
# ============================================================================
# plc_source_platform__加载平台.sh — 解析 PLC_PLATFORM 并导出 LLC/隔离参数
# ============================================================================
# 用法: source scripts/platform/plc_source_platform__加载平台.sh
# 环境:
#   PLC_PLATFORM=rpi5|x86_64   默认: aarch64→rpi5, x86_64→x86_64
#   PLC_PLATFORM_AUTO=0        关闭自动探测
# ============================================================================
[[ -n "${_PLC_PLATFORM_LOADED:-}" ]] && return 0 2>/dev/null || true
_PLC_PLATFORM_LOADED=1

_plc_platform_root() {
    if [ -n "${_PLC_PLATFORM_ROOT:-}" ]; then
        echo "$_PLC_PLATFORM_ROOT"
        return
    fi
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    _PLC_PLATFORM_ROOT="$(cd "$here/../.." && pwd)"
    echo "$_PLC_PLATFORM_ROOT"
}

plc_detect_platform_id() {
    if [ -n "${PLC_PLATFORM:-}" ]; then
        echo "$PLC_PLATFORM"
        return
    fi
    if [ "${PLC_PLATFORM_AUTO:-1}" = "0" ]; then
        echo "rpi5"
        return
    fi
    local m
    m="$(uname -m 2>/dev/null || echo unknown)"
    case "$m" in
        x86_64|amd64) echo "x86_64" ;;
        aarch64|arm64) echo "rpi5" ;;
        *) echo "generic" ;;
    esac
}

plc_source_platform() {
    local root id file
    root="$(_plc_platform_root)"
    id="$(plc_detect_platform_id)"
    export PLC_PLATFORM="$id"

    file="$root/manifests/platform/${id}.env"
    if [ ! -f "$file" ]; then
        file="$root/manifests/platform/generic.env"
    fi
    if [ ! -f "$file" ]; then
        echo "⚠️  未找到平台配置 manifests/platform/${id}.env" >&2
        export PLATFORM_ID="$id"
        return 1
    fi
    # shellcheck source=/dev/null
    source "$file"
    export PLATFORM_ID="${PLATFORM_ID:-$id}"
    export PLATFORM_ARCH="${PLATFORM_ARCH:-$(uname -m)}"
    return 0
}

plc_source_platform || true
