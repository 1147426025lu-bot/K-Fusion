#!/bin/bash
# Test make_exec (no el2_stop): expect -EFAULT if JAILHOUSE_BASE PTE unreadable, not Oops.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
CELL="${CELL:-$JH/configs/arm64/rpi5-minimal.cell}"
LOG_DIR="$REPO_ROOT/compare/crtos"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/make_exec_${STAMP}.log"

mkdir -p "$LOG_DIR"

{
	echo "=== make_exec test $(date -Iseconds) ==="
	uname -a

	bash "$SCRIPT_DIR/rebuild_jailhouse_ko__重编驱动.sh"

	sudo rmmod jailhouse 2>/dev/null || {
		echo "ERROR: rmmod failed — power-cycle first"
		exit 1
	}

	sudo insmod "$JH/driver/jailhouse.ko"
	set +e
	sudo "$JH/tools/jailhouse" enable "$CELL"
	RC=$?
	set -e
	echo "enable exit=$RC (expect 1 / EFAULT until JAILHOUSE_BASE PTE fixed)"

	sudo dmesg | grep -i jailhouse | tail -15
	sudo rmmod jailhouse 2>/dev/null || true

	if dmesg | grep -q 'Internal error: Oops'; then
		echo "❌ Oops — need power-cycle"
		exit 1
	fi
	if dmesg | grep -q 'PTE probe failed'; then
		echo "⚠️  make_exec probe failed cleanly (-EFAULT) — JAILHOUSE_BASE mapping blocked"
	fi
	echo "Log: $LOG"
	exit "$RC"
} 2>&1 | tee "$LOG"
