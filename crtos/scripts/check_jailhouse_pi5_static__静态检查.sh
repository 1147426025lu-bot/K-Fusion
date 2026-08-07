#!/bin/bash
# Offline checks — no insmod/enable (safe before Pi reboot test).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
KO="$JH/driver/jailhouse.ko"
BIN="$JH/hypervisor/jailhouse.bin"
FAIL=0

check() {
	if "$@"; then
		echo "OK  $*"
	else
		echo "FAIL $*" >&2
		FAIL=1
	fi
}

echo "=== Jailhouse Pi5 static preflight ==="
echo "kernel: $(uname -r)"

check test -s "$BIN"
check test -s "$KO"

if [ -s "$BIN" ]; then
	echo "     bin: $(stat -c '%s bytes' "$BIN") md5=$(md5sum "$BIN" | awk '{print $1}')"
	HV="$JH/hypervisor/hypervisor.o"
	if [ -f "$HV" ]; then
		BASE=$(readelf -S "$HV" 2>/dev/null | awk '/\.header/ {print $5; exit}')
		echo "     HV link base (.header): ${BASE:-unknown}"
		if [ "$BASE" = "ffffc00080000000" ]; then
			echo "OK  JAILHOUSE_BASE canonical (VMALLOC_START)"
		else
			echo "WARN expected JAILHOUSE_BASE ffffc00080000000, got $BASE" >&2
		fi
	fi
fi

if [ -f "$KO" ]; then
	VM=$(modinfo -F vermagic "$KO" 2>/dev/null || true)
	TGT="$(uname -r) SMP preempt mod_unload aarch64"
	if [ "$VM" = "$TGT" ]; then
		echo "OK  vermagic matches running kernel"
	else
		echo "FAIL vermagic: ko=$VM kernel=$TGT" >&2
		FAIL=1
	fi
	if strings "$KO" | grep -q 'memremap hv phys'; then
		echo "OK  driver uses memremap for Pi5 RW path"
	else
		echo "WARN driver missing memremap path string" >&2
	fi
	if strings "$KO" | grep -q 'ioremap hv phys.*remap_addr=0x0'; then
		echo "WARN driver still has full-size JAILHOUSE_BASE ioremap RW" >&2
	fi
fi

if [ -f /lib/firmware/jailhouse.bin ]; then
	SZ=$(stat -c '%s' /lib/firmware/jailhouse.bin)
	if [ "$SZ" -gt 0 ]; then
		echo "OK  /lib/firmware/jailhouse.bin ($SZ bytes)"
	else
		echo "FAIL /lib/firmware/jailhouse.bin is empty" >&2
		FAIL=1
	fi
else
	echo "WARN /lib/firmware/jailhouse.bin not installed"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
	echo "Static checks passed. Safe to try: dry_run netconsole script."
else
	echo "Fix failures before any enable test."
	exit 1
fi
