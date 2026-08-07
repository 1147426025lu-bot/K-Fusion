#!/bin/bash
# Build jailhouse.ko (driver only) against JH kernel headers.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
KR="$(uname -r)"
KDIR="${KDIR:-$REPO_ROOT/crtos/cache/jh-kdir}"
KHDR="${KHDR:-$REPO_ROOT/crtos/cache/jh-kernel-6.12.95-jh/arm64/include}"
# JAILHOUSE_BASE must match the *running* kernel VA layout, not necessarily KDIR .config.
SRC_CFG="$REPO_ROOT/crtos/cache/jh-kernel-$KR/.config"
if [ -f "$SRC_CFG" ]; then
	JH_VA_BITS="$(grep '^CONFIG_ARM64_VA_BITS=' "$SRC_CFG" | cut -d= -f2)"
fi
JH_VA_BITS="${JH_VA_BITS:-$(grep '^CONFIG_ARM64_VA_BITS=' "$KDIR/.config" | cut -d= -f2)}"

if [ ! -d "$KDIR" ]; then
	echo "KDIR missing: $KDIR" >&2
	exit 1
fi

ASM_DST="$KDIR/arch/arm64/include/asm"
if [ ! -f "$ASM_DST/sysreg.h" ] && [ -d "$KHDR/asm" ]; then
	mkdir -p "$(dirname "$ASM_DST")"
	ln -sfn "$KHDR/asm" "$ASM_DST"
fi

TARGET_VM="$(uname -r) SMP preempt mod_unload aarch64"
JH_VA_BITS="${JH_VA_BITS:-$(grep '^CONFIG_ARM64_VA_BITS=' "$KDIR/.config" | cut -d= -f2)}"

echo "=== Build jailhouse driver ==="
echo "KDIR=$KDIR JH_VA_BITS=$JH_VA_BITS (JAILHOUSE_BASE for running kernel layout)"
echo "target vermagic: $TARGET_VM"

make -C "$KDIR" M="$JH" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
	JH_PI5_MINIMAL_DRIVER=1 JH_VA_BITS="$JH_VA_BITS" modules

KO="$JH/driver/jailhouse.ko"
if [ ! -f "$KO" ]; then
	echo "Build failed: $KO not found" >&2
	exit 1
fi

BUILT_VM="$(modinfo -F vermagic "$KO")"
if [ "$BUILT_VM" != "$TARGET_VM" ] && echo "$BUILT_VM" | grep -q modversions; then
	python3 - "$KO" <<'PY'
import sys
path = sys.argv[1]
old = b"mod_unload modversions aarch64"
new = b"mod_unload aarch64" + b"\0" * (len(old) - len(b"mod_unload aarch64"))
data = bytearray(open(path, "r+b").read())
count = data.count(old)
if count == 0:
    sys.exit("vermagic patch: pattern not found in .ko")
data = data.replace(old, new)
open(path, "w+b").write(data)
print(f"patched vermagic ({count} slot(s)): dropped modversions tag")
PY
	BUILT_VM="$(modinfo -F vermagic "$KO")"
fi

if [ "$BUILT_VM" != "$TARGET_VM" ]; then
	echo "ERROR: vermagic still wrong" >&2
	echo "  built:  $BUILT_VM" >&2
	echo "  kernel: $TARGET_VM" >&2
	exit 1
fi

echo "OK: $KO ($(date -Iseconds))"
modinfo "$KO" | grep -E 'vermagic|filename' || true

BIN="$JH/hypervisor/jailhouse.bin"
if [ ! -s "$BIN" ]; then
	echo "WARN: $BIN missing/empty — run rebuild_jailhouse_pi5__重编HV与驱动.sh" >&2
	exit 1
fi
echo "OK: $BIN ($(stat -c '%s bytes' "$BIN"))"
