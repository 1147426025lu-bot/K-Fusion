#!/bin/bash
set -euo pipefail
DROP=/etc/systemd/system.conf.d/jailhouse-nowatchdog.conf

[ "$(id -u)" -eq 0 ] || { echo "sudo required" >&2; exit 1; }

rm -f "$DROP"
systemctl daemon-reexec
echo "✅ Watchdog restored (removed $DROP)"
