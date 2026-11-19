#!/bin/bash
# ============================================================================
# run_let_handwritten__C组手写中断实验.sh — 论文 C 组：手写硬中断读写
#
# 与 D 组（irq 臂，编译器生成硬中断）配对：同源同任务，唯一变量=读写桩来源。
# 手写模块 kf_task.ko 由 kf_hand_let__手写硬中断读写.c 板编译而来，
# 导出 /dev/kf_let_log（与 D 组同格式），dump 后写 table_correctness.json。
#
# 用法：
#   OUT=/home/pi/kfusion_results/let_hand_60s_3x60s \
#   DURATION_SEC=60 REPS=3 bash run_let_handwritten__C组手写中断实验.sh
# ============================================================================
set -euo pipefail

KF=/home/pi/K-Fusion
KO=/home/pi/kf_hand_build/kf_task.ko
PY=python3
OUT="${OUT:-/home/pi/kfusion_results/let_hand_60s_3x60s}"
DURATION="${DURATION_SEC:-60}"
REPS="${REPS:-3}"
CPU="${KF_CPU:-3}"
CAP="${KF_LET_LOG_CAP:-200000}"
HRD="${HRD:-1}"   # 1=硬中断回调（主口径）0=软中断回调（单变量对照）
WARMUP_SEC="${WARMUP_SEC:-10}"

mkdir -p "$OUT/figs"
[ -f "$KO" ] || { echo "缺 $KO，先板编译手写模块"; exit 1; }

cleanup() { sudo rmmod kf_task 2>/dev/null || true; }
trap cleanup EXIT

for rep in $(seq 1 "$REPS"); do
    echo "======== hand rep$rep start $(date +%H:%M:%S) ========"
    dest="$OUT/hand/rep$rep"
    mkdir -p "$dest"
    cleanup
    sudo insmod "$KO" cpu="$CPU" period_us=1000 rt_priority=80 \
        let_log_cap="$CAP" hrtimer_hard="$HRD"
    sleep 1
    {
        echo "hrtimer_hard=$(cat /sys/module/kf_task/parameters/hrtimer_hard)"
        for p in let_ctx_hardirq let_ctx_softirq let_ctx_task periods \
                 let_rd_jit_max_ns let_wr_jit_max_ns let_rd_ok let_wr_ok \
                 let_skip_commit let_log_drops; do
            [ -f /sys/module/kf_task/parameters/$p ] && echo "$p=$(cat /sys/module/kf_task/parameters/$p)"
        done
    } | tee "$dest/params_t1.txt"
    sleep "$DURATION"
    {
        for p in periods let_rw_fires let_rw_irq let_rd_ok let_wr_ok \
                 let_skip_commit let_log_drops let_ctx_hardirq \
                 let_ctx_softirq let_ctx_task let_rd_jit_max_ns \
                 let_wr_jit_max_ns let_rd_late_max_ns let_rd_early_max_ns \
                 let_wr_late_max_ns let_wr_early_max_ns let_op_dur_max_ns \
                 let_cb_dur_max_ns let_cb_wr_dur_max_ns let_cb_rd_dur_max_ns \
                 let_cb_over_10us; do
            [ -f /sys/module/kf_task/parameters/$p ] && echo "$p=$(cat /sys/module/kf_task/parameters/$p)"
        done
    } | tee "$dest/params_end.txt"
    sudo dd if=/dev/kf_let_log of="$dest/raw.bin" bs=1M status=none || \
        sudo cat /dev/kf_let_log >"$dest/raw.bin"
    cleanup
    # 注意：meta.json 才是干净 JSON；dump_summary.json 若用 tee 会被混入文本行
    "$PY" "$KF/kfusion/scripts/let_log_dump__导出读写日志.py" \
        --bin "$dest/raw.bin" -o "$dest" --tag "hand_r${rep}" \
        --warmup-sec "$WARMUP_SEC" \
        >"$dest/dump.log" 2>&1
    echo "======== hand rep$rep done $(date +%H:%M:%S) ========"
done

# 汇总成与 D 组同结构的 table_correctness.json（供配对出图脚本读）
"$PY" - "$OUT" <<'PYEOF'
import json, glob, os, sys
out = sys.argv[1]

def read_params(p):
    """params_end.txt 是 key=value 行；解析失败的值跳过不影响其它字段。"""
    d = {}
    if not os.path.isfile(p):
        return d
    for line in open(p, encoding="utf-8", errors="replace"):
        if "=" not in line:
            continue
        k, v = line.strip().split("=", 1)
        d[k] = v
    return d

rows = []
for f in sorted(glob.glob(os.path.join(out, "hand", "rep*", "*.meta.json"))):
    repdir = os.path.dirname(f)
    rep = int(os.path.basename(repdir).replace("rep", ""))
    d = json.load(open(f, encoding="utf-8"))   # meta.json 是干净 JSON；dump_summary.json 被 tee 混入文本行，勿用
    s = d["summary"]; h = d["hdr"]
    pe = read_params(os.path.join(repdir, "params_end.txt"))
    def pu(k):
        """取参数并换算成 us；缺失返回 None，不补 0 以免与真实 0 混淆。"""
        v = pe.get(k)
        return round(int(v) / 1000.0, 3) if v is not None else None
    rows.append({
        "arm": "hand", "rep": rep,
        "n_samples": s["n"],
        "max_rd_us": s["max_read_jitter_ns"] / 1000.0,
        "max_wr_us": s["max_write_jitter_ns"] / 1000.0,
        "p99_rd_us": (s["dist_read"]["p99_ns"] or 0) / 1000.0,
        "p99_wr_us": (s["dist_write"]["p99_ns"] or 0) / 1000.0,
        "p50_rd_us": (s["dist_read"]["p50_ns"] or 0) / 1000.0,
        "p50_wr_us": (s["dist_write"]["p50_ns"] or 0) / 1000.0,
        "late_rd_us": s["max_read_late_ns"] / 1000.0,
        "late_wr_us": s["max_write_late_ns"] / 1000.0,
        "early_rd_us": s["max_read_early_ns"] / 1000.0,
        "early_wr_us": s["max_write_early_ns"] / 1000.0,
        "skip": s["n_skip"], "err": s["n_err"],
        "drops": h["drops"], "hold": s["n_hold"],
        "ver_mismatch": s["version_mismatch_cycles"],
        "ctx": s["ctx_counts"],
        "op_dur_max_us": pu("let_op_dur_max_ns"),
        "cb_dur_max_us": pu("let_cb_dur_max_ns"),
        "cb_wr_max_us": pu("let_cb_wr_dur_max_ns"),
        "cb_rd_max_us": pu("let_cb_rd_dur_max_ns"),
        "cb_over_10us": pe.get("let_cb_over_10us"),
        "hrtimer_hard": pe.get("hrtimer_hard"),
    })
json.dump(rows, open(os.path.join(out, "figs", "table_correctness.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("wrote", os.path.join(out, "figs", "table_correctness.json"), "rows=", len(rows))
PYEOF
echo "===ALL DONE $(date +%H:%M:%S)==="
