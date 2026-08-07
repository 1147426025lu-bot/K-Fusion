#!/bin/bash
# Stream kernel log to another host over UDP (no Pi serial cable).
set -euo pipefail
TARGET="${1:-}"
PORT="${2:-6666}"

usage() {
    cat <<EOF
用法: sudo bash $0 <receiver-ip> [port]

Pi 与 PC 同网段 (ping 必须通)。

Windows（无 nc）在 PC 上:

  1) 管理员 PowerShell 放行 UDP（只需一次）:
     netsh advfirewall firewall add rule name="PiNetconsole6666" dir=in action=allow protocol=UDP localport=6666

  2) 监听窗口（保持运行）:
     powershell -NoProfile -ExecutionPolicy Bypass -File crtos/scripts/windows_netconsole_listen.ps1

  Pi 上在第二个 SSH 窗口测试: echo hi | sudo tee /dev/kmsg

卸载: sudo modprobe -r netconsole
EOF
}

[ "$(id -u)" -eq 0 ] || { echo "sudo required" >&2; exit 1; }
[ -n "$TARGET" ] || { usage; exit 1; }

# Prefer wired Ethernet when link is up — WiFi often dies with kernel Oops/hang.
IFACE=""
if ip link show eth0 2>/dev/null | grep -q 'LOWER_UP'; then
	IFACE="eth0"
fi
if [ -z "$IFACE" ]; then
	IFACE="$(ip route get "$TARGET" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
fi
SRC="$(ip route get "$TARGET" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
[ -n "$IFACE" ] || { echo "ERROR: no route to $TARGET" >&2; exit 1; }
[ -n "$SRC" ] || SRC="$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | cut -d/ -f1 | head -1)"

modprobe -r netconsole 2>/dev/null || true

# Format: [src-port]@[src-ip]/[dev],[tgt-port]@<tgt-ip>/
NC_CFG="${PORT}@${SRC}/${IFACE},${PORT}@${TARGET}/"
echo "Loading netconsole: $NC_CFG"
modprobe netconsole "netconsole=${NC_CFG}"

echo "✅ netconsole ${SRC}@${IFACE} → ${TARGET}:${PORT}"
echo "   Test: echo hi | sudo tee /dev/kmsg"
