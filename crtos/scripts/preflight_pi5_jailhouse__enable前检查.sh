#!/bin/bash
# Quick preflight before Pi5 jailhouse enable tests.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
echo "kernel: $(uname -r)"
JH_VA_BITS="$(zcat /proc/config.gz 2>/dev/null | grep '^CONFIG_ARM64_VA_BITS=' | cut -d= -f2 || true)"
if [ -z "$JH_VA_BITS" ]; then
	KR="$(uname -r)"
	for cfg in "$HOME/k-fusion/crtos/cache/jh-kernel-$KR/.config" \
		   "$HOME/k-fusion/crtos/cache/jh-kdir/.config"; do
		[ -f "$cfg" ] || continue
		JH_VA_BITS="$(grep '^CONFIG_ARM64_VA_BITS=' "$cfg" | cut -d= -f2)"
		[ -n "$JH_VA_BITS" ] && break
	done
fi
echo "VA_BITS: ${JH_VA_BITS:-unknown} (runtime kernel; must match HV+driver KDIR)"
echo "uptime: $(uptime -p)"
if lsmod | grep -q '^jailhouse '; then
	refcnt=$(cat /sys/module/jailhouse/refcnt 2>/dev/null || echo '?')
	echo "jailhouse: LOADED refcnt=$refcnt"
	if ! sudo rmmod jailhouse 2>/dev/null; then
		echo "STATUS: STUCK — power-cycle/reboot required before testing"
		exit 1
	fi
	echo "jailhouse: unloaded OK"
else
	echo "jailhouse: not loaded (OK)"
fi
KO="$JH/driver/jailhouse.ko"
BIN="$JH/hypervisor/jailhouse.bin"
if [ ! -f "$KO" ]; then
	echo "MISSING: $KO"
	exit 1
fi
if grep -aq 'direct_map clear+ioremap' "$KO"; then
	echo "driver: new Pi5 fix present"
	if zcat /proc/config.gz 2>/dev/null | grep -q '^CONFIG_ARM64_BTI=y' || \
	   grep -q '^CONFIG_ARM64_BTI=y' "$REPO_ROOT/crtos/cache/jh-kernel-$(uname -r)/.config" 2>/dev/null; then
		if [ -f "$BIN" ] && ! aarch64-linux-gnu-objdump -d "$JH/hypervisor/hypervisor.o" 2>/dev/null | grep -q 'bti[[:space:]]*c'; then
			echo "firmware: WARN — kernel has BTI but HV object lacks bti c at arch_entry (rebuild HV)"
		fi
	fi
	case "${JH_VA_BITS:-}" in
	39)
		if ! grep -q 'ffffffc0c0000000' "$JH/hypervisor/hypervisor.lds" 2>/dev/null; then
			echo "driver: WARN — HV not linked at 39-bit vmalloc slot (rebuild with JH_VA_BITS=39)"
		fi
		;;
	47)
		if ! grep -q 'ffffc000c0000000' "$JH/hypervisor/hypervisor.lds" 2>/dev/null; then
			echo "driver: WARN — HV not linked at 47-bit MODULES_END (rebuild with JH_VA_BITS=47)"
		fi
		;;
	esac
else
	echo "driver: OLD build — run rebuild_jailhouse_ko__重编驱动.sh"
	exit 1
fi
[ -s "$BIN" ] && echo "firmware: OK $(stat -c '%s bytes' "$BIN")" || { echo "firmware: EMPTY"; exit 1; }
echo "STATUS: ready (safe: dry_run / el2_stop only)"
echo ""
echo "SAFE  (no netconsole):  bash crtos/scripts/test_pi5_enable_steps__分步enable测试.sh dry_run"
echo "                        bash crtos/scripts/test_pi5_enable_steps__分步enable测试.sh el2_stop"
echo ""
echo "DANGER (kernel may hard-freeze — netconsole on PC required first):"
echo "  PC PowerShell (repo path):"
echo "    powershell -NoProfile -ExecutionPolicy Bypass -File crtos/scripts/windows_netconsole_listen.ps1"
echo "  Pi: sudo bash crtos/scripts/setup_netconsole__网络控制台.sh 192.168.137.1"
echo "  JAILHOUSE_ENABLE_OK=yes bash crtos/scripts/test_pi5_enable_steps__分步enable测试.sh make_exec_stop"
echo ""
echo "Do NOT run make_exec_stop without netconsole — timeout cannot unfreeze the kernel."
