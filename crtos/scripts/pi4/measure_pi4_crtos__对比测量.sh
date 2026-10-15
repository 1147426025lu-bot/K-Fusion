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
source "$SCRIPT_DIR/../../kfusion/scripts/lib/repo_paths__仓库路径.sh" 2>/dev/null || true

DURATION_MIN="${1:-${DURATION_MIN:-15}}"
REALM="${REALM:-root_cell}"
CPU="${CPU:-3}"
STRESS="${STRESS:-0}"
STAMP="$(date +%Y%m%d_%H%M%S)"
RESULTS="${CRTOS_ROOT:-$REPO_ROOT/crtos}/results"
mkdir -p "$RESULTS"
OUT="$RESULTS/pi4_crtos_${REALM}_${STAMP}.json"
RAW="$RESULTS/raw/pi4_crtos_${REALM}_${STAMP}.log"
mkdir -p "$(dirname "$RAW")"

echo "=== cRTOS Pi4 测量 (realm=$REALM, duration=${DURATION_MIN}min, cpu=$CPU) ==="

if [ "$REALM" = "inmate" ]; then
  echo "inmate 测量需要 inmate 侧自采样 + 导出（阶段 2）。" >&2
  echo "当前仅支持 realm=root_cell 的 cyclictest 兜底数据点。" >&2
  echo "请在 RT inmate 就绪后，把其串口/IVSHMEM 导出的 jitter 直方图解析填入本脚本的 inmate 分支。" >&2
  exit 2
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
