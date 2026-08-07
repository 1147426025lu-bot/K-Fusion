#!/bin/bash
# Run Pi5 enable with local dmesg capture (works if SSH survives soft hang).
# For hard freeze, PC netconsole_capture is mandatory — this file is a backup.
#
# Usage:
#   JAILHOUSE_ENABLE_OK=yes bash crtos/scripts/run_enable_with_capture__enable并抓日志.sh full_1cpu_debug
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOGDIR="${JH_LOGDIR:-$REPO_ROOT/crtos/logs}"
STEP="${1:-full_1cpu_debug}"

mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/enable-${STEP}-${STAMP}.log"
META="$LOGDIR/enable-${STEP}-${STAMP}.meta"

{
	echo "=== enable capture start $(date -Iseconds) step=$STEP ==="
	echo "ko=$(stat -c '%y' "$REPO_ROOT/crtos/upstream/jailhouse/driver/jailhouse.ko" 2>/dev/null | cut -d. -f1)"
	echo "bin=$(stat -c '%y' "$REPO_ROOT/crtos/upstream/jailhouse/hypervisor/jailhouse.bin" 2>/dev/null | cut -d. -f1)"
	echo "uname=$(uname -a)"
} | tee "$META"

# Follow kernel ring buffer into log (stops updating on hard hang; flush on exit).
# NOTE: never run `dmesg -w` in an `if` test — it blocks forever waiting for new lines.
DMESG_PID=""
if dmesg --help 2>&1 | grep -qE '\[-w\]|--follow'; then
	dmesg -w >>"$LOG" 2>&1 &
	DMESG_PID=$!
	echo "[$(date -Iseconds)] dmesg -w follower pid=$DMESG_PID -> $LOG" | tee -a "$META"
else
	dmesg >>"$LOG" 2>&1 || true
	echo "[$(date -Iseconds)] dmesg snapshot (no -w on this system)" | tee -a "$META"
fi

cleanup() {
	local rc=$?
	{
		echo "=== capture end $(date -Iseconds) exit=$rc ==="
		if [ -e /sys/module/jailhouse/parameters/scratch_dump ] 2>/dev/null; then
			echo 1 | sudo tee /sys/module/jailhouse/parameters/scratch_dump >/dev/null 2>&1 || true
		fi
		sudo dmesg 2>/dev/null | grep -E 'jailhouse:|scratch|Oops|Internal error' | tail -40 || true
	} >>"$LOG" 2>&1
	[ -n "$DMESG_PID" ] && kill "$DMESG_PID" 2>/dev/null || true
	sync
	echo "[$(date -Iseconds)] saved: $LOG"
	echo "[$(date -Iseconds)] meta:  $META"
	exit "$rc"
}
trap cleanup EXIT INT TERM

export JH_LOGDIR="$LOGDIR"
echo "[$(date -Iseconds)] starting enable step=$STEP ..." | tee -a "$META"
# Line-buffered so progress appears immediately (pipe to tee is block-buffered by default)
stdbuf -oL -eL env JAILHOUSE_ENABLE_OK=yes bash "$SCRIPT_DIR/test_pi5_enable_steps__分步enable测试.sh" "$STEP" 2>&1 \
	| stdbuf -oL tee -a "$LOG"
