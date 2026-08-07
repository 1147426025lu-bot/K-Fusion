#!/bin/bash
# Rebuild jailhouse.bin (Pi5 canonical VA) + jailhouse.ko for 6.12.95-jh.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JH="$REPO_ROOT/crtos/upstream/jailhouse"
KR="$(uname -r)"
KDIR="${KDIR:-$REPO_ROOT/crtos/cache/jh-kdir}"
KHDR="${KHDR:-$REPO_ROOT/crtos/cache/jh-kernel-6.12.95-jh/arm64/include}"
SRC_CFG="$REPO_ROOT/crtos/cache/jh-kernel-$KR/.config"
if [ -f "$SRC_CFG" ]; then
	JH_VA_BITS="$(grep '^CONFIG_ARM64_VA_BITS=' "$SRC_CFG" | cut -d= -f2)"
fi
JH_VA_BITS="${JH_VA_BITS:-$(grep '^CONFIG_ARM64_VA_BITS=' "$KDIR/.config" | cut -d= -f2)}"

if [ ! -d "$KDIR" ]; then
	echo "KDIR missing: $KDIR" >&2
	exit 1
fi

# jh-kdir is headers-only; hypervisor needs full arch/asm (sysreg.h, etc.)
ASM_DST="$KDIR/arch/arm64/include/asm"
if [ ! -f "$ASM_DST/sysreg.h" ] && [ -d "$KHDR/asm" ]; then
	echo "Link $ASM_DST -> $KHDR/asm"
	mkdir -p "$(dirname "$ASM_DST")"
	ln -sfn "$KHDR/asm" "$ASM_DST"
fi

TARGET_VM="$(uname -r) SMP preempt mod_unload aarch64"
case "$JH_VA_BITS" in
39) JH_BASE=0xffffffc0c0000000 ;;
47) JH_BASE=0xffffc000c0000000 ;;
48) JH_BASE=0xffff8000c0000000 ;;
*) JH_BASE="unknown" ;;
esac

echo "=== Build Jailhouse Pi5 (canonical JAILHOUSE_BASE + driver) ==="
echo "KDIR=$KDIR JH_VA_BITS=$JH_VA_BITS"
echo "JAILHOUSE_BASE=$JH_BASE (MODULES_END for this VA layout)"
if [ -n "${JH_ARCH_ENTRY_SMOKE:-}" ]; then
	echo "JH_ARCH_ENTRY_SMOKE=${JH_ARCH_ENTRY_SMOKE} (HV isolation — not for production enable)"
fi

MAKE_FLAGS=(JH_PI5_MINIMAL_DRIVER=1 JH_VA_BITS="$JH_VA_BITS")
[ -n "${JH_ARCH_ENTRY_SMOKE:-}" ] && MAKE_FLAGS+=(JH_ARCH_ENTRY_SMOKE=1)

make -C "$KDIR" M="$JH" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
	"${MAKE_FLAGS[@]}" clean 2>/dev/null || true
make -C "$KDIR" M="$JH" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
	"${MAKE_FLAGS[@]}" modules

KO="$JH/driver/jailhouse.ko"
BIN="$JH/hypervisor/jailhouse.bin"
if [ ! -f "$KO" ] || [ ! -f "$BIN" ]; then
	echo "Build failed: ko=$KO bin=$BIN" >&2
	exit 1
fi
BIN_SIZE="$(stat -c '%s' "$BIN")"
if [ "$BIN_SIZE" -lt 4096 ]; then
	echo "ERROR: $BIN is too small ($BIN_SIZE bytes) — build produced empty/corrupt image" >&2
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
if old not in data:
    sys.exit("vermagic patch: pattern not found")
data = data.replace(old, new)
open(path, "w+b").write(data)
print("patched vermagic: dropped modversions tag")
PY
fi

BUILT_VM="$(modinfo -F vermagic "$KO")"
if [ "$BUILT_VM" != "$TARGET_VM" ]; then
	echo "ERROR: vermagic mismatch built=$BUILT_VM kernel=$TARGET_VM" >&2
	exit 1
fi

echo "OK hypervisor: $BIN ($(stat -c '%y' "$BIN" | cut -d. -f1))"
echo "OK driver:     $KO ($(stat -c '%y' "$KO" | cut -d. -f1))"
if grep -q "$JH_BASE" "$JH/hypervisor/hypervisor.lds" 2>/dev/null; then
	echo "OK lds: JAILHOUSE_BASE=$JH_BASE"
else
	echo "WARN: hypervisor.lds missing JAILHOUSE_BASE=$JH_BASE" >&2
	grep '^\s*\.' "$JH/hypervisor/hypervisor.lds" 2>/dev/null | head -3 || true
fi
	# Install firmware and verify non-empty image
	if [ ! -f "$BIN" ] || [ "$(stat -c '%s' "$BIN")" -lt 4096 ]; then
		echo "ERROR: missing or empty $BIN — run rebuild_jailhouse_pi5 first" >&2
		exit 1
	fi
	echo "Install firmware: sudo cp -f $BIN /lib/firmware/jailhouse.bin"
