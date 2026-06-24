#!/bin/bash
# ============================================================================
# run_soak_cycletest__浸泡长测.sh — 安静浸泡测（无背景负载）
# ============================================================================
# 测量: soak — L2 隔离 + 1kHz fused cyclictest，测 best-case 长时抖动
# 用法:
#   bash scripts/deploy/run_soak_cycletest__浸泡长测.sh
#   DURATION_MIN=15 PLC_PROFILE=./profile_soak_l2_best__安静浸泡.env.sh bash run_soak_cycletest__浸泡长测.sh
# 加压测请用: run_stress_cycletest__加压长测.sh
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLOT_SCRIPT="$PROJECT_ROOT/src/plot_frequency_polygon__抖动绘图.py"
STRESS_LOAD_SCRIPT="$SCRIPT_DIR/../tune/rt_background_load__背景加压.sh"
RESULTS_PNG="$PROJECT_ROOT/results/${MEASURE_KIND:-soak}/png"
RESULTS_RAW="$PROJECT_ROOT/results/${MEASURE_KIND:-soak}/raw"
cd "$SCRIPT_DIR"

export PRE_IDLE_SEC="${PRE_IDLE_SEC:-90}"
export RING_EXPORT_PATH="${RING_EXPORT_PATH:-$PROJECT_ROOT/results/jitter.bin}"
export EXPORT_DECIM_MAX="${EXPORT_DECIM_MAX:-72000}"
export DECIM_STRIDE="${DECIM_STRIDE:-50}"
export MAX_UPTIME_SEC="${MAX_UPTIME_SEC:-0}"
export MEASURE_KIND="${MEASURE_KIND:-soak}"
export PLC_PROFILE="${PLC_PROFILE:-$SCRIPT_DIR/profiles/profile_soak_l2_best__安静浸泡.env.sh}"

# shellcheck source=env_setup__测量环境.sh
source "$SCRIPT_DIR/env_setup__测量环境.sh"

mkdir -p "$RESULTS_PNG" "$RESULTS_RAW"

SOAK_LOCK_FILE="${SOAK_LOCK_FILE:-/tmp/plc_soak_cycletest.lock}"
exec 8>"$SOAK_LOCK_FILE"
if ! flock -n 8; then
    echo "❌ 已有 soak 在运行（锁: ${SOAK_LOCK_FILE}）"
    echo "   若确认无其他测试: rm -f ${SOAK_LOCK_FILE}"
    exit 11
fi

DURATION_MIN="${DURATION_MIN:-15}"
DURATION_SEC=$((DURATION_MIN * 60))
TARGET_ABS_MAX_NS="${TARGET_ABS_MAX_NS:-5000}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TAG="${RUNNER_PROFILE:-fused_soak_l2}"
RAW_LOG="$RESULTS_RAW/${TAG}_${STAMP}_${DURATION_MIN}min_${MEASURE_KIND}.raw.log"
OUT_PNG="$RESULTS_PNG/${TAG}_${STAMP}_${DURATION_MIN}min_${MEASURE_KIND}.png"
LAT_PNG="${OUT_PNG%.png}_latency.png"
JITTER_BIN="${RING_EXPORT_PATH:-$PROJECT_ROOT/results/jitter.bin}"
STRESS_LOAD_STARTED=0

read_live_fused_stats() {
    local stats
    if [ ! -r /sys/kernel/debug/fused_stats ] 2>/dev/null; then
        stats=$(sudo -n cat /sys/kernel/debug/fused_stats 2>/dev/null || true)
    else
        stats=$(cat /sys/kernel/debug/fused_stats 2>/dev/null || true)
    fi
    if [ -z "$stats" ] || [ "$stats" = "idle" ]; then
        return 1
    fi
    printf '%s' "$stats"
}

reset_fused_measure_window() {
    local reset_path="/sys/kernel/debug/fused_stats_reset"
    if [ ! -e "$reset_path" ]; then
        echo "⚠️ 无 ${reset_path}（需重编 official_cycletest_mod.ko）"
        return 1
    fi
    if [ -w "$reset_path" ] 2>/dev/null; then
        echo reset_stats >"$reset_path"
    else
        echo reset_stats | sudo -n tee "$reset_path" >/dev/null
    fi
    echo "=== 测量窗口 stats 已清零（保留 jitter 补偿）==="
}

stress_load_stop() {
    if [ "$STRESS_LOAD_STARTED" = "1" ] && [ -f "$STRESS_LOAD_SCRIPT" ]; then
        bash "$STRESS_LOAD_SCRIPT" stop || true
        STRESS_LOAD_STARTED=0
    fi
}

cleanup() {
    stress_load_stop
    if [ -n "$SAVE_PRINTK" ]; then
        echo "$SAVE_PRINTK" | sudo -n tee /proc/sys/kernel/printk >/dev/null 2>&1 || true
    fi
    env_teardown_host
}
trap cleanup EXIT INT TERM

UPTIME=$(awk '{print int($1)}' /proc/uptime)
if [ "$MAX_UPTIME_SEC" -gt 0 ] && [ "$UPTIME" -gt "$MAX_UPTIME_SEC" ]; then
    echo "❌ uptime=${UPTIME}s > ${MAX_UPTIME_SEC}s，请先 reboot 或设 MAX_UPTIME_SEC=0 跳过"
    exit 10
fi
if [ "$MAX_UPTIME_SEC" -eq 0 ]; then
    echo "ℹ️ uptime=${UPTIME}s（已跳过 MAX_UPTIME 门禁）"
fi

if [ "$DURATION_MIN" -lt 15 ] 2>/dev/null; then
    echo "❌ 正式测量最短 15min（当前 DURATION_MIN=$DURATION_MIN）"
    echo "   调试请直接 bash ignite_official_cycletest__cyclictest主线.sh"
    exit 2
fi

echo "=== [${MEASURE_KIND}] ${TAG} 浸泡 ${DURATION_MIN}min | PRE_IDLE=${PRE_IDLE_SEC}s | iso=L${ISOLATION_LEVEL} | target<${TARGET_ABS_MAX_NS}ns ==="

if lsmod | grep -q '^official_cycletest_mod'; then
    bash ./safe_rmmod_official__cyclictest卸载.sh || true
    sleep 2
fi

env_setup_host > >(tee "${RAW_LOG%.raw.log}.env_setup.log") 2>&1 | tail -20 || true
env_mark_setup_done
env_report_probe_cpu_irq 2>&1 | head -25 || true

export FORCE_REBUILD_KERNEL_O="${FORCE_REBUILD_KERNEL_O:-0}"
export FOLLOW_DMESG=0
rm -f "$JITTER_BIN"
if [ "${SOAK_SKIP_KBUILD:-0}" = "1" ] && [ "${FORCE_KO_REBUILD:-0}" != "1" ] && \
   [ -f "$PROJECT_ROOT/test/official_cycletest_mod.ko" ]; then
    echo "=== 复用 test/official_cycletest_mod.ko（SOAK_SKIP_KBUILD，无 Kbuild）==="
else
    export IGNITE_BUILD_ONLY=1
    bash ./ignite_official_cycletest__cyclictest主线.sh
    export IGNITE_BUILD_ONLY=0
fi

env_pre_measure_idle "$PRE_IDLE_SEC"
env_pre_insmod_stabilize

export IGNITE_INSMOD_ONLY=1
bash ./ignite_official_cycletest__cyclictest主线.sh
unset IGNITE_INSMOD_ONLY

if ! lsmod | grep -q '^official_cycletest_mod'; then
    echo "❌ 模块未加载"
    exit 1
fi

SETTLE="${POST_INSMOD_SETTLE_SEC:-45}"
if [ "$SETTLE" -gt 0 ] 2>/dev/null; then
    echo "=== 模块加载后预热 ${SETTLE}s（不计入 ${DURATION_MIN}min 窗口）==="
    sleep "$SETTLE"
fi
reset_fused_measure_window || true

if [ "${STRESS_LOAD_ENABLE:-0}" = "1" ]; then
    bash "$STRESS_LOAD_SCRIPT" start
    STRESS_LOAD_STARTED=1
    echo "=== 背景加压运行中（housekeeping ${STRESS_LOAD_CPUS:-0-2}）==="
fi

echo "=== 浸泡计时 ${DURATION_MIN} min [${MEASURE_KIND}] ==="
sudo -n dmesg -c >/dev/null 2>&1 || true
if [ "${SOAK_SUPPRESS_PRINTK:-0}" = "1" ]; then
    SAVE_PRINTK="$(cat /proc/sys/kernel/printk 2>/dev/null || echo "4 4 1 7")"
    echo "=== 压低 kernel printk（SOAK_SUPPRESS_PRINTK）==="
    echo "3 3 3 3" | sudo -n tee /proc/sys/kernel/printk >/dev/null 2>&1 || true
fi

MONITOR_LOG="${RAW_LOG%.raw.log}.cpu3_monitor.log"
WATCHDOG_LOG="${RAW_LOG%.raw.log}.watchdog.log"
STRESS_LOG="${RAW_LOG%.raw.log}.stress_load.log"
PROBE="${JITTER_PROBE_CPU:-3}"
MONITOR_CPU3="${MONITOR_CPU3:-1}"
MODULE_WATCHDOG="${MODULE_WATCHDOG:-1}"
WATCHDOG_INTERVAL_SEC="${WATCHDOG_INTERVAL_SEC:-60}"
ISOLATE_REFRESH_SEC="${ISOLATE_REFRESH_SEC:-0}"
SAVE_PRINTK=""
MONITOR_PID=""
WATCHDOG_FAIL=0

if [ "$STRESS_LOAD_STARTED" = "1" ] && [ -f "${STRESS_LOAD_LOGFILE:-/tmp/plc_rt_stress_load.log}" ]; then
    cp "${STRESS_LOAD_LOGFILE:-/tmp/plc_rt_stress_load.log}" "$STRESS_LOG" 2>/dev/null || true
fi

if [ "$MONITOR_CPU3" = "1" ]; then
    echo "=== CPU${PROBE} 监控 → ${MONITOR_LOG} ==="
    (
        while true; do
            TS=$(date -Iseconds)
            IRQ=$(grep arch_timer /proc/interrupts 2>/dev/null | awk -v c="$((PROBE + 1))" '{print $c}')
            TASKS=$(ps -eLo pid,psr,comm 2>/dev/null | awk -v p="$PROBE" '$2==p {printf "%s:%s ", $1,$3}')
            echo "$TS irq_cpu${PROBE}=${IRQ:-na} tasks=${TASKS}" >> "$MONITOR_LOG"
            sleep 0.5
        done
    ) &
    MONITOR_PID=$!
fi

: > "$WATCHDOG_LOG"
echo "started $(date -Iseconds) measure_kind=${MEASURE_KIND} duration_min=${DURATION_MIN} stress=${STRESS_LOAD_ENABLE:-0}" >> "$WATCHDOG_LOG"

ELAPSED=0
while [ "$ELAPSED" -lt "$DURATION_SEC" ]; do
    sleep "$WATCHDOG_INTERVAL_SEC"
    ELAPSED=$((ELAPSED + WATCHDOG_INTERVAL_SEC))
    if [ "$ELAPSED" -gt "$DURATION_SEC" ]; then
        ELAPSED=$DURATION_SEC
    fi

    if [ "$MODULE_WATCHDOG" = "1" ]; then
        if ! lsmod | grep -q '^official_cycletest_mod'; then
            echo "FAIL $(date -Iseconds) module_unloaded elapsed=${ELAPSED}s" | tee -a "$WATCHDOG_LOG"
            WATCHDOG_FAIL=1
            break
        fi
    fi

    if [ "${STRESS_LOAD_ENABLE:-0}" = "1" ] && [ -f "$STRESS_LOAD_SCRIPT" ]; then
        if ! bash "$STRESS_LOAD_SCRIPT" status >/dev/null 2>&1; then
            echo "WARN $(date -Iseconds) stress_load_died elapsed=${ELAPSED}s" | tee -a "$WATCHDOG_LOG"
        fi
    fi

    BIN_INFO=""
    if LIVE_INFO="$(read_live_fused_stats)"; then
        BIN_INFO="$LIVE_INFO"
    elif [ -f "$JITTER_BIN" ] && [ -s "$JITTER_BIN" ] && [ "$(stat -c%s "$JITTER_BIN" 2>/dev/null || echo 0)" -gt 56 ]; then
        BIN_INFO=$(python3 - <<PY 2>/dev/null || true
import struct, os
p="${JITTER_BIN}"
with open(p,"rb") as f: h=f.read(56)
if len(h)<56: raise SystemExit(0)
magic,ver,cycles,min_ns,max_ns,samples,hb,lo,step=struct.unpack("<IIQqqIIqq",h)
abs_max=max(abs(min_ns),abs(max_ns))
print(f"cycles={cycles} abs_max_ns={abs_max} decim={samples} size={os.path.getsize(p)} stale_jitter_bin")
PY
)
    fi
    PCT=$((ELAPSED * 100 / DURATION_SEC))
    echo "OK $(date -Iseconds) elapsed=${ELAPSED}/${DURATION_SEC}s (${PCT}%) ${BIN_INFO}" | tee -a "$WATCHDOG_LOG"

    if [ "${ISOLATE_REFRESH_SEC:-0}" -gt 0 ] && \
       [ "$ELAPSED" -lt "$DURATION_SEC" ] && \
       [ $((ELAPSED % ISOLATE_REFRESH_SEC)) -lt "$WATCHDOG_INTERVAL_SEC" ]; then
        echo "    隔离 refresh @ ${ELAPSED}s..." >> "$WATCHDOG_LOG"
        bash "$SCRIPT_DIR/../tune/rt_host_isolate__CPU隔离.sh" refresh >> "$WATCHDOG_LOG" 2>&1 || true
    fi
done

if [ -n "${MONITOR_PID:-}" ]; then
    kill "$MONITOR_PID" 2>/dev/null || true
    wait "$MONITOR_PID" 2>/dev/null || true
fi

if [ "$WATCHDOG_FAIL" = "1" ]; then
    echo "❌ 浸泡中断：official_cycletest_mod 已卸载（见 ${WATCHDOG_LOG}）"
    bash ./safe_rmmod_official__cyclictest卸载.sh || true
    exit 3
fi

REM=$((DURATION_SEC - ELAPSED))
if [ "$REM" -gt 0 ]; then
    sleep "$REM"
fi

stress_load_stop
bash ./safe_rmmod_official__cyclictest卸载.sh || true
sudo -n dmesg > "${RAW_LOG}" 2>/dev/null || true

SUMMARY=$(grep -E 'JitterSummary:|FusedSummary:' "${RAW_LOG}" | tail -1 || true)
ABS_MAX=""
OUTLIERS=""

if [ -n "$SUMMARY" ]; then
    echo "$SUMMARY"
    ABS_MAX=$(echo "$SUMMARY" | sed -n 's/.*abs_max_ns=\([0-9-]*\).*/\1/p')
    if [ -z "$ABS_MAX" ]; then
        MIN_NS=$(echo "$SUMMARY" | sed -n 's/.*min_ns=\(-[0-9]*\).*/\1/p')
        MAX_NS=$(echo "$SUMMARY" | sed -n 's/.*max_ns=\([0-9]*\).*/\1/p')
        if [ -n "$MIN_NS" ] && [ -n "$MAX_NS" ]; then
            ABS_MAX=$MAX_NS
            if [ "${MIN_NS#-}" -gt "$ABS_MAX" ] 2>/dev/null; then
                ABS_MAX="${MIN_NS#-}"
            fi
        fi
    fi
    OUTLIERS=$(echo "$SUMMARY" | sed -n 's/.*outliers=\([0-9]*\).*/\1/p')
fi

if [ -f "$JITTER_BIN" ]; then
  echo "ℹ️ ring export: $JITTER_BIN"
  BIN_STATS=$(python3 - <<PY
import struct
p="${JITTER_BIN}"
with open(p,"rb") as f: h=f.read(56)
if len(h)<56: raise SystemExit(1)
magic,ver,cycles,min_ns,max_ns,samples,hb,lo,step=struct.unpack("<IIQqqIIqq",h)
abs_max=max(abs(min_ns),abs(max_ns))
print(f"cycles={cycles} abs_max_ns={abs_max} decim={samples} hist_bins={hb}")
PY
) || true
  if [ -n "${BIN_STATS:-}" ]; then
    echo "FusedBinSummary: ${BIN_STATS} source=jitter.bin"
    if [ -z "$ABS_MAX" ]; then
      ABS_MAX=$(echo "$BIN_STATS" | sed -n 's/.*abs_max_ns=\([0-9]*\).*/\1/p')
    fi
  fi
fi

if [ -z "$ABS_MAX" ] && [ -z "$SUMMARY" ]; then
    echo "❌ 无 JitterSummary/FusedSummary 且无有效 jitter.bin"
    exit 2
fi

echo "measure_kind=${MEASURE_KIND} abs_max_ns=${ABS_MAX} outliers=${OUTLIERS} target < ${TARGET_ABS_MAX_NS} ns"

PLOT_EXTRA=()
if [ -f "$JITTER_BIN" ]; then
    BIN_DECIM=$(python3 - <<PY 2>/dev/null || true
import struct
p="${JITTER_BIN}"
with open(p,"rb") as f: h=f.read(56)
if len(h)<56: raise SystemExit(0)
_,_,_,_,_,samples,_,_,_=struct.unpack("<IIQqqIIqq",h)
print(samples)
PY
)
    if [ "${BIN_DECIM:-0}" -gt 0 ] 2>/dev/null; then
        PLOT_EXTRA=(--input-jitter-bin "$JITTER_BIN")
    else
        echo "ℹ️ ring decim=0，出图仅用 dmesg/FusedSummary"
    fi
fi

PLOT_OK=0
if [ -f "$PLOT_SCRIPT" ]; then
    if python3 "$PLOT_SCRIPT" \
        "${PLOT_EXTRA[@]}" \
        --input-log "${RAW_LOG}" \
        --output "${OUT_PNG}" \
        --latency-output "${LAT_PNG}" \
        --bins 180 \
        --timeline-max-points 20000; then
        PLOT_OK=1
        echo "✅ 分布图: ${OUT_PNG}"
        echo "✅ 时序图: ${LAT_PNG}"
    else
        echo "⚠️ 出图跳过（无 decim 样本时可忽略）"
    fi
    echo "✅ 监控: ${MONITOR_LOG}"
    echo "✅ 看门狗: ${WATCHDOG_LOG}"
    [ -f "$STRESS_LOG" ] && echo "✅ 加压日志: ${STRESS_LOG}"
else
    echo "❌ 未找到 $PLOT_SCRIPT"
    exit 2
fi

if [ -n "$ABS_MAX" ] && [ "$ABS_MAX" -gt 0 ] && [ "$ABS_MAX" -lt "$TARGET_ABS_MAX_NS" ] 2>/dev/null; then
    echo "✅ PASS [${MEASURE_KIND}] abs_max_ns=${ABS_MAX} (< ${TARGET_ABS_MAX_NS})"
    exit 0
fi
if [ "$PLOT_OK" = "1" ] && [ -f "$OUT_PNG" ]; then
    echo "⚠️ 出图完成但 abs_max=${ABS_MAX} ns >= ${TARGET_ABS_MAX_NS} ns"
    exit 1
fi
echo "❌ FAIL [${MEASURE_KIND}] abs_max=${ABS_MAX:-?} ns"
exit 1
