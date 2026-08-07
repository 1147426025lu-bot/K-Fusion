#!/bin/bash
# Read Jailhouse Pi5 scratch RAM @0x1ffb0000 (survives reboot if nomap HV RAM retained).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
KO="${KO:-$REPO_ROOT/crtos/upstream/jailhouse/hypervisor/../driver/jailhouse.ko}"
KO="$REPO_ROOT/crtos/upstream/jailhouse/driver/jailhouse.ko"
JH_SCRATCH=0x1ffb0000

log() { echo "[$(date -Iseconds)] $*"; }

if lsmod | grep -q '^jailhouse '; then
	log "jailhouse already loaded"
else
	log "insmod $KO scratch_trace=1 (read-only, no enable)"
	sudo insmod "$KO" scratch_trace=1 2>/dev/null || {
		log "insmod failed — try: sudo rmmod jailhouse; reboot"
		exit 1
	}
fi

if [ ! -e /sys/module/jailhouse/parameters/scratch_dump ]; then
	log "scratch_dump sysfs missing (old jailhouse.ko?)"
	exit 1
fi

echo 1 | sudo tee /sys/module/jailhouse/parameters/scratch_dump >/dev/null
sudo dmesg | grep -E 'jailhouse: (scratch|0000:|0010:)' | tail -10

log "scratch bytes 0-3: EL1 A B C R | byte10: arch_entry 0/1/2 | byte4-9: EL2 | +0x70: trail"
log "Do NOT use ramoops @0xb000000 — that is pstore printk, not Jailhouse scratch."
python3 - <<'PY'
import os
try:
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    os.lseek(fd, 0x1ffb0070, os.SEEK_SET)
    b = os.read(fd, 16)
    os.close(fd)
    ln = b[0] if b else 0
    trail = b[1:1 + min(ln, 15)] if ln else b""
    print(f"  phys+0x70 trail len={ln} ascii={trail.decode('ascii', errors='replace')!r}")
except OSError as e:
    print(f"  phys+0x70: {e} (use scratch_dump via loaded jailhouse.ko)")
PY
log "NOTE: nomap scratch clears on full reboot — trail only valid same boot or if HV RAM retained"
