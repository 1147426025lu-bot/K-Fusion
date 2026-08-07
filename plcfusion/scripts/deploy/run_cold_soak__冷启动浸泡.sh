#!/bin/bash
# ============================================================================
# run_cold_soak__冷启动浸泡.sh — 冷启动后跑 L2 soak（逼近 2802ns 基线）
# ============================================================================
# 用法:
#   bash scripts/deploy/run_cold_soak__冷启动浸泡.sh schedule   # 120s 后开跑，配合 reboot
#   bash scripts/deploy/run_cold_soak__冷启动浸泡.sh now         # 立即跑（uptime 应 < 8h）
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/repo_paths__仓库路径.sh"
PROJECT_ROOT="$PLCFUSION_ROOT"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$RESULTS_ROOT/soak/coldboot_${STAMP}.log"
ACTION="${1:-now}"
WARMUP_SEC="${COLD_SOAK_WARMUP_SEC:-120}"

run_soak() {
    cd "$PROJECT_ROOT"
    sudo -n true 2>/dev/null || { echo "❌ 需要 sudo -v"; exit 1; }
    UPTIME=$(awk '{print int($1)}' /proc/uptime)
    echo "=== coldboot soak uptime=${UPTIME}s log=$LOG ===" | tee "$LOG"
    exec env DURATION_MIN="${DURATION_MIN:-15}" bash scripts/deploy/run_soak_cycletest__浸泡长测.sh >>"$LOG" 2>&1
}

case "$ACTION" in
    schedule)
        (
            sleep "$WARMUP_SEC"
            run_soak
        ) &
        echo "✅ ${WARMUP_SEC}s 后开跑 → $LOG (pid=$!)"
        ;;
    now)
        run_soak
        ;;
    *)
        echo "用法: $0 {schedule|now}"
        exit 2
        ;;
esac
