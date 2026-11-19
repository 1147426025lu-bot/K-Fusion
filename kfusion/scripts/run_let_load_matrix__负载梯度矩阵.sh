#!/usr/bin/env bash
# 负载梯度矩阵：在隔离核 CPU3 之外持续施加指定负载，再跑 thread/irq 两臂读写抖动实验。
# 修复原 run_let_jitter_exp MODE=load 的 bug：原实现 --timeout=DURATION_SEC 只覆盖第一场，
# 而实验总时长 = REPS×2臂×DURATION。本脚本用外部 stress-ng 覆盖全程，实验脚本走 idle 模式。
#
# 用法：
#   LOAD_KIND=cpu|vm|io|net DURATION_SEC=60 REPS=3 OUT=<dir> \
#     bash kfusion/scripts/run_let_load_matrix__负载梯度矩阵.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# 实验脚本优先用环境变量覆盖（传到 Pi 用 ASCII 名时 ROOT 解析会失效），否则按仓库相对路径
EXP="${KF_EXP_SCRIPT:-$ROOT/kfusion/scripts/run_let_jitter_exp__读写抖动实验.sh}"

LOAD_KIND="${LOAD_KIND:-cpu}"
DURATION="${DURATION_SEC:-60}"
REPS="${REPS:-3}"
OUT="${OUT:-/home/pi/kfusion_results/let_load_${LOAD_KIND}_${DURATION}s_${REPS}r}"
CPU="${KF_CPU:-3}"
VM_BYTES="${VM_BYTES:-512M}"
NET_WORKERS="${NET_WORKERS:-2}"
# 复用同一批已验收的 .ko：既省时长，又保证各负载档之间唯一变量只有负载
REUSE_KO_DIR="${REUSE_KO_DIR:-/home/pi/kfusion_results/let_jitter_idle_60s_3x60s}"

command -v stress-ng >/dev/null || { echo "stress-ng missing" >&2; exit 1; }
mkdir -p "$OUT"

# 负载核 = 所有在线核中排除隔离核 CPU3（本机 online=0-3, isolated=3 → 负载核 0,1,2）
ALL_CPUS="$(cat /sys/devices/system/cpu/online)"
LOAD_CPUS="$(python3 - "$ALL_CPUS" "$CPU" <<'PY'
import sys
spec, iso = sys.argv[1], int(sys.argv[2])
out=[]
for part in spec.split(','):
    if '-' in part:
        a,b=part.split('-'); out += list(range(int(a),int(b)+1))
    else:
        out.append(int(part))
print(','.join(str(x) for x in out if x != iso))
PY
)"
[ -n "$LOAD_CPUS" ] || { echo "no load cpus" >&2; exit 1; }

# 总时长余量：build 两臂 + REPS×2臂×DURATION + 缓冲
TOTAL_SEC=$(( REPS * 2 * DURATION + 240 ))

case "$LOAD_KIND" in
  cpu) LOAD_CMD=(stress-ng --cpu 0 --cpu-load 100);;
  vm)  LOAD_CMD=(stress-ng --vm 0 --vm-bytes "$VM_BYTES");;
  io)  LOAD_CMD=(stress-ng --io 0);;
  net) LOAD_CMD=(stress-ng --sock "$NET_WORKERS");;
  *) echo "unknown LOAD_KIND=$LOAD_KIND (cpu|vm|io|net)" >&2; exit 1;;
esac

{
  echo "load_kind=$LOAD_KIND"
  echo "load_cmd=taskset -c $LOAD_CPUS ${LOAD_CMD[*]} --timeout ${TOTAL_SEC}s --quiet"
  echo "load_cpus=$LOAD_CPUS measure_cpu=$CPU"
  echo "duration=${DURATION}s reps=$REPS total_budget=${TOTAL_SEC}s"
  echo "reuse_ko_dir=$REUSE_KO_DIR"
} | tee "$OUT/load_config.txt"

# 后台持续负载，绑非隔离核
taskset -c "$LOAD_CPUS" "${LOAD_CMD[@]}" --timeout "${TOTAL_SEC}s" --quiet &
LOAD_PID=$!
sleep 2  # 让负载先起来
kill -0 "$LOAD_PID" 2>/dev/null || { echo "load failed to start" >&2; exit 1; }

cleanup(){ kill "$LOAD_PID" 2>/dev/null || true; wait "$LOAD_PID" 2>/dev/null || true; }
trap cleanup EXIT

# 实验脚本走 idle 模式（负载由外部维持）；记录 load 臂标记在 OUT 名上
MODE=idle DURATION_SEC="$DURATION" REPS="$REPS" OUT="$OUT" REUSE_KO_DIR="$REUSE_KO_DIR" \
  bash "$EXP" 2>&1 | tee "$OUT/run.log"

cleanup
trap - EXIT
echo "✅ load matrix done: $OUT (kind=$LOAD_KIND)"
