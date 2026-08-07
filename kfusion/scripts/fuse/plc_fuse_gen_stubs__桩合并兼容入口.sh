#!/bin/bash
# ============================================================================
# plc_fuse_gen_stubs__桩合并兼容入口.sh — 桩生成兼容入口（转发 merge_stubs）
# ============================================================================
# 功能: 兼容旧脚本名，实际调用 plc_fuse_merge_stubs__桩合并.sh
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$SCRIPT_DIR/plc_fuse_merge_stubs__桩合并.sh" ]; then
    echo "❌ 未找到 plc_fuse_merge_stubs__桩合并.sh" >&2
    echo "   💡 确认在 PLCFusion 项目 scripts/ 目录下执行" >&2
    exit 3
fi
exec "$SCRIPT_DIR/plc_fuse_merge_stubs__桩合并.sh" "$@"
