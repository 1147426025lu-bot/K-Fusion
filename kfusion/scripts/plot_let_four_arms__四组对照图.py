#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plot_let_four_arms__四组对照图.py — 开题四组对照（A/B/C/D）出图

输入目录约定（由 run_let_four_arms 产生）：
  <run>/A/rep<N>/userspace_r<N>.meta.json + .samples.csv      A 用户态线程
  <run>/BD/thread/rep<N>/thread_r<N>.meta.json               B 内核 kthread
  <run>/C_hard/hand/rep<N>/hand_r<N>.meta.json               C 手写硬中断
  <run>/C_soft/hand/rep<N>/hand_r<N>.meta.json               C 手写软中断（对照）
  <run>/BD/irq/rep<N>/irq_r<N>.meta.json                     D 编译器生成硬中断

统计纪律：
  - 跨轮只报中位数与极差，不报跨轮均值（均值会被单轮异常拍拉偏）
  - max 与 p99 是不同风险，图上分开画，不合成一个"代表值"
  - 图中文字全英文，中文解释放文档（matplotlib 缺中文字体会出豆腐块）

用法：
  python3 plot_let_four_arms__四组对照图.py --run /home/pi/kfusion_results/let4_20261119 -o figs
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import statistics as st

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ARMS = [
    # (key, 显示名, meta glob, csv glob, 颜色)
    ("A", "A userspace thread", "A/rep*/userspace_r*.meta.json",
     "A/rep*/userspace_r*.samples.csv", "#4C72B0"),
    ("B", "B kernel kthread", "BD/thread/rep*/thread_r*.meta.json",
     "BD/thread/rep*/thread_r*.samples.csv", "#55A868"),
    ("C", "C handwritten hrtimer", "C_hard/hand/rep*/hand_r*.meta.json",
     "C_hard/hand/rep*/hand_r*.samples.csv", "#C44E52"),
    ("D", "D generated hrtimer", "BD/irq/rep*/irq_r*.meta.json",
     "BD/irq/rep*/irq_r*.samples.csv", "#8172B2"),
]
ARM_C_SOFT = ("C_soft", "C handwritten (softirq)", "C_soft/hand/rep*/hand_r*.meta.json",
              "C_soft/hand/rep*/hand_r*.samples.csv", "#937860")


def natural_rep(path):
    """按 rep 号排序，避免 rep10 排在 rep2 前面。"""
    base = os.path.basename(os.path.dirname(path))
    digits = "".join(ch for ch in base if ch.isdigit())
    return int(digits) if digits else 0


def load_arm(run, meta_glob):
    files = sorted(glob.glob(os.path.join(run, meta_glob)), key=natural_rep)
    reps = []
    for f in files:
        m = json.load(open(f, encoding="utf-8"))
        s = m["summary"]
        reps.append({
            "rep": natural_rep(f),
            "meta": m,
            "read": s["dist_read"],
            "write": s["dist_write"],
            "skip": s["n_skip"],
            "err": s["n_err"],
            "ver_mismatch": s["version_mismatch_cycles"],
            "n": s["n"],
        })
    return reps


def med(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return st.median(vals)


def rng(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None
    return min(vals), max(vals)


def load_csv_jitter(path, op_name, limit=200000):
    """读 samples.csv 里某 op 的 jitter_ns（只取 status ok）。

    只用于画 CDF 形状，不做统计推断；limit 防超大文件撑爆内存。
    """
    vals = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("op_name") != op_name or row.get("status_name") != "ok":
                continue
            j = row.get("jitter_ns")
            if not j:
                continue
            vals.append(int(j))
            if len(vals) >= limit:
                break
    return vals


SHORT = {
    "A": "A\nuserspace",
    "B": "B\nkthread",
    "C": "C\nhand hrtimer",
    "D": "D\ngen hrtimer",
    "C_soft": "C'\nsoftirq",
}


def fig_bars(data, out):
    """主图：读/写 × {p50, p99, max}，柱高=跨轮中位数，误差条=跨轮极差。

    只在 p50 柱上标数值——三个指标全标会互相压字（v1 的教训）。
    """
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 6.4), sharey=True)
    metrics = [("p50_ns", "p50"), ("p99_ns", "p99"), ("max_ns", "max")]
    x = list(range(len(data)))
    width = 0.26

    for ax, op, op_cn in ((axes[0], "read", "Read"), (axes[1], "write", "Write")):
        for i, (key, mname) in enumerate(metrics):
            heights, lo_err, hi_err = [], [], []
            for d in data:
                vals = [r[op][key] / 1000.0 for r in d["reps"] if r[op][key] is not None]
                mm = med(vals)
                lo, hi = rng(vals)
                heights.append(mm if mm is not None else 0)
                lo_err.append((mm - lo) if (mm is not None and lo is not None) else 0)
                hi_err.append((hi - mm) if (mm is not None and hi is not None) else 0)
            pos = [xx + (i - 1) * width for xx in x]
            ax.bar(pos, heights, width, label=mname,
                   yerr=[lo_err, hi_err], capsize=2.5, alpha=0.88)
            if mname == "p50":
                for p, h in zip(pos, heights):
                    if h:
                        ax.text(p, h * 1.12, "%.1f" % h, ha="center", va="bottom", fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels([SHORT.get(d["key"], d["key"]) for d in data],
                           fontsize=8, linespacing=1.15)
        ax.set_title("%s  |t_after - nominal|   (bar=median of reps, error=range)" % op_cn,
                     fontsize=10)
        ax.grid(axis="y", alpha=0.3, which="both")
    axes[0].set_ylabel("microseconds (log)")
    axes[0].set_yscale("log")
    axes[0].set_ylim(bottom=2)          # 柱最低 3.3us，留一点头避免贴边
    axes[1].legend(loc="lower left", fontsize=8, framealpha=0.92)
    fig.suptitle("LET read/write completion jitter — four arms (A/B/C/D)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    p = os.path.join(out, "fig1_four_arms_p50_p99_max.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


def fig_reps(data, out):
    """跨轮稳定性：每臂画 p50(实线) 与 max(虚线)，5 轮全画不做跨轮平均。"""
    fig, axes = plt.subplots(2, 1, figsize=(9.5, 7.5), sharex=True)
    for ax, op in ((axes[0], "read"), (axes[1], "write")):
        for d in data:
            xs = [r["rep"] for r in d["reps"]]
            ys50 = [(r[op]["p50_ns"] / 1000.0 if r[op]["p50_ns"] is not None else None)
                    for r in d["reps"]]
            ysmax = [(r[op]["max_ns"] / 1000.0 if r[op]["max_ns"] is not None else None)
                     for r in d["reps"]]
            ax.plot(xs, ys50, "o-", color=d["color"], markersize=5,
                    label="%s p50" % d["key"])
            ax.plot(xs, ysmax, "^--", color=d["color"], markersize=5, alpha=0.65,
                    label="%s max" % d["key"])
        ax.set_title("%s jitter per repetition (p50 solid, max dashed)" % op.capitalize(),
                     fontsize=10)
        ax.set_ylabel("microseconds (log)")
        ax.set_yscale("log")
        ax.grid(alpha=0.3, which="both")
        ax.set_xticks(sorted({r["rep"] for d in data for r in d["reps"]}))
    axes[1].set_xlabel("repetition")
    axes[0].legend(fontsize=7, ncol=2, framealpha=0.9)
    fig.tight_layout()
    p = os.path.join(out, "fig2_per_rep_stability.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


def fig_soft_vs_hard(run, out):
    """C 组内部单变量对照：同一份手写代码，只改 hrtimer 回调跑在硬中断还是软中断。

    这是全篇最强的一组证据：唯一差异是回调上下文，其余（代码、相位、优先级）全同。
    """
    hard = load_arm(run, "C_hard/hand/rep*/hand_r*.meta.json")
    soft = load_arm(run, "C_soft/hand/rep*/hand_r*.meta.json")
    if not hard or not soft:
        return None

    pairs = [("C handwritten / hardirq", hard, "#C44E52"),
             ("C handwritten / softirq", soft, "#937860")]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), sharey=True)
    width = 0.26
    for ax, op, op_cn in ((axes[0], "read", "Read"), (axes[1], "write", "Write")):
        for i, (key, mname) in enumerate([("p50_ns", "p50"), ("p99_ns", "p99"),
                                        ("max_ns", "max")]):
            for j, (label, reps, color) in enumerate(pairs):
                vals = [r[op][key] / 1000.0 for r in reps if r[op][key] is not None]
                mm = med(vals)
                lo, hi = rng(vals)
                pos = j + (i - 1) * width
                ax.bar(pos, mm, width, color=color, alpha=0.55 + 0.15 * i,
                       yerr=[[mm - lo], [hi - mm]], capsize=2.5,
                       label=mname if j == 0 else None)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["hardirq", "softirq"], fontsize=9)
        ax.set_title("%s |t_after - nominal| (median, error=range)" % op_cn, fontsize=10)
        ax.set_yscale("log")
        ax.grid(axis="y", alpha=0.3, which="both")
    axes[0].set_ylabel("microseconds (log)")
    axes[1].legend(fontsize=8, loc="lower right")
    fig.suptitle("Same handwritten module, only the hrtimer callback context differs",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    p = os.path.join(out, "fig4_softirq_vs_hardirq.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


def fig_cdf(data, run, out, op="read"):
    """CDF：形状比单一分位数更能说明"长尾"还是"双峰"。"""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for d in data:
        files = sorted(glob.glob(os.path.join(run, d["csv_glob"])), key=natural_rep)
        if not files:
            continue
        # 多轮合并再抽稀，避免单轮样本过密；形状用途，不做推断
        vals = []
        for f in files:
            vals.extend(load_csv_jitter(f, op))
        if not vals:
            continue
        vals.sort()
        n = len(vals)
        step = max(1, n // 20000)
        xs = [v / 1000.0 for v in vals[::step]]
        ys = [(i * step + 1) / n * 100 for i in range(len(xs))]
        ax.plot(xs, ys, label="%s (n=%d)" % (d["label"], n), color=d["color"], lw=1.4)
    ax.set_xscale("log")
    ax.set_xlabel("%s |t_after - nominal| (us, log)" % op)
    ax.set_ylabel("cumulative % of samples")
    ax.set_title("Jitter CDF, %s (all reps merged)" % op)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(out, "fig3_cdf_%s.png" % op)
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("-o", "--out-dir", help="默认 <run>/figs")
    ap.add_argument("--with-softirq", action="store_true",
                    help="把 C 软中断对照臂也画进图（默认不进主图，避免混淆四组）")
    args = ap.parse_args()

    out = args.out_dir or os.path.join(args.run, "figs")
    os.makedirs(out, exist_ok=True)

    specs = list(ARMS)
    if args.with_softirq:
        specs.append(ARM_C_SOFT)

    data = []
    for key, label, mg, cg, color in specs:
        reps = load_arm(args.run, mg)
        if not reps:
            print("跳过 %s：无数据（%s）" % (key, mg))
            continue
        data.append({"key": key, "label": label, "color": color,
                     "reps": reps, "csv_glob": cg})
        print("%-32s reps=%d" % (label, len(reps)))

    if not data:
        raise SystemExit("没有任何臂的 meta.json，检查 --run 目录")

    paths = [fig_bars(data, out), fig_reps(data, out)]
    for op in ("read", "write"):
        paths.append(fig_cdf(data, args.run, out, op))
    # C 硬/软 单变量对照始终单独出图：它的 max 是毫秒级，混进主图会把纵轴拉爆
    soft_fig = fig_soft_vs_hard(args.run, out)
    if soft_fig:
        paths.append(soft_fig)

    # 表格：供文档直接引用，含每轮值与跨轮中位/极差
    rows = []
    for d in data:
        for op in ("read", "write"):
            for key in ("p50_ns", "p99_ns", "max_ns"):
                vals = [(r[op][key] / 1000.0 if r[op][key] is not None else None)
                        for r in d["reps"]]
                mm = med(vals)
                lo, hi = rng(vals)
                rows.append({
                    "arm": d["key"], "op": op, "metric": key[:-3],
                    "per_rep_us": [None if v is None else round(v, 2) for v in vals],
                    "median_us": None if mm is None else round(mm, 2),
                    "min_us": None if lo is None else round(lo, 2),
                    "max_us": None if hi is None else round(hi, 2),
                    "spread_us": None if (mm is None) else round(hi - lo, 2),
                })
    tp = os.path.join(out, "table_four_arms.json")
    json.dump(rows, open(tp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    md = ["# 四组对照（跨轮中位数 [极小-极大]，单位 us）", "",
          "| 臂 | 操作 | p50 | p99 | max |", "|---|---|---|---|---|"]
    for d in data:
        for op in ("read", "write"):
            cells = []
            for key in ("p50_ns", "p99_ns", "max_ns"):
                row = next(r for r in rows if r["arm"] == d["key"] and r["op"] == op
                           and r["metric"] == key[:-3])
                cells.append("%.2f [%.2f-%.2f]" % (row["median_us"], row["min_us"], row["max_us"])
                             if row["median_us"] is not None else "-")
            md.append("| %s | %s | %s |" % (d["key"], op, " | ".join(cells)))
    mp = os.path.join(out, "table_four_arms.md")
    open(mp, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))
    print("\n写了：")
    for p in paths + [tp, mp]:
        print("  " + p)


if __name__ == "__main__":
    main()
