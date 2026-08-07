#!/bin/bash
# Reduce WiFi drops when USB3 storage is attached (Pi 5 + 2.4GHz hotspot).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../../plcfusion/scripts/lib/repo_paths__仓库路径.sh"

CONF=/etc/NetworkManager/conf.d/wifi-powersave-off.conf
sudo mkdir -p /etc/NetworkManager/conf.d
sudo tee "$CONF" >/dev/null <<'EOF'
[connection]
wifi.powersave = 2
EOF

sudo nmcli connection reload 2>/dev/null || true
if ip link show wlan0 >/dev/null 2>&1; then
    sudo iw dev wlan0 set power_save off 2>/dev/null || true
fi

echo "✅ WiFi powersave disabled (NetworkManager wifi.powersave=2)"
echo "   Still use 5 GHz hotspot or USB2/ethernet if 2.4G stays flaky with USB3."
