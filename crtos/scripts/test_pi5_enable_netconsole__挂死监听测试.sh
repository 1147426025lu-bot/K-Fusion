#!/bin/bash
# Pi5 make_exec_stop with netconsole + timeout. Run netconsole receiver on PC first.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
CELL="$JH/configs/arm64/rpi5-minimal.cell"
KO="$JH/driver/jailhouse.ko"
PC_IP="${NETCONSOLE_PC:-192.168.137.1}"
PORT="${NETCONSOLE_PORT:-6666}"
LOG="$REPO_ROOT/compare/crtos/enable_netconsole_$(date +%Y%m%d_%H%M%S).log"
TIMEOUT_SEC="${ENABLE_TIMEOUT:-45}"

if [ "${JAILHOUSE_ENABLE_OK:-}" != "yes" ]; then
	echo "Set JAILHOUSE_ENABLE_OK=yes after PC netconsole listener is running." >&2
	exit 2
fi

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

mkdir -p "$(dirname "$LOG")"
log "=== Pi5 enable hang-safe test ==="
log "kernel: $(uname -r) uptime: $(uptime -p)"
log "log file: $LOG"

if lsmod | grep -q '^jailhouse '; then
	if ! sudo rmmod jailhouse 2>>"$LOG"; then
		log "ERROR: jailhouse stuck — reboot first"
		exit 1
	fi
fi

log "Starting netconsole → ${PC_IP}:${PORT}"
sudo bash "$SCRIPT_DIR/setup_netconsole__网络控制台.sh" "$PC_IP" "$PORT" >>"$LOG" 2>&1 || {
	log "WARN: netconsole setup failed (continue with local dmesg only)"
}

sudo dmesg -C
log "Loading jailhouse.ko"
sudo insmod "$KO"
log "insmod OK refcnt=$(cat /sys/module/jailhouse/refcnt)"

echo 0 | sudo tee /sys/module/jailhouse/parameters/dry_run >/dev/null
echo 0 | sudo tee /sys/module/jailhouse/parameters/el2_stop >/dev/null
echo 1 | sudo tee /sys/module/jailhouse/parameters/make_exec_stop >/dev/null

log "=== enable (timeout ${TIMEOUT_SEC}s, make_exec_stop=1) ==="
set +e
timeout "$TIMEOUT_SEC" sudo "$JH/tools/jailhouse" enable "$CELL" >>"$LOG" 2>&1
rc=$?
set -e
log "jailhouse enable exit=$rc"

log "=== dmesg jailhouse (last 40) ==="
sudo dmesg | grep 'jailhouse:' | tail -40 | tee -a "$LOG"

if [ "$rc" -eq 124 ]; then
	log "TIMEOUT — likely kernel hang in make_exec (check last dmesg line on PC netconsole)"
	log "Bisect: echo 1 | sudo tee /sys/module/jailhouse/parameters/skip_vmap_clean"
	exit 124
fi

if [ "$rc" -ne 0 ]; then
	exit "$rc"
fi

log "PASS: make_exec_stop returned OK"
log "Reboot before next test (maps left up by design)"
