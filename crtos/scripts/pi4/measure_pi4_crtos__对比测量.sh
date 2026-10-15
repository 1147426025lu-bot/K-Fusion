#!/bin/bash
# measure_pi4_crtos__对比测量.sh — 在 Pi4 上测量 cRTOS 实时域 jitter，输出与 PLCFusion 同 schema 的 JSON
# 用法: bash measure_pi4_crtos__对比测量.sh [duration_min=15]
# 环境变量: REALM(root_cell|inmate,默认 root_cell)  CPU(隔离CPU,默认 3)  STRESS(0|1)
#
# realm=root_cell: 在 root cell Linux 跑 cyclictest（pinned + isolation），作为
#   "PREEMPT_RT under Jailhouse partition" 对照点（立即可跑）。
# realm=inmate:    RT inmate 自测周期 jitter（需 inmate 侧导出，阶段 2；此处只留采集骨架）。
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 本脚本在 crtos/scripts/pi4/，到仓库根需回退三级
source "$SCRIPT_DIR/../../../kfusion/scripts/lib/repo_paths__仓库路径.sh" 2>/dev/null || true

DURATION_MIN="${1:-${DURATION_MIN:-15}}"
REALM="${REALM:-root_cell}"
CPU="${CPU:-3}"
STRESS="${STRESS:-0}"
STAMP="$(date +%Y%m%d_%H%M%S)"
# 结果目录：优先 CRTOS_ROOT，缺省回退到脚本相对路径（crtos/），避免 REPO_ROOT 为空时写到 /crtos
RESULTS="${CRTOS_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}/results"
mkdir -p "$RESULTS"
OUT="$RESULTS/pi4_crtos_${REALM}_${STAMP}.json"
RAW="$RESULTS/raw/pi4_crtos_${REALM}_${STAMP}.log"
mkdir -p "$(dirname "$RAW")"

echo "=== cRTOS Pi4 测量 (realm=$REALM, duration=${DURATION_MIN}min, cpu=$CPU) ==="

if [ "$REALM" = "inmate" ]; then
  # inmate 测量本质在「接串口的主机」侧驱动：inmate 直接写物理 mini-UART，且与 root cell
  # 共用同一根串口线，无法从 Pi 本地可靠读取。因此本分支解析「主机侧采集的 rtlat 日志」：
  #   - 设 INMATE_LOG=<日志文件> 直接解析；或
  #   - 未设置时打印主机侧采集命令。
  LOG="${INMATE_LOG:-}"
  if [ -z "$LOG" ] || [ ! -f "$LOG" ]; then
    echo "需要 rtlat 串口输出日志。请先在「接串口的主机」上采集：" >&2
    echo "  Windows:    powershell -File measure_inmate_serial__串口测inmate.ps1 -Port COM11 -IntervalUs 1000 -Loops 60000" >&2
    echo "  Linux/macOS:bash measure_inmate_serial__串口测inmate.sh /dev/ttyUSB0 1000 60000" >&2
    echo "然后: INMATE_LOG=<上面产生的日志> REALM=inmate bash $0 ${DURATION_MIN}" >&2
    exit 2
  fi
  LINE="$(grep -E 'rtlat RESULT' "$LOG" | tail -1 || true)"
  [ -n "$LINE" ] || { echo "FAIL: 日志中无 'rtlat RESULT' 行: $LOG" >&2; exit 1; }
  LOOPS="$(echo "$LINE" | sed -n 's/.*loops=\([0-9]*\).*/\1/p')"
  MINUS="$(echo "$LINE" | sed -n 's/.*min=\([0-9]*\)us.*/\1/p')"
  AVGUS="$(echo "$LINE" | sed -n 's/.*avg=\([0-9]*\)us.*/\1/p')"
  MAXUS="$(echo "$LINE" | sed -n 's/.*max=\([0-9]*\)us.*/\1/p')"
  OVR="$(echo "$LINE" | sed -n 's/.*over200us=\([0-9]*\).*/\1/p')"
  echo "解析: loops=${LOOPS:-?} min=${MINUS:-?}us avg=${AVGUS:-?}us max=${MAXUS:-?}us over200us=${OVR:-?}"
  cp -f "$LOG" "$RAW" 2>/dev/null || true
  python3 - "$OUT" "$RAW" "${LOOPS:-0}" "${MINUS:-0}" "${AVGUS:-0}" "${MAXUS:-0}" "${OVR:-0}" "$DURATION_MIN" "$STAMP" "$REALM" <<'PY'
import json, sys
out, raw, loops, mn, av, mx, ovr, dur, stamp, realm = sys.argv[1:11]
def us(x):
    try: return int(x) * 1000
    except Exception: return 0
data = {
    "schema_version": 1,
    "test": "pi4_crtos_jitter",
    "stamp": stamp,
    "started": stamp,
    "duration_min": int(dur),
    "realm": realm,
    "tool": "rtlat (NuttX inmate, SCHED_TICKLESS + USEC_PER_TICK=1)",
    "results": [{
        "realm": realm,
        "loops": int(loops or 0),
        "min_ns": us(mn),
        "avg_ns": us(av),
        "abs_max_ns": us(mx),
        "over_200us": int(ovr or 0),
        "raw_log": raw,
        "stamp": stamp,
    }],
}
json.dump(data, open(out, "w"), indent=2)
print("wrote", out)
PY
  echo ""
  echo "完成。JSON: $OUT"
  exit 0
fi

# --- root_cell: cyclictest ---
if ! command -v cyclictest >/dev/null 2>&1; then
  echo "缺 cyclictest（apt install rt-tests）；先装再测。" >&2
  exit 1
fi

echo "运行 cyclictest（pinned cpu=$CPU, ${DURATION_MIN}min）..."
# -m 锁内存, -Sp<cpu> 指定 CPU, -i<us> 周期, -l 无限, -p 优先级
timeout "$((DURATION_MIN*60+30))" \
  cyclictest --mlockall --smp -Sp"$CPU" -i1000 -p90 -l100000000 \
  -h400 --histfile="$RAW" > "$RAW.stdout" 2>&1 || true

# 解析 abs_max（cyclictest 末行 Max 列）
ABS_MAX="$(grep -E '^T:' "$RAW.stdout" | awk '{print $7}' | sort -n | tail -1)"
ABS_MAX="${ABS_MAX:-0}"
echo "abs_max_ns(解析)=$ABS_MAX"

# 生成与 PLCFusion isolation_ab_results 兼容的 JSON
python3 - "$OUT" "$RAW" "$ABS_MAX" "$DURATION_MIN" "$STAMP" "$REALM" "$STRESS" <<'PY'
import json, sys
out, raw, amax, dur, stamp, realm, stress = sys.argv[1:8]
data = {
    "schema_version": 1,
    "test": "pi4_crtos_jitter",
    "stamp": stamp,
    "started": stamp,
    "duration_min": int(dur),
    "realm": realm,
    "stress": int(stress),
    "results": [{
        "realm": realm,
        "abs_max_ns": int(amax),
        "exit_code": 0,
        "raw_log": raw,
        "png": "",
        "stamp": stamp,
    }],
}
with open(out, "w") as f:
    json.dump(data, f, indent=2)
print("wrote", out)
PY

echo ""
echo "完成。JSON: $OUT"
echo "后续: 用 kfusion/scripts/paper/paper_summarize_results__论文结果汇总.py 的字段约定汇总到论文 Table 3。"
