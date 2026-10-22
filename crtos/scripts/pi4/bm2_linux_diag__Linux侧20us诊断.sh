#!/usr/bin/env bash
# 诊断 Linux 侧 BM2 那个「恒定的 20 µs」到底从哪来
#
# 背景：BM2 在 NuttX cell 里 p50 = 6 µs，在 Linux root cell 里 p50 = 20 µs，
#       而且 Linux 的 CDF 在 20 µs 处**陡升**（67–71% 落在同一个 1 µs 桶），
#       不是从 0 缓慢爬升 —— 随机调度会给平滑分布，陡升更像**固定唤醒延迟**。
#
# 这个脚本把四个候选原因分开测：
#   A) 是不是 CPU 的问题（换 CPU 0/2/3 看 p50 是否都 20）
#   B) 是不是 BM2 特有（用 cyclictest 同窗口对照，看它的中位是多少）
#   C) 是不是中断/softirq 落到这个核（读 /proc/interrupts、看 ksoftirqd）
#   D) 是不是和其他核共享 L2 的代价（vmem/stream 施加在别的核上时 BM2 的 p50 变不变）
#
# 用法：bash bm2_linux_diag__Linux侧20us诊断.sh
# 前置：root；gcc；cyclictest（rt-tests）；可选 stress-ng
# 产物：/root/bm2linux/diag/*.log + 终端结论表

set -u

OUT_DIR="${OUT_DIR:-/root/bm2linux/diag}"
SRC_C="${SRC_C:-/root/kfusion-pi4/build/nuttx-build/apps/benchmarks/bm2pid/bm2pid_main.c}"
CYCLES="${CYCLES:-20000}"          # 20 s，够看 p50
PRIO="${RT_PRIO:-99}"

log() { printf '\n\033[1;36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "$OUT_DIR"
BIN="$OUT_DIR/bm2pid_diag"

log "0) 准备"
[ -f "$SRC_C" ] || die "找不到 $SRC_C（SRC_C= 覆盖）"
gcc -O2 -o "$BIN" "$SRC_C" -lm || die "编译失败"
command -v cyclictest >/dev/null || echo "  ⚠ 没有 cyclictest，B 项会跳过"
echo "  内核: $(uname -r)"
echo "  cmdline 隔离参数: $(grep -oE 'isolcpus=[^ ]*|nohz_full=[^ ]*|rcu_nocbs=[^ ]*' /proc/cmdline | tr '\n' ' ' || echo '（无）')"

# 统一的 p50 提取（mawk 不支持 {6} 区间量词，用 length()）
p50of() {
  awk '
    length($1) == 6 && $2 + 0 > 0 { b[$1 + 0] = $2 + 0; n += $2 }
    END {
      if (n == 0) { print "?"; exit }
      c = 0
      for (k in b) order[++m] = k
      for (x = 1; x <= m; x++) for (y = x + 1; y <= m; y++)
        if (order[x] + 0 > order[y] + 0) { t = order[x]; order[x] = order[y]; order[y] = t }
      for (x = 1; x <= m; x++) { c += b[order[x]]; if (c >= n / 2) { print order[x] + 0; exit } }
    }' "$1"
}

run_bm2() {  # $1=cpu  $2=tag
  chrt -f "$PRIO" taskset -c "$1" "$BIN" -l "$CYCLES" > "$OUT_DIR/$2.log" 2>&1
  printf "  %-16s p50=%-4s max=%-5s %s\n" "$2" \
    "$(p50of "$OUT_DIR/$2.log")" \
    "$(grep -oE '^# Max Latencies: 0*([0-9]+)' "$OUT_DIR/$2.log" | grep -oE '[0-9]+$')" \
    "$(grep -oE 'exec_us: avg=[0-9.]+' "$OUT_DIR/$2.log")"
}

log "A) 换 CPU：p50 是否都是 20 µs？"
echo "  （若各核都是 20 µs → 与核无关，是唤醒路径的固有延迟）"
chrt -f "$PRIO" taskset -c 3 "$BIN" -l 2000 >/dev/null 2>&1   # 预热
for c in 0 2 3; do run_bm2 "$c" "cpu$c"; done

log "B) 同窗口 cyclictest 对照：它的 p50 是多少？"
if command -v cyclictest >/dev/null; then
  # -h 与 BM2 同为 1 µs 桶；-D 秒数与 BM2 周期数对齐
  SECS=$(( CYCLES / 1000 ))
  chrt -f "$PRIO" taskset -c 3 cyclictest -t 1 -q -p "$PRIO" -i 1000 \
    -D "$SECS" -h 200 --histfile="$OUT_DIR/cyc_hist.txt" > "$OUT_DIR/cyc.out" 2>&1
  if [ -f "$OUT_DIR/cyc_hist.txt" ]; then
    printf "  %-16s p50=%-4s （cyclictest %s s）\n" "cyclictest" "$(p50of "$OUT_DIR/cyc_hist.txt")" "$SECS"
  else
    echo "  ⚠ cyclictest 未产出 histfile"
  fi
  echo "  判读：若 cyclictest 的 p50 也是 ~20 µs → 这是 Linux 的固有唤醒延迟，不是 BM2 的问题；"
  echo "        若 cyclictest 只有 1–2 µs → 说明 20 µs 与 BM2 的 deadline 记账方式或代码路径有关。"
else
  echo "  （跳过：未安装 cyclictest）"
fi

log "C) 中断与 softirq 是否压在这个核上"
echo "  --- CPU3 的 ksoftirqd / migration / rcuc 线程 ---"
ps -eo pid,psr,pcpu,comm 2>/dev/null | grep -E "ksoftirqd/3|migration/3|rcuc/3|irq/" | head -6
echo "  --- CPU3 上的中断计数（前 8 个） ---"
awk 'NR>1 && NF>=2 { irq=$1; for (i=2; i<=NF; i++) if (i-2 == 3) { if ($i+0 > 0) print "    irq " irq " cpu3=" $i } }' /proc/interrupts 2>/dev/null | head -8
echo "  判读：若 CPU3 上有周期性中断（尤其 mmc/timer/IPI），20 µs 可能来自中断处理。"

log "D) 其他核的内存压力是否影响这台（共享 L2）"
if command -v stress-ng >/dev/null; then
  echo "  施加 stream 负载到 CPU 0,2（不碰 3）20 s，同时跑 BM2@CPU3"
  stress-ng --stream 2 --taskset 0,2 --timeout 30s >/dev/null 2>&1 &
  SPID=$!
  sleep 2
  run_bm2 3 "cpu3_with_stream"
  kill "$SPID" 2>/dev/null
  echo "  判读：与 A 的 cpu3 相比，p50 明显抬高 → 共享内存带宽是主要干扰源（与五档负载结论一致）。"
else
  echo "  （跳过：未安装 stress-ng）"
fi

log "结论表"
echo "  各项 p50（µs）:"
for f in cpu0 cpu2 cpu3 cyc_hist cpu3_with_stream; do
  [ -f "$OUT_DIR/$f.log" ] || [ -f "$OUT_DIR/$f.txt" ] || continue
  t="$OUT_DIR/$f.log"; [ -f "$t" ] || t="$OUT_DIR/$f.txt"
  printf "    %-18s %s\n" "$f" "$(p50of "$t")"
done
echo
echo "  原始日志: $OUT_DIR/"
