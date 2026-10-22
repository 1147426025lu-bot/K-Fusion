#!/bin/bash
# bm2_nuttx_run__NuttX域跑BM2.sh — 主机侧(Git Bash)：在 NuttX 非根 cell 里跑 bm2pid 并抓串口直方图
#
# 为什么需要这个脚本（两条通道的时序问题）：
#   - NuttX inmate 的输出走**物理 mini-UART**(GPIO14/15)，`jailhouse console` 看不到；
#   - cell 一旦 start，UART 就归 inmate，**Linux 控制台变黑**；
#   - 所以：控制必须走 SSH（WiFi/以太网），抓数必须走串口（COM11）。
#   本脚本把「SSH 建 cell → 串口等 nsh> → 发 bm2pid → 抓窗口 → SSH 拆 cell」固化，
#   避免手敲时把 1 h 的窗口打歪。
#
# 用法：
#   bash bm2_nuttx_run__NuttX域跑BM2.sh [loops=3600000] [port=COM11]
#   例：bash bm2_nuttx_run__NuttX域跑BM2.sh 60000 COM11     # 60 s 冒烟
#       bash bm2_nuttx_run__NuttX域跑BM2.sh 3600000 COM11   # 1 h 正式
#
# 环境变量：PI_HOST(默认 root@192.168.1.4) PI_PW(root) PI_HOSTKEY(...) BAUD(115200)
#
# 口径（与 bm2_linux_run__Linux域跑BM2.sh 对齐）：
#   - 1 kHz 周期、同一份 bm2pid_main.c；
#   - Linux 侧 chrt -f 99 + CPU3；NuttX 侧用 app 内 PRIORITY（各自最高可用档，不可跨 OS 对齐数值）；
#   - 跑测期间 WiFi 可开：inmate 独占 CPU1，控制流量落在 root cell 的 CPU0/3，不进 cell。
#
# 前置：Pi 上 jailhouse 已 enable（root cell running）、/root/nuttx-build/nuttx/nuttx.bin 已构建。
set -uo pipefail

LOOPS="${1:-3600000}"
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
LOG="$OUTDIR/bm2_nuttx_${LOOPS}_${STAMP}.log"

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

pi() { "$PLINK" -ssh -batch -hostkey "$PI_HOSTKEY" -pw "$PI_PW" "$PI_HOST" "$@"; }

# 串口一次会话：$1=发送前等待秒数 $2=总抓取秒数 $3=要发送的字符串（空=不发）
ser() {
  local pre="$1" hold="$2" send="$3"
  if [ -z "$send" ]; then
    timeout $((hold + 20)) "$PLINK" -serial "$PORT" -sercfg "${BAUD},8,n,1,N" < <(sleep "$hold") 2>&1
  else
    timeout $((hold + 20)) "$PLINK" -serial "$PORT" -sercfg "${BAUD},8,n,1,N" \
      < <(sleep "$pre"; printf '%s\r' "$send"; sleep $((hold - pre))) 2>&1
  fi
}

say "0) 前置检查（SSH）"
pi "jailhouse cell list 2>/dev/null | grep -q Raspberry-Pi4 || { echo NO_ROOTCELL; exit 1; }
    [ -f /root/nuttx-build/nuttx/nuttx.bin ] || { echo NO_NUTTX_BIN; exit 1; }
    ls -l /root/nuttx-build/nuttx/nuttx.bin | awk '{print \"nuttx.bin\",\$5,\"bytes\"}'
    grep -E '^(CONFIG_BENCHMARK_BM2PID|CONFIG_SCHED_TICKLESS|CONFIG_RR_INTERVAL)' /root/nuttx-build/nuttx/.config | tr '\\n' ' '; echo" \
  || die "前置检查失败（root cell 未激活 / nuttx.bin 缺失）"

say "1) 建 cell + 启动（UART 将归 inmate，Linux 控制台转黑）"
pi "bash /root/kfusion-pi4/scripts/deploy_nuttx_cell__部署NuttX_cell.sh 2>&1 | tail -6" \
  || die "cell 部署失败"

say "2) 等 NSH 就绪（串口 45 s 探活）"
BOOT="$(ser 0 40 $'\n')"
if ! printf '%s' "$BOOT" | grep -q 'nsh>'; then
  printf '%s\n' "$BOOT" | tail -20
  pi "jailhouse cell destroy rpi4-nuttx 2>/dev/null || true" >/dev/null 2>&1
  die "未看到 nsh> 提示符（已尝试拆 cell）。检查 GPIO14/15 接线与 nuttx.bin"
fi
say "   NSH 就绪"

say "3) 发命令并抓取：bm2pid -l $LOOPS（预计 $((LOOPS / 1000)) s + 收尾）"
CAP=$(( LOOPS / 1000 + 90 ))
ser 3 "$CAP" "bm2pid -l $LOOPS" > "$LOG"
say "   串口日志: $LOG ($(wc -c < "$LOG") 字节)"

say "4) 拆 cell，把 UART 还给 Linux"
pi "jailhouse cell destroy rpi4-nuttx 2>&1 | tail -2; jailhouse cell list | tail -2" || true

say "5) 解析"
if ! grep -q 'exec_us' "$LOG"; then
  echo "WARN: 日志里没有 exec_us —— 本次没有跑到收尾（窗口不足或 inmate 异常）。" >&2
  echo "      可在 Pi 上重试，或把 LOOPS 调小先冒烟。" >&2
  exit 2
fi
grep -E '^# (Total|Max|exec_us)' "$LOG" | sed 's/^/   /'
awk '
  length($1)==6 && $2+0>0 { b[$1+0]=$2+0; n+=$2; if(!f){min=$1+0;f=1} if($1+0<min)min=$1+0; if($1+0>max)max=$1+0 }
  END{
    if(n==0){print "   n=0 解析失败"; exit}
    for(k in b)o[++m]=k
    for(x=1;x<=m;x++)for(y=x+1;y<=m;y++)if(o[x]+0>o[y]+0){t=o[x];o[x]=o[y];o[y]=t}
    c=0;pa="?";pb="?"
    for(x=1;x<=m;x++){c+=b[o[x]]; if(pa=="?"&&c>=n*0.5)pa=o[x]+0; if(pb=="?"&&c>=n*0.99)pb=o[x]+0}
    printf "   n=%d min=%d p50=%s p99=%s buckmax=%d\n",n,min,pa,pb,max
  }' "$LOG"
echo
echo "对数：bash bm2_crossdomain_plot__跨域CDF对比.py out.png \"Linux:<linux日志>\" \"NuttX:$LOG\""
