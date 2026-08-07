#!/bin/bash
# Pi5 Jailhouse enable staged test. NEVER runs full EL2 unless STEP=full.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
CELL="${JAILHOUSE_CELL:-$JH/configs/arm64/rpi5-minimal.cell}"
KO="$JH/driver/jailhouse.ko"
STEP="${1:-make_exec_stop}"

log() { echo "[$(date -Iseconds)] $*"; }

# Kernel hang in make_exec cannot be stopped by userspace timeout — require netconsole first.
require_netconsole_gate() {
	if [ "${JAILHOUSE_ENABLE_OK:-}" = "yes" ]; then
		return 0
	fi
	cat >&2 <<'EOF'
REFUSED: make_exec / full enable can freeze the whole Pi (SSH dies, reboot required).

Before running, on your PC (192.168.137.1) — Windows has no nc by default:

  cd <k-fusion repo on PC or copy scripts folder>
  powershell -NoProfile -ExecutionPolicy Bypass -File crtos/scripts/windows_netconsole_listen.ps1

  Or paste in PowerShell (one window, leave running):

  $u = New-Object System.Net.Sockets.UdpClient 6666
  $ep = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
  while ($true) { $b = $u.Receive([ref]$ep); Write-Host ([Text.Encoding]::UTF8.GetString($b)) }

On the Pi:
  sudo bash crtos/scripts/setup_netconsole__网络控制台.sh 192.168.137.1
  echo hi | sudo tee /dev/kmsg    # confirm line appears on PC

Then re-run with:
  JAILHOUSE_ENABLE_OK=yes bash crtos/scripts/test_pi5_enable_steps__分步enable测试.sh make_exec_stop

Safe without netconsole: dry_run | el2_stop only.
EOF
	exit 2
}

load_driver() {
	log "=== load driver ==="
	log "NOTE: do not 'insmod jailhouse.ko' by hand — this script sets scratch_trace/sequential_el2"
	if lsmod | grep -q '^jailhouse '; then
		if [ -e /sys/module/jailhouse/parameters/force_cleanup ]; then
			echo 1 | sudo tee /sys/module/jailhouse/parameters/force_cleanup >/dev/null 2>&1 || true
		fi
		if ! sudo rmmod jailhouse 2>/dev/null; then
			if [ -e /sys/module/jailhouse/parameters/force_cleanup ]; then
				log "rmmod failed — trying force_cleanup (post-Oops recovery)"
				echo 1 | sudo tee /sys/module/jailhouse/parameters/force_cleanup >/dev/null
				sudo rmmod jailhouse 2>/dev/null || true
			fi
		fi
		if lsmod | grep -q '^jailhouse '; then
			log "ERROR: jailhouse still loaded — reboot, or:"
			log "  echo 1 | sudo tee /sys/module/jailhouse/parameters/force_cleanup && sudo rmmod jailhouse"
			exit 1
		fi
	fi
	local extra_params=()
	[ "${JH_TRACE_EL2:-0}" = "1" ] && extra_params+=(trace_el2=1)
	[ "${JH_SEQUENTIAL_EL2:-0}" = "1" ] && extra_params+=(sequential_el2=1)
	[ "${JH_SCRATCH_TRACE:-0}" = "1" ] && extra_params+=(scratch_trace=1)
	[ "${JH_STUB_ARCH_ENTRY:-0}" = "1" ] && extra_params+=(stub_arch_entry=1)
	[ "${JH_UART_TRACE:-0}" = "1" ] && extra_params+=(uart_trace=1)
	[ "${JH_HYP_PROBE:-0}" = "1" ] && extra_params+=(hyp_probe=1)
	sudo insmod "$KO" "${extra_params[@]}"
	log "insmod OK ko=$(stat -c '%y' "$KO" | cut -d. -f1) refcnt=$(cat /sys/module/jailhouse/refcnt) params=${extra_params[*]:-(none)}"
}

setup_netconsole_if_needed() {
	local pc_ip="${NETCONSOLE_PC:-192.168.137.1}"
	local port="${NETCONSOLE_PORT:-6666}"
	if lsmod | grep -q '^netconsole '; then
		log "netconsole already loaded"
		return 0
	fi
	log "=== setup netconsole → ${pc_ip}:${port} ==="
	sudo bash "$SCRIPT_DIR/setup_netconsole__网络控制台.sh" "$pc_ip" "$port"
	log "netconsole test: echo hi (must appear on PC listener NOW)"
	echo hi | sudo tee /dev/kmsg >/dev/null
	sleep 1
}

run_enable() {
	local label=$1 dry=$2 el2=$3 mex=$4
	local enable_cmd=(sudo)
	log "=== $label (dry_run=$dry el2_stop=$el2 make_exec_stop=$mex) ==="
	echo "$dry" | sudo tee /sys/module/jailhouse/parameters/dry_run >/dev/null
	echo "$el2" | sudo tee /sys/module/jailhouse/parameters/el2_stop >/dev/null
	echo "$mex" | sudo tee /sys/module/jailhouse/parameters/make_exec_stop >/dev/null
	# Pin enable on CPU0 so sequential_el2 uses direct enter_hypervisor (no IPI hang)
	if [ "$dry" = 0 ] && [ "$el2" = 0 ] && [ "$mex" = 0 ]; then
		enable_cmd=(taskset -c 0 sudo)
		log "enable pinned to CPU0 (taskset -c 0)"
	fi
	if ! timeout "${ENABLE_TIMEOUT:-45}" "${enable_cmd[@]}" "$JH/tools/jailhouse" enable "$CELL"; then
		local rc=$?
		log "$label: enable FAILED/TIMEOUT exit=$rc"
		sudo dmesg | grep 'jailhouse:' | tail -20
		show_scratch
		return "$rc"
	fi
	log "$label: enable OK"
}

show_dmesg() {
	log "=== dmesg (jailhouse) ==="
	sudo dmesg | grep 'jailhouse:' | tail -25
}

show_scratch() {
	log "=== scratch dump (same boot if module still loaded) ==="
	if [ -e /sys/module/jailhouse/parameters/scratch_dump ] 2>/dev/null; then
		echo 1 | sudo tee /sys/module/jailhouse/parameters/scratch_dump >/dev/null 2>&1 || true
		sudo dmesg | grep 'jailhouse:.*scratch\|jailhouse: 00' | tail -10
	else
		log "  scratch_dump unavailable (module unloaded?) — if Pi SSH works after hang:"
		log "  echo 1 | sudo tee /sys/module/jailhouse/parameters/force_cleanup; sudo rmmod jailhouse"
		log "  or reboot then: bash crtos/scripts/read_jh_scratch__读scratch.sh"
	fi
	log "  0000: byte0-3=EL1 A(41) B(42) C(43) R(52)  byte4-7=EL2 D(44) E(45) P(46) F(47)  byte8=>(3e)"
	log "  read: echo 1 | sudo tee /sys/module/jailhouse/parameters/scratch_dump; sudo dmesg | tail -5"
}

case "$STEP" in
load)       load_driver ;;
dry_run)    load_driver; run_enable "dry_run" 1 0 0; show_dmesg ;;
el2_stop)   load_driver; run_enable "el2_stop" 0 1 0; show_dmesg ;;
make_exec_stop)
	require_netconsole_gate
	setup_netconsole_if_needed
	load_driver
	run_enable "make_exec_stop" 0 0 1
	show_dmesg
	log "NOTE: run 'sudo rmmod jailhouse' or reboot before full EL2 enable"
	;;
full)
	require_netconsole_gate
	setup_netconsole_if_needed
	log "WARNING: full EL2 may hang WiFi/SSH"
	load_driver
	run_enable "full_enable" 0 0 0
	show_dmesg
	;;
full_debug)
	require_netconsole_gate
	setup_netconsole_if_needed
	export JH_TRACE_EL2=1 JH_SEQUENTIAL_EL2=1 JH_SCRATCH_TRACE=1 JH_HYP_PROBE=1 JH_UART_TRACE=1
	log "WARNING: full EL2 debug (sequential CPUs, trace_el2, scratch@0x1ffb0000)"
	log "CELL=$CELL"
	load_driver
	run_enable "full_debug" 0 0 0
	show_dmesg
	;;
full_1cpu_debug)
	require_netconsole_gate
	setup_netconsole_if_needed
	log "=== rebuild production HV (no JH_ARCH_ENTRY_SMOKE) ==="
	bash "$SCRIPT_DIR/rebuild_jailhouse_pi5__重编HV与驱动.sh"
	sudo cp -f "$JH/hypervisor/jailhouse.bin" /lib/firmware/jailhouse.bin
	# Minimal params — scratch/uart/trace extra work before EL2
	export JH_SEQUENTIAL_EL2=1 JH_SCRATCH_TRACE=1
	CELL="$JH/configs/arm64/rpi5-minimal-1cpu.cell"
	log "WARNING: 1-CPU full EL2 (sequential + scratch; SET_VECTORS+hvc trap from driver)"
	log "HV mtime: $(stat -c '%y' "$JH/hypervisor/jailhouse.bin" 2>/dev/null | cut -d. -f1)"
	log "KO mtime: $(stat -c '%y' "$KO" 2>/dev/null | cut -d. -f1)"
	load_driver
	run_enable "full_1cpu_debug" 0 0 0
	show_dmesg
	;;
full_1cpu_trace)
	require_netconsole_gate
	setup_netconsole_if_needed
	export JH_TRACE_EL2=1 JH_SEQUENTIAL_EL2=1 JH_SCRATCH_TRACE=1 JH_UART_TRACE=1
	CELL="$JH/configs/arm64/rpi5-minimal-1cpu.cell"
	log "WARNING: full EL2 with scratch/trace (may affect timing)"
	load_driver
	run_enable "full_1cpu_trace" 0 0 0
	show_dmesg
	;;
stub_arch_entry)
	require_netconsole_gate
	setup_netconsole_if_needed
	export JH_SEQUENTIAL_EL2=1 JH_SCRATCH_TRACE=1 JH_STUB_ARCH_ENTRY=1 JH_HYP_PROBE=1
	CELL="$JH/configs/arm64/rpi5-minimal-1cpu.cell"
	log "BLR isolation: stub_arch_entry=1 (must NOT hang; expect arch_entry STUB in dmesg)"
	load_driver
	run_enable "stub_arch_entry" 0 0 0
	show_dmesg
	;;
arch_entry_smoke)
	require_netconsole_gate
	setup_netconsole_if_needed
	log "=== rebuild HV with JH_ARCH_ENTRY_SMOKE (ret before HVC) ==="
	JH_ARCH_ENTRY_SMOKE=1 bash "$SCRIPT_DIR/rebuild_jailhouse_pi5__重编HV与驱动.sh"
	sudo cp -f "$JH/hypervisor/jailhouse.bin" /lib/firmware/jailhouse.bin
	export JH_TRACE_EL2=1 JH_SEQUENTIAL_EL2=1 JH_SCRATCH_TRACE=1 JH_HYP_PROBE=1
	CELL="$JH/configs/arm64/rpi5-minimal-1cpu.cell"
	log "arch_entry_smoke: real BLR arch_entry, HV returns 0 before HVC"
	log "PASS: 'arch_entry returned -11' + scratch byte0=A; FAIL: hang at BLR"
	load_driver
	run_enable "arch_entry_smoke" 0 0 0
	show_dmesg
	show_scratch
	;;
arch_entry_smoke_early)
	require_netconsole_gate
	setup_netconsole_if_needed
	log "=== rebuild HV with JH_ARCH_ENTRY_SMOKE=early (ret after bti_c) ==="
	JH_ARCH_ENTRY_SMOKE=early bash "$SCRIPT_DIR/rebuild_jailhouse_pi5__重编HV与驱动.sh"
	sudo cp -f "$JH/hypervisor/jailhouse.bin" /lib/firmware/jailhouse.bin
	export JH_SEQUENTIAL_EL2=1 JH_SCRATCH_TRACE=1 JH_HYP_PROBE=1
	CELL="$JH/configs/arm64/rpi5-minimal-1cpu.cell"
	log "arch_entry_smoke_early: BLR then immediate ret — isolates fetch/BTI/icache"
	log "PASS: arch_entry returned -11 (EAGAIN); FAIL: hang at BLR"
	load_driver
	run_enable "arch_entry_smoke_early" 0 0 0
	show_dmesg
	show_scratch
	;;
all_safe)
	load_driver
	run_enable "dry_run" 1 0 0
	run_enable "el2_stop" 0 1 0
	show_dmesg
	log "NOTE: make_exec_stop omitted — run separately with JAILHOUSE_ENABLE_OK=yes"
	;;
*)
	echo "Usage: $0 {load|dry_run|el2_stop|make_exec_stop|all_safe|full|full_debug|full_1cpu_debug|full_1cpu_trace|stub_arch_entry|arch_entry_smoke_early|arch_entry_smoke}" >&2
	exit 1
	;;
esac
