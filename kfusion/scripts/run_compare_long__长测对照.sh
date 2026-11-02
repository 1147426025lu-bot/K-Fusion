#!/usr/bin/env bash
# run_compare_long__长测对照.sh — 用户态 cyclictest + fused .ko 长测并出图
#
# 用法:
#   DURATION_MIN=15 bash kfusion/scripts/run_compare_long__长测对照.sh
#   DURATION_MIN=60 bash kfusion/scripts/run_compare_long__长测对照.sh
#
# 口径（与 30s 短对照一致）:
#   · 用户态: cyclictest 唤醒延迟（CPU3 / FIFO99 / 1kHz）
#   · fused : official_cycletest_mod 周期抖动（signed actual−nominal）
#   · 图: latency summary / jitter hist / fused timeseries
set -euo pipefail

KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/repo_paths__仓库路径.sh
source "$KF/scripts/lib/repo_paths__仓库路径.sh"

DURATION_MIN="${DURATION_MIN:-15}"
DURATION_SEC=$((DURATION_MIN * 60))
CPU="${CPU:-3}"
PRIO="${PRIO:-99}"
INTERVAL_US="${INTERVAL_US:-1000}"
OUT_ROOT="${OUT_ROOT:-$RESULTS_ROOT/compare_long}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TAG="${DURATION_MIN}m_${STAMP}"
OUT="$OUT_ROOT/$TAG"
CYCLIC="$KF/test/rt-tests/cyclictest"
PLOT_COMPARE="$KF/scripts/plot_compare_us_vs_fused__对照出图.py"
PLOT_DUAL="$KF/src/plot_frequency_polygon__抖动绘图.py"
DEPLOY="$KF/scripts/deploy"
KO="$KF/test/official_cycletest_mod.ko"

mkdir -p "$OUT"
echo "══ compare long ${DURATION_MIN}min  stamp=$STAMP  out=$OUT ══"

[ -x "$CYCLIC" ] || { echo "❌ 缺 $CYCLIC"; exit 2; }
[ -f "$KO" ] || { echo "❌ 缺 $KO"; exit 2; }

# ---- 1) userspace ----
echo "── [1/3] userspace cyclictest ${DURATION_MIN}min ──"
US_HIST="$OUT/userspace.hist"
US_LOG="$OUT/userspace.log"
US_JSON="$OUT/userspace.json"
sudo "$CYCLIC" -a "$CPU" -t 1 -p "$PRIO" -i "$INTERVAL_US" -m \
  -D "${DURATION_MIN}m" -q -h 200 --histfile="$US_HIST" \
  --json="$US_JSON" >"$US_LOG" 2>&1 || {
  echo "⚠ cyclictest exit=$? — 见 $US_LOG"
}
# histfile may be root-owned
sudo chown "$(id -u):$(id -g)" "$US_HIST" "$US_LOG" "$US_JSON" 2>/dev/null || true
echo "    hist=$US_HIST"

# ---- 2) fused soak (quiet + L4 hooks if available) ----
echo "── [2/3] fused official_cycletest ${DURATION_MIN}min ──"
export RESULTS_ROOT
export PLCFUSION_ROOT="$KF"
export MEASURE_KIND=soak
export DURATION_MIN
export PRE_IDLE_SEC="${PRE_IDLE_SEC:-30}"
export POST_INSMOD_SETTLE_SEC="${POST_INSMOD_SETTLE_SEC:-20}"
export SOAK_SKIP_KBUILD="${SOAK_SKIP_KBUILD:-1}"
export FORCE_KO_REBUILD=0
export MAX_UPTIME_SEC=0
export FUSED_RINGBUF_ENABLE=1
export DECIM_STRIDE=1
export EXPORT_DECIM_MAX=4000000
export RING_EXPORT_PATH="$OUT/fused_jitter.bin"
export TARGET_ABS_MAX_NS="${TARGET_ABS_MAX_NS:-100000000}"  # 出图优先，不因阈值失败
export PLC_PROFILE="${PLC_PROFILE:-$DEPLOY/profiles/profile_soak_l4__深度隔离.env.sh}"
# profile 会覆盖 RING_EXPORT_PATH — 再钉一次
export RING_EXPORT_PATH="$OUT/fused_jitter.bin"

# 隔离环境尽量拉起；失败不阻断
if [ -f "$DEPLOY/../tune/rt_host_isolate__CPU隔离.sh" ]; then
  bash "$DEPLOY/../tune/rt_host_isolate__CPU隔离.sh" setup 2>"$OUT/iso_setup.err" | tee "$OUT/iso_setup.log" | tail -15 || true
fi

cd "$DEPLOY"
if lsmod | grep -q '^official_cycletest_mod'; then
  bash ./safe_rmmod_official__cyclictest卸载.sh || true
  sleep 1
fi

# 直接 insmod（跳过完整 soak 框架的 flock/监控自扰；需要 ring 导出时走 ignite）
export IGNITE_INSMOD_ONLY=1
export FOLLOW_DMESG=0
# 主线 ignite 读 RING / profile
set +e
bash ./ignite_official_cycletest__cyclictest主线.sh >"$OUT/fused_insmod.log" 2>&1
INS=$?
set -e
if ! lsmod | grep -q '^official_cycletest_mod'; then
  echo "❌ fused 模块未加载，见 $OUT/fused_insmod.log"
  tail -40 "$OUT/fused_insmod.log"
  exit 3
fi

# 绑核 / 参数：从 debugfs 重置窗口
sleep "$POST_INSMOD_SETTLE_SEC"
if [ -e /sys/kernel/debug/fused_stats_reset ]; then
  echo reset_stats | sudo tee /sys/kernel/debug/fused_stats_reset >/dev/null
fi

echo "    soaking ${DURATION_MIN}min..."
ELAPSED=0
while [ "$ELAPSED" -lt "$DURATION_SEC" ]; do
  sleep 60
  ELAPSED=$((ELAPSED + 60))
  if [ "$ELAPSED" -gt "$DURATION_SEC" ]; then ELAPSED=$DURATION_SEC; fi
  STATS="$(sudo cat /sys/kernel/debug/fused_stats 2>/dev/null || echo missing)"
  echo "    $(date +%H:%M:%S) ${ELAPSED}/${DURATION_SEC}s  $STATS" | tee -a "$OUT/fused_progress.log"
  if ! lsmod | grep -q '^official_cycletest_mod'; then
    echo "❌ 模块中途卸载"; exit 4
  fi
done

sudo dmesg >"$OUT/fused.dmesg" 2>/dev/null || true
sudo cat /sys/kernel/debug/fused_stats >"$OUT/fused.stats" 2>/dev/null || true
# ring export: module may write on rmmod; also try debugfs dump if present
bash ./safe_rmmod_official__cyclictest卸载.sh || true
sleep 1
# ignite/profile 常把 bin 写到 RING_EXPORT_PATH；若仍空则找默认
if [ ! -s "$OUT/fused_jitter.bin" ]; then
  for c in "$RESULTS_ROOT/jitter.bin" /home/pi/kfusion_results/jitter.bin; do
    if [ -s "$c" ]; then
      cp -a "$c" "$OUT/fused_jitter.bin"
      break
    fi
  done
fi
sudo chown "$(id -u):$(id -g)" "$OUT/fused_jitter.bin" 2>/dev/null || true
[ -s "$OUT/fused_jitter.bin" ] || { echo "❌ 无 fused_jitter.bin"; ls -la "$OUT"; exit 5; }
echo "    bin=$OUT/fused_jitter.bin ($(wc -c <"$OUT/fused_jitter.bin") bytes)"

if [ -f "$DEPLOY/../tune/rt_host_isolate__CPU隔离.sh" ]; then
  bash "$DEPLOY/../tune/rt_host_isolate__CPU隔离.sh" teardown 2>"$OUT/iso_teardown.err" || true
fi

# ---- 3) plots ----
echo "── [3/3] 出图 ──"
python3 "$PLOT_COMPARE" \
  --hist "$US_HIST" \
  --jitter-bin "$OUT/fused_jitter.bin" \
  --out-dir "$OUT" \
  --stamp "$STAMP" \
  --duration-s "$DURATION_SEC" \
  --period-us "$INTERVAL_US" \
  --cpu "$CPU" | tee "$OUT/plot_compare.log"

if [ -f "$PLOT_DUAL" ]; then
  python3 "$PLOT_DUAL" \
    --input-jitter-bin "$OUT/fused_jitter.bin" \
    --output "$OUT/fig_fused_dist_${STAMP}.png" \
    --latency-output "$OUT/fig_fused_latency_timeline_${STAMP}.png" \
    --label "fused ${DURATION_MIN}min" \
    >"$OUT/plot_dual.log" 2>&1 || echo "⚠ dual plot 跳过（见 plot_dual.log）"
fi

# 方便汇总
ln -sfn "$OUT" "$OUT_ROOT/LAST_${DURATION_MIN}m"
echo
echo "✅ 完成 ${DURATION_MIN}min → $OUT"
ls -la "$OUT"/fig_*.png "$OUT"/summary_*.json 2>/dev/null
