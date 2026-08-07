#!/bin/bash
# el2_stop=1 — memremap + firmware + cell_prepare, skip make_exec/EL2, exit 0.
# Requires clean module state (power-cycle if prior Oops left jailhouse stuck).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
CELL="${CELL:-$JH/configs/arm64/rpi5-minimal.cell}"
LOG_DIR="$REPO_ROOT/compare/crtos"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/el2_stop_${STAMP}.log"

mkdir -p "$LOG_DIR"

{
	echo "=== el2_stop enable $(date -Iseconds) ==="
	uname -a
	echo "cell=$CELL"

	if lsmod | grep -q '^jailhouse'; then
		echo "WARN: jailhouse already loaded — if rmmod fails, power-cycle first"
	fi

	bash "$SCRIPT_DIR/rebuild_jailhouse_ko__重编驱动.sh"

	sudo rmmod jailhouse 2>/dev/null || {
		echo "ERROR: rmmod jailhouse failed (module in use). Power-cycle Pi and retry."
		exit 1
	}

	sudo insmod "$JH/driver/jailhouse.ko" el2_stop=1
	echo "el2_stop=$(cat /sys/module/jailhouse/parameters/el2_stop 2>/dev/null || echo '?')"

	set +e
	sudo "$JH/tools/jailhouse" enable "$CELL"
	RC=$?
	set -e
	echo "enable exit=$RC"

	sudo dmesg | grep -i jailhouse | tail -20

	sudo rmmod jailhouse 2>/dev/null || echo "WARN: rmmod failed after test"
	if [ "$RC" -eq 0 ]; then
		echo "✅ el2_stop OK — memremap + config path verified"
	else
		echo "❌ el2_stop failed (exit=$RC)"
	fi
	echo "Log: $LOG"
	exit "$RC"
} 2>&1 | tee "$LOG"
