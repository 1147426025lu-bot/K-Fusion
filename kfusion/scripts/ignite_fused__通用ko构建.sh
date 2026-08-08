#!/bin/bash
# ============================================================================
# ignite_fused__通用ko构建.sh — 通用 fused 内核模块 (.ko) 构建
# ============================================================================
# 推荐入口: bash scripts/plc_kernelize__内核化.sh <manifest>
# 本脚本等价于: PLC_KERNELIZE_STAGE=ko FUSE_RUNNER_PROFILE=generic $0
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
# shellcheck source=lib/plc_ignite__ko构建公共.sh
source "$SCRIPT_DIR/lib/plc_ignite__ko构建公共.sh"
plc_enable_err_trap

MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
PROJECT_ROOT="$(plc_project_root)"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
export PLC_FUSE_MANIFEST="$MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST"

FUSE_RUN_MAIN="${FUSE_RUN_MAIN:-0}"
FUSE_LINK_RUNTIME_STUBS="${FUSE_LINK_RUNTIME_STUBS:-1}"
FUSE_LINK_PTHREAD_HOST="${FUSE_LINK_PTHREAD_HOST:-1}"
FUSE_HOST="${FUSE_HOST:-generic}"
FUSE_AUTO_DETECT="${FUSE_AUTO_DETECT:-1}"
FORCE_REBUILD_KERNEL_O="${FORCE_REBUILD_KERNEL_O:-1}"

plc_ignite_build_generic "$MANIFEST"
