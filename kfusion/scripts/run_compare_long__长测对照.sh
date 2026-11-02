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
# 隔离/取证失败默认 fail-closed（L4 不得带病继续）；ALLOW_ISO_FAIL=1 仅作逃生
ALLOW_ISO_FAIL="${ALLOW_ISO_FAIL:-0}"
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
  set +e
  sudo "$CYCLIC" -a "$CPU" -t 1 -p "$PRIO" -i "$INTERVAL_US" -m \
    -D "${SEG_MIN}m" -q -h 200 --histfile="$hist" \
    --json="$json" >"$log" 2>&1
  local rc=$?
  set -e
  sudo chown "$(id -u):$(id -g)" "$hist" "$log" "$json" 2>/dev/null || true
  if [ "$rc" -ne 0 ] || [ ! -s "$hist" ]; then
    echo "❌ cyclictest[$tag] 失败 rc=$rc — 见 $log"; exit 6
  fi
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
  # 禁止回落到历史 residual jitter.bin（会把旧数据当成本轮结果）
  sudo chown "$(id -u):$(id -g)" "$bin" 2>/dev/null || true
  [ -s "$bin" ] || { echo "❌ 无本轮 $bin（拒绝使用残留 jitter）"; exit 5; }
  cp -a "$bin" "$OUT/fused_jitter.bin"
  echo "    bin=$bin ($(wc -c <"$bin") bytes)"
}

# ---- 0) 隔离先于一切测量（P0-5）；失败默认中止 ----
echo "── [0] 隔离 setup + 取证（用户态/内核态共用）──"
[ -f "$ISO_SH" ] || { echo "❌ 缺 $ISO_SH"; exit 7; }
if ! bash "$ISO_SH" setup 2>"$OUT/iso_setup.err" | tee "$OUT/iso_setup.log" | tail -20; then
  if [ "$ALLOW_ISO_FAIL" = "1" ]; then
    echo "⚠ 隔离 setup 失败但 ALLOW_ISO_FAIL=1，继续（不得标 L4）"
  else
    echo "❌ 隔离 setup 失败（见 iso_setup.err）；L4 实验中止"; exit 7
  fi
fi
[ -f "$EVIDENCE_PY" ] || { echo "❌ 缺 $EVIDENCE_PY"; exit 7; }
if ! python3 "$EVIDENCE_PY" --out "$OUT/deployment_evidence_pre.json" 2>"$OUT/iso_evidence.err"; then
  if [ "$ALLOW_ISO_FAIL" = "1" ]; then
    echo "⚠ iso_evidence 失败但 ALLOW_ISO_FAIL=1"
  else
    echo "❌ 隔离取证失败（见 iso_evidence.err）"; exit 7
  fi
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

# ---- 出图（用最后一次 A 的 hist + 最后一次 B 的 bin；时长=段长）----
echo "── 出图 ──"
SEG_PLOT_SEC=$SEG_SEC
WALL_SEC=$((SEG_SEC * ${#ORDER[@]}))
if [ "$ABBA" = "1" ]; then ORDER_JSON='["A","B","B","A"]'; else ORDER_JSON='["A","B"]'; fi
python3 -c "
import json
json.dump({
  'duration_min_label': $DURATION_MIN,
  'seg_min': $SEG_MIN,
  'seg_sec': $SEG_SEC,
  'wall_sec': $WALL_SEC,
  'abba': $( [ "$ABBA" = 1 ] && echo True || echo False ),
  'order': json.loads('$ORDER_JSON'),
  'cpu': $CPU, 'prio': $PRIO, 'interval_us': $INTERVAL_US,
}, open('$OUT/run_meta.json','w'), indent=2, ensure_ascii=False)
print('wrote $OUT/run_meta.json wall_sec=$WALL_SEC seg_sec=$SEG_SEC')
"

python3 "$PLOT_COMPARE" \
  --hist "$OUT/userspace.hist" \
  --jitter-bin "$OUT/fused_jitter.bin" \
  --out-dir "$OUT" \
  --stamp "$STAMP" \
  --duration-s "$SEG_PLOT_SEC" \
  --period-us "$INTERVAL_US" \
  --cpu "$CPU" | tee "$OUT/plot_compare.log"

if [ -f "$PLOT_DUAL" ]; then
  python3 "$PLOT_DUAL" \
    --input-jitter-bin "$OUT/fused_jitter.bin" \
    --output "$OUT/fig_fused_dist_${STAMP}.png" \
    --latency-output "$OUT/fig_fused_latency_timeline_${STAMP}.png" \
    --label "fused ${SEG_MIN}min/seg (wall ${WALL_SEC}s)" \
    >"$OUT/plot_dual.log" 2>&1 || echo "⚠ dual plot 跳过（见 plot_dual.log）"
fi

ln -sfn "$OUT" "$OUT_ROOT/LAST_${DURATION_MIN}m"
echo
echo "✅ 完成 label=${DURATION_MIN}min ABBA=$ABBA seg=${SEG_MIN}min wall=${WALL_SEC}s → $OUT"
ls -la "$OUT"/fig_*.png "$OUT"/summary_*.json 2>/dev/null || true
