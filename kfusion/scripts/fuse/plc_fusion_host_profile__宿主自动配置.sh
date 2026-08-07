#!/bin/bash
# ============================================================================
# plc_fusion_host_profile__宿主自动配置.sh — 从 pre.ll 推断通用宿主组合
# ============================================================================
# 输出: test/${FUSE_NAME}.host_profile.env
#   FUSE_DETECT_NEED_HRTIMER / FUSE_DETECT_NEED_PTHREAD / FUSE_DETECT_NEED_SIGNAL
#   FUSE_DETECT_NEED_SEM / FUSE_DETECT_NEED_FILEIO / FUSE_DETECT_NEED_BARRIER
# 用法: source 于 ignite_fused；或 bash ... manifests/foo.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
PRE_LL="${2:-}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

PRE_LL="${PRE_LL:-$PROJECT_ROOT/test/${FUSE_NAME}_pre.ll}"
OUT="$PROJECT_ROOT/test/${FUSE_NAME}.host_profile.env"

plc_require_file "$PRE_LL" "pre.ll" \
    "先: bash scripts/plc_fuse__内核化主流程.sh $MANIFEST"

need_hrtimer=0
need_pthread=0
need_signal=0
need_sem=0
need_fileio=0
need_barrier=0

if grep -qE '@(nanosleep|clock_nanosleep|timer_create|timer_settime|timer_delete|usleep|sleep)\(' "$PRE_LL" 2>/dev/null; then
    need_hrtimer=1
fi
if grep -qE '@pthread_(create|join|mutex_|cond_|barrier_)' "$PRE_LL" 2>/dev/null; then
    need_pthread=1
fi
if grep -qE '@(signal|sigaction|sigprocmask|sigwait)\(' "$PRE_LL" 2>/dev/null; then
    need_signal=1
fi
if grep -qE '@sem_(init|wait|post|destroy|timedwait)\(' "$PRE_LL" 2>/dev/null; then
    need_sem=1
fi
if grep -qE '@(fopen|fread|fputs|fscanf|open|mmap|shm_open)\(' "$PRE_LL" 2>/dev/null; then
    need_fileio=1
fi
if grep -qE '@pthread_barrier_(init|wait)\(' "$PRE_LL" 2>/dev/null; then
    need_barrier=1
fi

{
    echo "# PLCFusion host profile — $(date -Iseconds)"
    echo "# pre=$PRE_LL"
    echo "FUSE_DETECT_NEED_HRTIMER=$need_hrtimer"
    echo "FUSE_DETECT_NEED_PTHREAD=$need_pthread"
    echo "FUSE_DETECT_NEED_SIGNAL=$need_signal"
    echo "FUSE_DETECT_NEED_SEM=$need_sem"
    echo "FUSE_DETECT_NEED_FILEIO=$need_fileio"
    echo "FUSE_DETECT_NEED_BARRIER=$need_barrier"
} > "$OUT"

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    echo "=== host profile: ${FUSE_NAME} ==="
    echo "    hrtimer=$need_hrtimer pthread=$need_pthread signal=$need_signal"
    echo "    sem=$need_sem fileio=$need_fileio barrier=$need_barrier"
    echo "    -> $OUT"
fi
