#!/usr/bin/env bash
# run_compare_long__长测对照.sh — 用户态 cyclictest + fused .ko 长测并出图
#
# 用法:
#   DURATION_MIN=15 bash kfusion/scripts/run_compare_long__长测对照.sh
#   DURATION_MIN=60 ABBA=1 bash kfusion/scripts/run_compare_long__长测对照.sh
#
# 口径:
#   · 用户态: cyclictest 唤醒延迟（CPU3 / FIFO99 / 1kHz）
#   · fused : official_cycletest_mod 周期抖动（signed actual−nominal）
#   · 图: latency summary / jitter hist / fused timeseries
#
# P0-5：先隔离，再测。默认顺序 A→B；ABBA=1 时跑 A→B→B→A（每段 DURATION/2）。
set -euo pipefail

KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/repo_paths__仓库路径.sh
source "$KF/scripts/lib/repo_paths__仓库路径.sh"

DURATION_MIN="${DURATION_MIN:-15}"
CPU="${CPU:-3}"
PRIO="${PRIO:-99}"
INTERVAL_US="${INTERVAL_US:-1000}"
ABBA="${ABBA:-0}"
OUT_ROOT="${OUT_ROOT:-$RESULTS_ROOT/compare_long}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TAG="${DURATION_MIN}m_${STAMP}"
OUT="$OUT_ROOT/$TAG"
CYCLIC="$KF/test/rt-tests/cyclictest"
PLOT_COMPARE="$KF/scripts/plot_compare_us_vs_fused__对照出图.py"
PLOT_DUAL="$KF/src/plot_frequency_polygon__抖动绘图.py"
DEPLOY="$KF/scripts/deploy"
ISO_SH="$KF/scripts/tune/rt_host_isolate__CPU隔离.sh"
KO="$KF/test/official_cycletest_mod.ko"
EVIDENCE_PY="$KF/scripts/tune/iso_evidence__隔离取证.py"

mkdir -p "$OUT"
echo "══ compare long ${DURATION_MIN}min  stamp=$STAMP  ABBA=$ABBA  out=$OUT ══"

[ -x "$CYCLIC" ] || { echo "❌ 缺 $CYCLIC"; exit 2; }
[ -f "$KO" ] || { echo "❌ 缺 $KO"; exit 2; }

# 段时长：ABBA 时四段各 DURATION/2；否则 A/B 各一整段
if [ "$ABBA" = "1" ]; then
  SEG_MIN=$(( (DURATION_MIN + 1) / 2 ))
  [ "$SEG_MIN" -ge 1 ] || SEG_MIN=1
  ORDER=(A B B A)
else
  SEG_MIN=$DURATION_MIN
  ORDER=(A B)
fi
SEG_SEC=$((SEG_MIN * 60))

run_userspace() {
  local tag="$1"  # e.g. A1 / A2
  local hist="$OUT/userspace_${tag}.hist"
  local log="$OUT/userspace_${tag}.log"
  local json="$OUT/userspace_${tag}.json"
  echo "── userspace[$tag] cyclictest ${SEG_MIN}min ──"
  sudo "$CYCLIC" -a "$CPU" -t 1 -p "$PRIO" -i "$INTERVAL_US" -m \
    -D "${SEG_MIN}m" -q -h 200 --histfile="$hist" \
    --json="$json" >"$log" 2>&1 || {
    echo "⚠ cyclictest[$tag] exit=$? — 见 $log"
  }
  sudo chown "$(id -u):$(id -g)" "$hist" "$log" "$json" 2>/dev/null || true
  # 主摘要用最后一次 A；第一次也保留
  cp -a "$hist" "$OUT/userspace.hist"
  cp -a "$log" "$OUT/userspace.log"
  cp -a "$json" "$OUT/userspace.json" 2>/dev/null || true
  echo "    hist=$hist"
}

run_fused() {
  local tag="$1"
  local bin="$OUT/fused_jitter_${tag}.bin"
  echo "── fused[$tag] official_cycletest ${SEG_MIN}min ──"
  export RESULTS_ROOT PLCFUSION_ROOT="$KF"
  export MEASURE_KIND=soak
  export POST_INSMOD_SETTLE_SEC="${POST_INSMOD_SETTLE_SEC:-15}"
  export SOAK_SKIP_KBUILD="${SOAK_SKIP_KBUILD:-1}"
  export FORCE_KO_REBUILD=0
  export FUSED_RINGBUF_ENABLE=1
  export DECIM_STRIDE=1
  export EXPORT_DECIM_MAX=4000000
  export RING_EXPORT_PATH="$bin"
  export PLC_PROFILE="${PLC_PROFILE:-$DEPLOY/profiles/profile_soak_l4__深度隔离.env.sh}"
  export RING_EXPORT_PATH="$bin"

  cd "$DEPLOY"
  if lsmod | grep -q '^official_cycletest_mod'; then
    bash ./safe_rmmod_official__cyclictest卸载.sh || true
    sleep 1
  fi
  export IGNITE_INSMOD_ONLY=1 FOLLOW_DMESG=0
  set +e
  bash ./ignite_official_cycletest__cyclictest主线.sh >"$OUT/fused_insmod_${tag}.log" 2>&1
  set -e
  if ! lsmod | grep -q '^official_cycletest_mod'; then
    echo "❌ fused[$tag] 模块未加载"; tail -40 "$OUT/fused_insmod_${tag}.log"; exit 3
  fi
  sleep "$POST_INSMOD_SETTLE_SEC"
  if [ -e /sys/kernel/debug/fused_stats_reset ]; then
    echo reset_stats | sudo tee /sys/kernel/debug/fused_stats_reset >/dev/null
  fi

  local elapsed=0
  while [ "$elapsed" -lt "$SEG_SEC" ]; do
    sleep 60
    elapsed=$((elapsed + 60))
    [ "$elapsed" -gt "$SEG_SEC" ] && elapsed=$SEG_SEC
    STATS="$(sudo cat /sys/kernel/debug/fused_stats 2>/dev/null || echo missing)"
    echo "    $(date +%H:%M:%S) [$tag] ${elapsed}/${SEG_SEC}s  $STATS" | tee -a "$OUT/fused_progress.log"
    lsmod | grep -q '^official_cycletest_mod' || { echo "❌ 模块中途卸载"; exit 4; }
  done

  sudo dmesg >"$OUT/fused_${tag}.dmesg" 2>/dev/null || true
  sudo cat /sys/kernel/debug/fused_stats >"$OUT/fused_${tag}.stats" 2>/dev/null || true
  bash ./safe_rmmod_official__cyclictest卸载.sh || true
  sleep 1
  if [ ! -s "$bin" ]; then
    for c in "$RESULTS_ROOT/jitter.bin" /home/pi/kfusion_results/jitter.bin; do
      [ -s "$c" ] && cp -a "$c" "$bin" && break
    done
  fi
  sudo chown "$(id -u):$(id -g)" "$bin" 2>/dev/null || true
  [ -s "$bin" ] || { echo "❌ 无 $bin"; exit 5; }
  cp -a "$bin" "$OUT/fused_jitter.bin"
  echo "    bin=$bin ($(wc -c <"$bin") bytes)"
}

# ---- 0) 隔离先于一切测量（P0-5）----
echo "── [0] 隔离 setup + 取证（用户态/内核态共用）──"
if [ -f "$ISO_SH" ]; then
  bash "$ISO_SH" setup 2>"$OUT/iso_setup.err" | tee "$OUT/iso_setup.log" | tail -20 || true
else
  echo "⚠ 无 $ISO_SH，跳过隔离 setup"
fi
if [ -f "$EVIDENCE_PY" ]; then
  python3 "$EVIDENCE_PY" --out "$OUT/deployment_evidence_pre.json" 2>"$OUT/iso_evidence.err" \
    || echo "⚠ iso_evidence 失败（见 iso_evidence.err）"
fi

# ---- 测量序列 ----
A_N=0
B_N=0
for leg in "${ORDER[@]}"; do
  if [ "$leg" = "A" ]; then
    A_N=$((A_N + 1))
    run_userspace "A${A_N}"
  else
    B_N=$((B_N + 1))
    run_fused "B${B_N}"
  fi
done

# ---- 收尾取证 + teardown ----
if [ -f "$EVIDENCE_PY" ]; then
  python3 "$EVIDENCE_PY" --out "$OUT/deployment_evidence_post.json" 2>>"$OUT/iso_evidence.err" || true
fi
if [ -f "$ISO_SH" ]; then
  bash "$ISO_SH" teardown 2>"$OUT/iso_teardown.err" || true
fi

# ---- 出图（用最后一次 A 的 hist + 最后一次 B 的 bin）----
echo "── 出图 ──"
DURATION_SEC=$((DURATION_MIN * 60))
python3 "$PLOT_COMPARE" \
  --hist "$OUT/userspace.hist" \
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

ln -sfn "$OUT" "$OUT_ROOT/LAST_${DURATION_MIN}m"
echo
echo "✅ 完成 ${DURATION_MIN}min ABBA=$ABBA → $OUT"
ls -la "$OUT"/fig_*.png "$OUT"/summary_*.json 2>/dev/null || true
