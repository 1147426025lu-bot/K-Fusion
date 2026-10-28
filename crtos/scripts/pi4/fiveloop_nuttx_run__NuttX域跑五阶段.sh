#!/bin/bash
# fiveloop_nuttx_run__NuttX域跑五阶段.sh — 主机侧(Git Bash)：在 NuttX inmate 跑 fiveloop plan/flat
#
# 与 bm2_nuttx_run 同通道纪律：SSH 控 cell，串口抓数（inmate 独占 mini-UART）。
#
# 用法：
#   bash fiveloop_nuttx_run__NuttX域跑五阶段.sh [cycles=60000] [port=COM11]
#   例：bash fiveloop_nuttx_run__NuttX域跑五阶段.sh 5000 COM11     # 5 s 冒烟
#       bash fiveloop_nuttx_run__NuttX域跑五阶段.sh 60000 COM11    # 60 s 正式
#
# 验收：
#   - plan 与 flat 的 `# digest:` 必须相同（融合只改释放次数，不改采样序列）
#   - plan 的 response p50 应接近一次释放税（NuttX ≈6 µs + 执行）
#   - flat 的 response p50 应接近 (N-1)·step + 释放税（5 阶段 × 200 µs ≈ 800 µs 量级）
set -uo pipefail

CYCLES="${1:-60000}"
PORT="${2:-COM11}"
BAUD="${BAUD:-115200}"
PI_HOST="${PI_HOST:-root@192.168.1.4}"
PI_PW="${PI_PW:-root}"
PI_HOSTKEY="${PI_HOSTKEY:-SHA256:ECSWgs0cMn9Qk6FbrJecWoCkhHPY0JH/5JPbfWZAy3Y}"
PLINK="${PLINK:-plink}"

HERE="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="${OUTDIR:-$HERE/../../results/raw}"
mkdir -p "$OUTDIR"
STAMP="$(date +%Y%m%d_%H%M%S)"

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

pi() { "$PLINK" -ssh -batch -hostkey "$PI_HOSTKEY" -pw "$PI_PW" "$PI_HOST" "$@"; }

ser() {
  local pre="$1" hold="$2" send="$3"
  timeout $((hold + 20)) "$PLINK" -serial "$PORT" -sercfg "${BAUD},8,n,1,N" \
    < <(sleep "$pre"; printf '%s\r' "$send"; sleep $((hold - pre))) 2>&1
}

say "0) 前置检查"
pi "[ -f /root/nuttx-build/nuttx/nuttx.bin ] || exit 1
    grep -q CONFIG_BENCHMARK_FIVELOOP=y /root/nuttx-build/nuttx/.config || exit 2
    jailhouse cell list 2>/dev/null | grep -q Raspberry-Pi4 || exit 3
    echo OK" || die "前置失败（缺 nuttx.bin / 未开 FIVELOOP / root cell 未起）"

say "1) 建 cell"
pi "bash /root/kfusion-pi4/scripts/deploy_nuttx_cell__部署NuttX_cell.sh 2>&1 | tail -6" \
  || die "cell 部署失败"

say "2) 等 NSH"
BOOT="$(ser 0 40 $'\n')"
printf '%s' "$BOOT" | grep -q 'nsh>' || {
  pi "jailhouse cell destroy rpi4-nuttx 2>/dev/null || true" >/dev/null 2>&1
  die "未看到 nsh>"
}

CAP=$(( CYCLES / 1000 + 90 ))
for MODE in plan flat; do
  LOG="$OUTDIR/fiveloop_nuttx_${MODE}_${CYCLES}_${STAMP}.log"
  say "3) fiveloop $MODE $CYCLES → $LOG"
  ser 3 "$CAP" "fiveloop $MODE $CYCLES" > "$LOG"
  grep -E '^# (mode:|digest:|stat_response|deadline_miss)' "$LOG" | sed 's/^/   /' || true
done

say "4) 拆 cell"
pi "jailhouse cell destroy rpi4-nuttx 2>&1 | tail -2" || true

PLAN_LOG="$OUTDIR/fiveloop_nuttx_plan_${CYCLES}_${STAMP}.log"
FLAT_LOG="$OUTDIR/fiveloop_nuttx_flat_${CYCLES}_${STAMP}.log"
DP=$(grep -m1 '^# digest:' "$PLAN_LOG" | awk '{print $3}')
DF=$(grep -m1 '^# digest:' "$FLAT_LOG" | awk '{print $3}')
say "5) digest 对照：plan=$DP flat=$DF"
if [ -n "$DP" ] && [ "$DP" = "$DF" ]; then
  say "   ✅ digest 一致"
  exit 0
fi
say "   ❌ digest 不一致或缺失"
exit 2
