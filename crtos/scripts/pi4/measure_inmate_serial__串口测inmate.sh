#!/bin/bash
# measure_inmate_serial__串口测inmate.sh — 从串口驱动 NuttX inmate 的 rtlat 抖动测量（Linux/macOS 主机）
#
# 用法: bash measure_inmate_serial__串口测inmate.sh <serial_dev> [interval_us=1000] [loops=60000]
# 例:   bash measure_inmate_serial__串口测inmate.sh /dev/ttyUSB0 1000 60000
#
# 说明: inmate(NSH) 输出走物理 mini-UART(GPIO 14/15)。本脚本在接串口的主机上打开串口、
#       发送 rtlat 命令、抓取输出，解析 "rtlat RESULT" 行并落日志。
set -euo pipefail
DEV="${1:?用法: $0 <serial_dev> [interval_us] [loops]}"
IV="${2:-1000}"
LOOPS="${3:-60000}"
BAUD="${BAUD:-115200}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="${OUTDIR:-$HERE/../../results/raw}"
mkdir -p "$OUTDIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$OUTDIR/pi4_inmate_rtlat_${STAMP}.log"
CAP=$(( LOOPS * IV / 1000000 + 8 ))

echo "=== inmate rtlat 串口测量: dev=$DEV baud=$BAUD iv=${IV}us loops=$LOOPS cap=${CAP}s ==="
command -v stty >/dev/null || { echo "FAIL: 缺 stty" >&2; exit 1; }
stty -F "$DEV" "$BAUD" cs8 -cstopb -parenb -echo raw

timeout "$CAP" cat "$DEV" > "$LOG" &
CATPID=$!
sleep 0.5
printf '\r' > "$DEV"; sleep 0.3
printf 'rtlat -i %s -l %s\r' "$IV" "$LOOPS" > "$DEV"
wait "$CATPID" 2>/dev/null || true

LINE="$(grep -E 'rtlat RESULT' "$LOG" | tail -1 || true)"
if [ -z "$LINE" ]; then
  echo "WARN: 未找到 'rtlat RESULT'。检查 cell 运行 / NSH 就绪 / GPIO14/15 接线。日志: $LOG" >&2
  exit 2
fi
echo "RESULT: $LINE"
echo "$LINE"
echo "日志: $LOG"
