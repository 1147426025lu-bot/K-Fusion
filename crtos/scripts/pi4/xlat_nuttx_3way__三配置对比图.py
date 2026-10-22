#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NuttX 域三配置对比图：直方图叠加 + 均值/极值柱状图

用法：
  python xlat_nuttx_3way__三配置对比图.py <输出.png> [标签:日志路径 ...]

环境变量 SUBTITLE 可覆盖副标题（默认按 cyclictest 写，换负载时要设）。

日志格式：cyclictest -q 输出的 6 位桶号 + 计数。
"""
import re
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文字体（缺字会显示方框）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def parse_hist(path):
    bins = {}
    for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*(\d{6})\s+(\d+)\s*$", ln)
        if m and int(m.group(2)):
            bins[int(m.group(1))] = int(m.group(2))
    return bins


def stats(bins):
    n = sum(bins.values())
    mean = sum(k * v for k, v in bins.items()) / n
    return n, min(bins), max(bins), mean


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    out = sys.argv[1]
    items = []
    for spec in sys.argv[2:]:
        label, path = spec.split(":", 1)
        b = parse_hist(path)
        if b:
            items.append((label, b, stats(b)))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

    colors = ["#2E86C1", "#E74C3C", "#27AE60", "#8E44AD", "#F39C12"]
    for i, (label, bins, (n, mn, mx, mean)) in enumerate(items):
        xs = sorted(bins)
        ys = [bins[x] / n * 100 for x in xs]
        ax1.plot(xs, ys, "o-", ms=5, lw=1.8, color=colors[i % len(colors)],
                 label=f"{label}  n={n}  均值={mean:.3f}µs")
    ax1.set_yscale("log")
    ax1.set_xlabel("唤醒延迟（µs，1 µs 桶）")
    ax1.set_ylabel("占比（%，对数轴）")
    ax1.set_title(os.environ.get("SUBPLOT_TITLE", "延迟分布：各配置叠加"))
    ax1.grid(True, which="both", alpha=0.25, ls=":")
    ax1.legend(fontsize=9, loc="lower right")

    labels = [it[0] for it in items]
    x = range(len(items))
    w = 0.26
    ax2.bar([i - w for i in x], [it[2][1] for it in items], w, label="min", color="#95A5A6")
    ax2.bar(list(x), [it[2][3] for it in items], w, label="加权均值", color="#2E86C1")
    ax2.bar([i + w for i in x], [it[2][2] for it in items], w, label="abs_max", color="#E74C3C")
    for i, it in enumerate(items):
        n, mn, mx, mean = it[2]
        ax2.text(i, mean + 0.15, f"{mean:.2f}", ha="center", fontsize=9, color="#1B4F72")
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(labels, fontsize=9)
    ax2.set_ylabel("µs")
    ax2.set_title("min / 均值 / abs_max")
    ax2.set_ylim(0, 11)
    ax2.grid(True, axis="y", alpha=0.25, ls=":")
    ax2.legend(fontsize=9)

    fig.suptitle(os.environ.get(
        "SUBTITLE",
        "NuttX inmate（Jailhouse cell, CPU1, tickless, 1 kHz）"
        " · cyclictest -n 0 -m 0 -q -p 200 -h 200 -l 100000 -i 1000"), fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    print(f"-> {out}")
    for label, _, s in items:
        print(f"  {label}: n={s[0]} min={s[1]} mean={s[3]:.3f} max={s[2]}")


if __name__ == "__main__":
    main()
