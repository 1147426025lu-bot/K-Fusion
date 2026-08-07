#!/bin/bash
# Safe pre-check: dry_run=1 — RW ioremap + firmware copy only, then return 0.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
CELL="${CELL:-$JH/configs/arm64/rpi5-minimal.cell}"
LOG_DIR="$REPO_ROOT/compare/crtos"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/dry_run_${STAMP}.log"

mkdir -p "$LOG_DIR"

{
	echo "=== dry_run enable $(date -Iseconds) ==="
	uname -a
	echo "cell=$CELL"

	bash "$SCRIPT_DIR/rebuild_jailhouse_ko__重编驱动.sh"

	sudo cp -f "$JH/hypervisor/jailhouse.bin" /lib/firmware/jailhouse.bin
	echo "firmware: $(md5sum /lib/firmware/jailhouse.bin | awk '{print $1}')"

	sudo rmmod jailhouse 2>/dev/null || true
	# MUST reload ko after each rebuild — dry_run is fixed at insmod time
	sudo insmod "$JH/driver/jailhouse.ko" dry_run=1
	echo "jailhouse loaded: $(lsmod | grep '^jailhouse' || true)"
	echo "dry_run=$(cat /sys/module/jailhouse/parameters/dry_run 2>/dev/null || echo '?')"

	set +e
	sudo "$JH/tools/jailhouse" enable "$CELL"
	RC=$?
	set -e
	echo "enable exit=$RC"

	sudo dmesg | tail -30 | grep -i jailhouse || sudo dmesg | tail -15

	sudo rmmod jailhouse 2>/dev/null || true
	echo "Log: $LOG"
	if [ "$RC" -eq 0 ]; then
		echo "✅ dry_run OK — RW map + firmware copy verified"
	else
		echo "❌ dry_run failed (exit=$RC) — see dmesg for enable start / ioremap / dry_run OK"
	fi
	exit "$RC"
} 2>&1 | tee "$LOG"
