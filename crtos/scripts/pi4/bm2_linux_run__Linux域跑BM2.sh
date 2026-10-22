#!/usr/bin/env bash
# bm2pid 在 Linux 域（root cell）跑对照 —— 与 NuttX cell 用同一份源码
#
# 为什么这个脚本存在：
#   BM2 是论文里"同一份源码跨域"的实证负载。NuttX 侧它是 builtin app，
#   Linux 侧**不加 __NuttX__ 直接 gcc 编**就能跑（该文件除一个 #ifdef 外全是 POSIX）。
#   这个脚本把"编译 → 设优先级 → 绑核 → 跑 N 轮 → 打印摘要"固化下来，
#   便于 artifact 复现，也避免手敲 chrt/taskset 时把参数搞乱。
#
# 用法：
#   bash bm2_linux_run__Linux域跑BM2.sh [轮数=3] [周期数=100000] [CPU=3]
#
# 前置：root 权限（SCHED_FIFO + 绑核）；gcc 可用；源码路径见 SRC_C 覆盖。
# 注意：Linux 的 SCHED_FIFO 上限是 99，与 NuttX 的 PRIORITY=200 不可跨 OS 直接对齐，
#       两侧都取"各自最高可用档"。

set -u

ROUNDS="${1:-3}"
CYCLES="${2:-100000}"
CPU="${3:-3}"
PRIO="${RT_PRIO:-99}"

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_C="${SRC_C:-/root/kfusion-pi4/build/nuttx-build/apps/benchmarks/bm2pid/bm2pid_main.c}"
OUT_DIR="${OUT_DIR:-/root/bm2linux}"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

[ -f "$SRC_C" ] || die "找不到源码 $SRC_C（可用 SRC_C=... 覆盖）"
command -v gcc   >/dev/null || die "找不到 gcc"
command -v chrt  >/dev/null || die "找不到 chrt（util-linux）"
command -v taskset >/dev/null || die "找不到 taskset"

mkdir -p "$OUT_DIR"
BIN="$OUT_DIR/bm2pid_linux"

log "编译（不加 __NuttX__，即 Linux 版）"
# -lm 只是保险：BM2 刻意不用 sqrt，闭包保持干净
gcc -O2 -Wall -o "$BIN" "$SRC_C" -lm || die "编译失败"
log "二进制: $BIN ($(stat -c%s "$BIN") 字节)"

log "环境"
echo "  内核      : $(uname -r)"
echo "  hypervisor: $(jailhouse cell list 2>/dev/null | tail -n +2 | tr -s ' ' | tr '\n' ';' || echo '未启用')"
echo "  cmdline   : $(grep -o 'isolcpus=[^ ]*\|nohz_full=[^ ]*' /proc/cmdline | tr '\n' ' ' || true)"
echo "  CPU$CPU gov: $(cat /sys/devices/system/cpu/cpu$CPU/cpufreq/scaling_governor 2>/dev/null || echo '无 cpufreq 节点')"

# 预热：让首轮不承担缺页与冷缓存代价（BM2 没有 mlockall）
log "预热 2000 周期"
chrt -f "$PRIO" taskset -c "$CPU" "$BIN" -l 2000 >/dev/null 2>&1

for i in $(seq 1 "$ROUNDS"); do
  log "第 $i/$ROUNDS 轮（FIFO $PRIO, CPU$CPU, $CYCLES 周期）"
  chrt -f "$PRIO" taskset -c "$CPU" "$BIN" -l "$CYCLES" > "$OUT_DIR/run_linux_$i.log" 2>&1 \
    || die "第 $i 轮运行失败"
  grep -E '^# (Min|Max|exec_us)' "$OUT_DIR/run_linux_$i.log" | sed 's/^/  /'
done

echo
log "摘要（1 µs 桶；p50 用累积计数求）"
for i in $(seq 1 "$ROUNDS"); do
  # 注意：不要写 /^[0-9]{6}$/ —— mawk（Debian 默认 awk）不支持区间量词，
  # 会导致整张表解析为空（n=0）。用 length() 判断更可靠。
  awk -v tag="run$i" '
    length($1) == 6 && $1 + 0 >= 0 && $2 + 0 > 0 {
      b[$1 + 0] = $2 + 0; n += $2;
      if (nfirst == 0) { min = $1 + 0; nfirst = 1 }
      if ($1 + 0 < min) min = $1 + 0;
      if ($1 + 0 > max) max = $1 + 0;
    }
    END {
      if (n == 0) { printf "  %-6s 解析失败（日志里没有直方图？）\n", tag; exit }
      c = 0; p50 = "?"
      for (k in b) { order[++m] = k }
      for (x = 1; x <= m; x++)
        for (y = x + 1; y <= m; y++)
          if (order[x] + 0 > order[y] + 0) { t = order[x]; order[x] = order[y]; order[y] = t }
      for (x = 1; x <= m; x++) { c += b[order[x]]; if (p50 == "?" && c >= n / 2) p50 = order[x] + 0 }
      printf "  %-6s n=%d  min=%d  p50=%s  max=%d\n", tag, n, min, p50, max
    }' "$OUT_DIR/run_linux_$i.log"
done

echo
echo "原始日志: $OUT_DIR/run_linux_*.log"
echo "出图: python3 bm2_crossdomain_plot__跨域CDF对比.py out.png \"Linux:<日志>\" \"NuttX:<日志>\""
