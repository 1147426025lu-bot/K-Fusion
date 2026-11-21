#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plot_four_arms_v2__四组重跑出图.py — 计算链统一后的四组（A/B/C/D）出图

输入目录约定（由 run_four_arms_v2 / run_let_arm 产生）：
  <run>/<ARM>/figs/table_<ARM>.json      每轮汇总（read/write 的 p50/p90/p99/p999/max）
  <run>/<ARM>/rep<N>/*.samples.csv       逐样本（idx,cycle_id,op,nominal_ns,t_after_ns,jitter_ns…）

三张主图（评审要求的口径）：
  fig1_max_per_rep   每个臂「各轮的最大读/写抖动」散点 + 同条件最高观测值（跨轮 max 包络）
  fig2_signed_vs_t   带符号时刻偏差 (t_after − nominal) 随时间，一臂一格
  fig3_p99_tail      尾部：p99 / p99.9 逐轮散点 + 跨轮中位

统计纪律（与文档一致，别改）：
  · 跨轮只报中位数与极差，**不报跨轮均值**（均值会被单轮异常拍拉偏）；
  · max 与 p99 是两种不同风险，不合并成"代表值"，也不跨臂做倍数比；
  · 图内文字用英文（板上 matplotlib 无中文字体，中文会出豆腐块）；解释写在文档里。

用法：
  python3 plot_four_arms_v2__四组重跑出图.py --run /home/pi/kfusion_results/four_arms_v2 -o figs
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
    ("A", "A userspace thread", "#4C72B0"),
    ("B", "B kernel kthread (worker)", "#55A868"),
    ("C", "C handwritten impl (irq)", "#C44E52"),
    ("D", "D generated impl (irq)", "#8172B2"),
]
OPS = [("read", "read", "o"), ("write", "write", "s")]


# ---------------------------------------------------------------- 读入
def _row_from_meta(path):
    """把逐轮 *.meta.json 折成与 table_<arm>.json 同形的行。"""
    m = json.load(open(path, encoding="utf-8"))
    s = m["summary"]
    base = os.path.basename(os.path.dirname(path))
    digits = "".join(ch for ch in base if ch.isdigit())
    return {
        "arm": None, "rep": int(digits) if digits else 0, "n": s["n"],
        "warmup_sec": s.get("warmup_sec"), "n_trimmed": s.get("n_trimmed"),
        "read": s["dist_read"], "write": s["dist_write"],
        "skip": s["n_skip"], "hold": s["n_hold"], "err": s["n_err"],
        "ver_mismatch": s["version_mismatch_cycles"],
        "ver_nonmono": s["version_nonmono_writes"],
        "hdr": {k: m["hdr"].get(k) for k in ("arm_name", "arm_path", "drops",
                                            "period_us", "n_samples")},
    }


def load_tables(run):
    """返回 {arm: [row, ...]}，row 已按 rep 排序。缺臂返回空列表。"""
    out = {}
    for arm, _disp, _c in ARMS:
        p = os.path.join(run, arm, "figs", "table_%s.json" % arm)
        if os.path.exists(p):
            rows = json.load(open(p, encoding="utf-8"))
        else:
            # A 组走 run_let_userspace，只落逐轮 *.meta.json 不落 table_*.json
            rows = [_row_from_meta(f)
                    for f in glob.glob(os.path.join(run, arm, "rep*", "*.meta.json"))]
        out[arm] = sorted(rows, key=lambda r: r["rep"])
    return out


def samples_csv(run, arm, rep):
    hits = sorted(glob.glob(os.path.join(run, arm, "rep%d" % rep, "*.samples.csv")))
    return hits[0] if hits else None


def load_signed(path, stride):
    """读 samples.csv，返回 [(t_s, delta_us, op_name)]，按 stride 抽稀。"""
    pts = []
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for i, r in enumerate(rd):
            if i % stride:
                continue
            try:
                t0 = int(r["nominal_ns"])
                t1 = int(r["t_after_ns"])
            except (KeyError, ValueError):
                continue
            if r.get("status_name") not in (None, "", "ok"):
                continue
            pts.append((t0 / 1e9, (t1 - t0) / 1e3, 1 if r["op_name"] == "write" else 0))
    return pts


# ---------------------------------------------------------------- fig1
def fig_max_per_rep(tables, outdir):
    """各轮最大抖动 + 同条件最高观测值。max 是"最差一次"，必须逐轮摆开看极差。"""
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), sharey=True)
    for ax, (op, label, marker) in zip(axes, OPS):
        for xi, (arm, disp, color) in enumerate(ARMS):
            rows = tables[arm]
            if not rows:
                continue
            vals = [r[op]["max_ns"] / 1e3 for r in rows]
            ax.scatter([xi] * len(vals), vals, s=42, marker=marker, color=color,
                       zorder=3, label="%s  (%s)" % (arm, op))
            ax.hlines(st.median(vals), xi - 0.30, xi + 0.30, color=color, lw=2.2, zorder=2)
            ax.annotate("%.1f" % max(vals), (xi, max(vals)), textcoords="offset points",
                        xytext=(6, 4), fontsize=8, color=color)
        ax.set_xticks(range(len(ARMS)))
        ax.set_xticklabels([a for a, _d, _c in ARMS])
        ax.set_xlim(-0.6, len(ARMS) - 0.4)
        ax.set_title("%s — per-rep MAX jitter (worst single sample)" % label)
        ax.grid(axis="y", ls=":", alpha=0.55)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("max |t_after - nominal|  [us]")
    axes[0].legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.suptitle("fig1  Worst-case jitter per repetition (5 x 60 s each, warmup 10 s dropped)",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    p = os.path.join(outdir, "fig1_max_per_rep.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


# ---------------------------------------------------------------- fig2
def fig_signed_vs_time(run, tables, outdir, rep):
    """带符号偏差随时间。看的是"偏差是否随时间漂/涨"，不是分布形状。"""
    stride = int(os.environ.get("KF_PLOT_STRIDE", "10"))
    fig, axes = plt.subplots(len(ARMS), 1, figsize=(11.0, 2.15 * len(ARMS)),
                             sharex=False)
    if len(ARMS) == 1:
        axes = [axes]
    for ax, (arm, disp, color) in zip(axes, ARMS):
        path = samples_csv(run, arm, rep)
        if not path:
            ax.set_visible(False)
            continue
        pts = load_signed(path, stride)
        if not pts:
            ax.set_visible(False)
            continue
        t0 = pts[0][0]
        tw = [(t - t0, d) for t, d, w in pts if w]
        tr = [(t - t0, d) for t, d, w in pts if not w]
        ax.scatter([x for x, _ in tr], [y for _, y in tr], s=1.2, color="#4C72B0",
                   alpha=0.35, lw=0, label="read", rasterized=True)
        ax.scatter([x for x, _ in tw], [y for _, y in tw], s=1.2, color="#DD8452",
                   alpha=0.35, lw=0, label="write", rasterized=True)
        allv = [d for _t, d, _w in pts]
        p50, p99 = st.median(allv), sorted(allv)[int(0.99 * (len(allv) - 1))]
        ax.axhline(p50, color="k", lw=1.0, ls="-", alpha=0.8)
        ax.axhline(p99, color="k", lw=1.0, ls="--", alpha=0.8)
        ax.annotate("p50 %.1f us" % p50, (0.995, p50), xycoords=("axes fraction", "data"),
                    ha="right", va="bottom", fontsize=8)
        ax.annotate("p99 %.1f us" % p99, (0.995, p99), xycoords=("axes fraction", "data"),
                    ha="right", va="bottom", fontsize=8)
        ax.set_title("%s  (rep%d, %d pts shown)" % (disp, rep, len(pts)),
                     fontsize=9, loc="left")
        ax.set_ylabel("t_after-nominal [us]")
        ax.grid(axis="y", ls=":", alpha=0.5)
        ax.set_axisbelow(True)
        ax.legend(loc="upper left", fontsize=7, markerscale=6, framealpha=0.85)
    axes[-1].set_xlabel("elapsed time since first shown sample [s]")
    fig.suptitle("fig2  Signed timing deviation over time (one repetition per arm)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    p = os.path.join(outdir, "fig2_signed_vs_time.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


# ---------------------------------------------------------------- fig3
def fig_p99_tail(tables, outdir):
    """尾部：p99 与 p99.9 逐轮散点。两者是不同风险，分两格。"""
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), sharey=True)
    for ax, q, qlabel in zip(axes, ("p99_ns", "p999_ns"), ("p99", "p99.9")):
        for xi, (arm, disp, color) in enumerate(ARMS):
            for op, _label, marker in OPS:
                vals = [r[op][q] / 1e3 for r in tables[arm]]
                if not vals:
                    continue
                off = -0.12 if op == "read" else 0.12
                ax.scatter([xi + off] * len(vals), vals, s=34, marker=marker,
                           color=color, alpha=0.85, zorder=3,
                           label=op if xi == 0 else None)
                ax.hlines(st.median(vals), xi + off - 0.11, xi + off + 0.11,
                          color=color, lw=2.0, zorder=2)
        ax.set_xticks(range(len(ARMS)))
        ax.set_xticklabels([a for a, _d, _c in ARMS])
        ax.set_xlim(-0.6, len(ARMS) - 0.4)
        ax.set_title("%s jitter per repetition" % qlabel)
        ax.grid(axis="y", ls=":", alpha=0.55)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("jitter [us]")
    axes[0].legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.suptitle("fig3  Tail quantiles per repetition (bars = median across 5 reps)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    p = os.path.join(outdir, "fig3_p99_tail.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


# ---------------------------------------------------------------- 表
def write_md_table(tables, outdir):
    lines = ["| arm | op | p50 | p99 | max | n(rep) |", "|---|---|---|---|---|---|"]
    for arm, disp, _c in ARMS:
        rows = tables[arm]
        if not rows:
            continue
        for op, _l, _m in OPS:
            def fmt(key):
                v = [r[op][key] / 1e3 for r in rows]
                return "%.2f [%.2f–%.2f]" % (st.median(v), min(v), max(v))
            lines.append("| %s | %s | %s | %s | %s | %d |"
                         % (disp, op, fmt("p50_ns"), fmt("p99_ns"), fmt("max_ns"), len(rows)))
    txt = "\n".join(lines) + "\n"
    p = os.path.join(outdir, "table_four_arms_v2.md")
    open(p, "w", encoding="utf-8").write(txt)
    return p, txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("--rep", type=int, default=0, help="fig2 用第几轮（0=自动取中间轮）")
    a = ap.parse_args()

    outdir = a.outdir or os.path.join(a.run, "figs")
    os.makedirs(outdir, exist_ok=True)
    tables = load_tables(a.run)

    reps = [len(tables[arm]) for arm, _d, _c in ARMS if tables[arm]]
    rep = a.rep or (max(1, (min(reps) + 1) // 2) if reps else 1)

    made = [fig_max_per_rep(tables, outdir)]
    made.append(fig_signed_vs_time(a.run, tables, outdir, rep))
    made.append(fig_p99_tail(tables, outdir))
    p, txt = write_md_table(tables, outdir)
    made.append(p)
    for m in made:
        print("wrote", m)
    print()
    print(txt)


if __name__ == "__main__":
    main()
