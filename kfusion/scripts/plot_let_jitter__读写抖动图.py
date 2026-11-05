#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从多次实验的 meta/samples 画必做抖动图与正确性表（省内存：柱状图只读 meta；
时序图流式扫描 CSV，按窗保留峰值）。"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def load_meta(meta_path):
    return json.load(open(meta_path, encoding="utf-8"))


def arm_from_path(mp, meta):
    parts = mp.split(os.sep)
    for p in parts:
        if p in ("thread", "irq", "userspace", "irq_hard"):
            return p
    return (meta.get("hdr") or {}).get("arm_name") or meta.get("arm") or "unknown"


def stream_peak_series(csv_path, want_op, win=300):
    """流式读 CSV：want_op in {1,2} 或 'write'/'read'；每 win 条保留该窗最大抖动。"""
    xs, ys = [], []
    cur_x, cur_y = None, -1.0
    n_in_win = 0
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            op = r.get("op")
            on = r.get("op_name")
            if want_op in (1, "1", "write"):
                ok_op = (str(op) == "1") or (on == "write")
            else:
                ok_op = (str(op) == "2") or (on == "read")
            if not ok_op:
                continue
            if str(r.get("status")) not in ("0", "ok") and r.get("status_name") != "ok":
                continue
            j = r.get("jitter_ns")
            if j is None or j == "":
                continue
            j = float(j) / 1000.0
            cid = int(r["cycle_id"])
            if j > cur_y:
                cur_y = j
                cur_x = cid
            n_in_win += 1
            if n_in_win >= win:
                if cur_x is not None:
                    xs.append(cur_x)
                    ys.append(cur_y)
                cur_x, cur_y, n_in_win = None, -1.0, 0
    if n_in_win and cur_x is not None:
        xs.append(cur_x)
        ys.append(cur_y)
    return xs, ys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("-o", "--out-dir", help="默认 root/figs")
    args = ap.parse_args()
    root = args.root
    out = args.out_dir or os.path.join(root, "figs")
    os.makedirs(out, exist_ok=True)

    metas = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.endswith(".meta.json"):
                metas.append(os.path.join(dirpath, fn))
    metas.sort()
    if not metas:
        raise SystemExit("未找到 meta.json")

    by_arm = {}
    for mp in metas:
        meta = load_meta(mp)
        arm = arm_from_path(mp, meta)
        csv_path = mp[:-10] + ".samples.csv" if mp.endswith(".meta.json") else None
        by_arm.setdefault(arm, []).append((mp, meta, csv_path))

    # ---- max jitter bars ----
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, key, title in (
        (axes[0], "max_read_jitter_ns", "Max read jitter max|R-r|"),
        (axes[1], "max_write_jitter_ns", "Max write jitter max|W-d|"),
    ):
        arms = sorted(by_arm.keys())
        x = np.arange(len(arms))
        means, all_pts = [], []
        for a in arms:
            vals = []
            for _, meta, _ in by_arm[a]:
                v = (meta.get("summary") or {}).get(key)
                if v is not None:
                    vals.append(v / 1000.0)
            means.append(float(np.mean(vals)) if vals else 0.0)
            all_pts.append(vals)
        ax.bar(x, means, color="#4C78A8", alpha=0.75, width=0.55, label="mean")
        for i, pts in enumerate(all_pts):
            ax.scatter([i] * len(pts), pts, color="#E45756", zorder=3, s=36)
        ax.set_xticks(x)
        ax.set_xticklabels(arms)
        ax.set_ylabel("us")
        ax.set_title(title)
        ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig_max_jitter_compare.png"), dpi=140)
    plt.close(fig)

    # ---- timeseries: first rep per arm only ----
    for arm, runs in by_arm.items():
        mp, meta, csv_path = runs[0]
        if not csv_path or not os.path.isfile(csv_path):
            continue
        fig, axes = plt.subplots(2, 1, figsize=(11, 5.5), sharex=True)
        for ax, op, name in ((axes[0], 2, "read |R-r|"), (axes[1], 1, "write |W-d|")):
            xs, ys = stream_peak_series(csv_path, op, win=300)
            if not xs:
                ax.set_title("%s %s (no samples)" % (arm, name))
                continue
            ax.plot(xs, ys, lw=0.8, color="#4C78A8")
            im = int(np.argmax(ys))
            ax.scatter([xs[im]], [ys[im]], color="#E45756", s=28, zorder=3,
                       label="peak_bin=%.1fus" % ys[im])
            # also annotate meta max
            key = "max_read_jitter_ns" if op == 2 else "max_write_jitter_ns"
            mmax = (meta.get("summary") or {}).get(key)
            if mmax is not None:
                ax.axhline(mmax / 1000.0, color="#F58518", ls="--", lw=0.9,
                           label="max=%.2fus" % (mmax / 1000.0))
            ax.legend(loc="upper right", fontsize=8)
            ax.set_ylabel("us")
            ax.set_title("%s — %s" % (arm, name))
            ax.grid(True, alpha=0.3)
        axes[-1].set_xlabel("cycle_id")
        fig.tight_layout()
        fig.savefig(os.path.join(out, "fig_jitter_timeseries_%s.png" % arm), dpi=140)
        plt.close(fig)

    # ---- correctness table ----
    table = []
    for arm in sorted(by_arm.keys()):
        for i, (_, meta, _) in enumerate(by_arm[arm], 1):
            s = meta.get("summary") or {}
            h = meta.get("hdr") or {}
            table.append({
                "arm": arm,
                "rep": i,
                "n_samples": s.get("n"),
                "read_ok": s.get("n_read_ok"),
                "write_ok": s.get("n_write_ok"),
                "skip": s.get("n_skip"),
                "hold": s.get("n_hold"),
                "err": s.get("n_err"),
                "drops": h.get("drops"),
                "max_rd_us": (s["max_read_jitter_ns"] / 1000.0
                              if s.get("max_read_jitter_ns") is not None else None),
                "max_wr_us": (s["max_write_jitter_ns"] / 1000.0
                              if s.get("max_write_jitter_ns") is not None else None),
                "ctx": s.get("ctx_counts"),
            })
    json.dump(table, open(os.path.join(out, "table_correctness.json"), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=2)
    lines = [
        "| arm | rep | read_ok | write_ok | skip | err | drops | max_rd_us | max_wr_us | ctx |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in table:
        lines.append(
            "| {arm} | {rep} | {read_ok} | {write_ok} | {skip} | {err} | {drops} | {max_rd_us} | {max_wr_us} | {ctx} |".format(
                arm=r["arm"], rep=r["rep"],
                read_ok=r["read_ok"], write_ok=r["write_ok"],
                skip=r["skip"], err=r["err"], drops=r["drops"],
                max_rd_us=("%.2f" % r["max_rd_us"]) if r["max_rd_us"] is not None else "-",
                max_wr_us=("%.2f" % r["max_wr_us"]) if r["max_wr_us"] is not None else "-",
                ctx=r["ctx"],
            ))
    open(os.path.join(out, "table_correctness.md"), "w", encoding="utf-8").write(
        "\n".join(lines) + "\n")
    print("figs ->", out)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
