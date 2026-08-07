#!/bin/bash
# Stop hardware watchdog reboots while debugging Jailhouse enable (re-enable after bringup).
set -euo pipefail
DROP=/etc/systemd/system.conf.d/jailhouse-nowatchdog.conf

if [ "$(id -u)" -ne 0 ]; then
    echo "Run: sudo bash $0" >&2
    exit 1
fi

mkdir -p /etc/systemd/system.conf.d
cat >"$DROP" <<'EOF'
[Manager]
RuntimeWatchdogSec=0
RebootWatchdogSec=0
WatchdogDevice=
EOF

systemctl daemon-reexec
echo "✅ Watchdog disabled (systemd). Drop-in: $DROP"
echo "   Enable hangs will freeze SSH instead of rebooting in ~60s."
echo "   Restore: sudo bash crtos/scripts/restore_watchdog__恢复看门狗.sh"
