#!/bin/bash
# 8 小时安静浸泡测 — L2 soak profile
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRJ="$(cd "$SCRIPT_DIR/../.." && pwd)"

export PRJ
export PLC_PROFILE="${PLC_PROFILE:-$SCRIPT_DIR/profiles/profile_soak_l2_best__安静浸泡.env.sh}"
export MEASURE_KIND=soak
export DURATION_MIN="${DURATION_MIN:-480}"
export MAX_UPTIME_SEC="${MAX_UPTIME_SEC:-0}"
export RUNNER_PROFILE="${RUNNER_PROFILE:-fused_soak_l2}"
export EXPORT_DECIM_MAX="${EXPORT_DECIM_MAX:-576000}"
export TARGET_ABS_MAX_NS="${TARGET_ABS_MAX_NS:-5000}"
export RING_EXPORT_PATH="${RING_EXPORT_PATH:-$PRJ/results/jitter_8h_soak.bin}"

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$PRJ/results/soak/stability/soak_8h_${STAMP}.log"
MONITOR_NOTE="$PRJ/results/soak/stability/soak_8h_${STAMP}.monitor.txt"
mkdir -p "$PRJ/results/soak/stability"

cat > "$MONITOR_NOTE" <<EOF
# 8h 安静浸泡测监控说明
主日志: $LOG
watchdog: results/raw/*_480min_soak.watchdog.log
cpu3:     results/raw/*_480min_soak.cpu3_monitor.log
出图:     results/png/*_480min_soak.png
EOF

echo "=== soak 8h | profile=$PLC_PROFILE | DURATION_MIN=$DURATION_MIN ===" | tee "$LOG"
echo "监控说明: $MONITOR_NOTE" | tee -a "$LOG"
echo "started: $(date -Iseconds)" | tee -a "$LOG"

cd "$SCRIPT_DIR"
exec sudo -E env \
    PATH="${PATH:-/usr/local/llvm-17/bin:/usr/bin:/bin}" \
    PRJ="$PRJ" PLC_PROFILE="$PLC_PROFILE" MEASURE_KIND="$MEASURE_KIND" \
    DURATION_MIN="$DURATION_MIN" MAX_UPTIME_SEC="$MAX_UPTIME_SEC" \
    RUNNER_PROFILE="$RUNNER_PROFILE" EXPORT_DECIM_MAX="$EXPORT_DECIM_MAX" \
    TARGET_ABS_MAX_NS="$TARGET_ABS_MAX_NS" RING_EXPORT_PATH="$RING_EXPORT_PATH" \
    bash run_soak_cycletest__浸泡长测.sh 2>&1 | tee -a "$LOG"
