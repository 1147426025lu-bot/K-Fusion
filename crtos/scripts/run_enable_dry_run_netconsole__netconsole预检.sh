#!/bin/bash
# dry_run enable with netconsole — capture last kernel lines if SSH dies.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
CELL="${CELL:-$JH/configs/arm64/rpi5-minimal.cell}"
LOG_DIR="$REPO_ROOT/compare/crtos"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/dry_run_netconsole_${STAMP}.log"
PC_IP="${PC_IP:-192.168.137.1}"
NC_PORT="${NC_PORT:-6666}"

mkdir -p "$LOG_DIR"

{
	echo "=== dry_run + netconsole $(date -Iseconds) ==="
	echo "PC_IP=$PC_IP NC_PORT=$NC_PORT"
	uname -a
	echo "cell=$CELL"
	echo ""
	echo ">>> Windows PC 先开 PowerShell UDP 监听 (端口 $NC_PORT) <<<"
	echo ""

	sudo bash "$SCRIPT_DIR/disable_watchdog_for_jh__关闭看门狗.sh"
	sudo bash "$SCRIPT_DIR/setup_netconsole__网络控制台.sh" "$PC_IP" "$NC_PORT"
	echo "netconsole test ping:"
	echo "netconsole-test-$(date +%H%M%S)" | sudo tee /dev/kmsg

	bash "$SCRIPT_DIR/rebuild_jailhouse_pi5__重编HV与驱动.sh"

	BIN="$JH/hypervisor/jailhouse.bin"
	if [ ! -s "$BIN" ]; then
		echo "ERROR: $BIN missing or empty — hypervisor build failed" >&2
		exit 1
	fi
	sudo cp -f "$BIN" /lib/firmware/jailhouse.bin
	echo "firmware: $(stat -c '%s bytes' /lib/firmware/jailhouse.bin) md5=$(md5sum /lib/firmware/jailhouse.bin | awk '{print $1}')"

	sudo rmmod jailhouse 2>/dev/null || true
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
		echo "✅ dry_run OK — check PC netconsole window for enable start / dry_run OK"
	else
		echo "❌ dry_run failed — last lines should be on PC netconsole"
	fi
	exit "$RC"
} 2>&1 | tee "$LOG"
