#!/bin/bash
# Listen for Pi netconsole and append every line to a timestamped log file.
#
# Run on PC (Linux/WSL) BEFORE enable on Pi:
#   bash crtos/scripts/netconsole_capture__netconsole抓日志.sh
#
# Windows (auto-save + hang detect after BLR):
#   powershell -File crtos/scripts/windows_netconsole_listen.ps1 -LogFile C:\temp\jh.log
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOGDIR="${JH_LOGDIR:-$REPO_ROOT/crtos/logs}"
PORT="${NETCONSOLE_PORT:-6666}"
OUT=""

while [ $# -gt 0 ]; do
	case "$1" in
	--port) PORT="$2"; shift 2 ;;
	--out)  OUT="$2"; shift 2 ;;
	-h|--help)
		echo "Usage: $0 [--port 6666] [--out file.log]"
		exit 0
		;;
	*) echo "Unknown: $1" >&2; exit 1 ;;
	esac
done

mkdir -p "$LOGDIR"
[ -n "$OUT" ] || OUT="$LOGDIR/netconsole-$(date +%Y%m%d-%H%M%S).log"

echo "[$(date -Iseconds)] === netconsole capture port=$PORT out=$OUT ===" | tee -a "$OUT"
echo "[$(date -Iseconds)] Pi: sudo bash crtos/scripts/setup_netconsole__网络控制台.sh <PC-IP>" | tee -a "$OUT"

if ! command -v nc >/dev/null 2>&1; then
	echo "ERROR: nc not found. Use Windows PowerShell script with -LogFile" >&2
	exit 1
fi

echo "[$(date -Iseconds)] listening..." | tee -a "$OUT"
while IFS= read -r line; do
	printf '[%s] %s\n' "$(date -Iseconds)" "$line" | tee -a "$OUT"
done < <(nc -u -l -p "$PORT" 2>/dev/null || nc -ul "$PORT")
