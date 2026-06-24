#!/bin/bash
# ============================================================================
# run_stress_cycletest__加压长测.sh — 背景 hackbench + fused cyclictest 加压测
# ============================================================================
# 测量: stress — CPU0-2 hackbench 加压，CPU3 隔离测最坏延迟
# 用法:
#   bash scripts/deploy/run_stress_cycletest__加压长测.sh
#   DURATION_MIN=15 bash scripts/deploy/run_stress_cycletest__加压长测.sh
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export PLC_PROFILE="${PLC_PROFILE:-$SCRIPT_DIR/profiles/profile_stress_l2__背景加压.env.sh}"
export MEASURE_KIND=stress

exec bash "$SCRIPT_DIR/run_soak_cycletest__浸泡长测.sh" "$@"
