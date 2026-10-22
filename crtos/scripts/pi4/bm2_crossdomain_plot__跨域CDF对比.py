#!/usr/bin/env python3
"""BM2 跨域对比图：CDF + 分位数。

为什么不用直方图叠加：NuttX 域 99.999% 的样本落在同一个 1 µs 桶里，
叠在直方图上是一条竖线，看不出形状；Linux 域的长尾又会把横轴拉长，
把两边的差别压扁。CDF 对这种情况最直观：
    - NuttX 在 6 µs 处一步跳到 1.0（几乎确定）
    - Linux 在 20 µs 附近才爬上去，且尾巴一直拖到 100 µs

用法：
  python bm2_crossdomain_plot.py <输出.png> "标签:日志路径" ...
日志格式：bm2pid / cyclictest 的 6 位桶号 + 计数。
"""
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                          "Noto Sans CJK SC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


def load(path):
    """返回 (值列表按桶展开的 numpy 数组, exec_avg, exec_max)。"""
    import numpy as np

    buckets = {}
    txt = open(path, encoding="utf-8", errors="replace").read()
    for ln in txt.splitlines():
        m = re.match(r"^\s*(\d{6})\s+(\d+)\s*$", ln)
        if m and int(m.group(2)):
            buckets[int(m.group(1))] = int(m.group(2))
    if not buckets:
        raise SystemExit(f"{path}: 没有直方图数据")
    vals = np.repeat(np.array(sorted(buckets)), [buckets[k] for k in sorted(buckets)])
    e = re.search(r"exec_us: avg=([\d.]+) max=([\d.]+)", txt)
    return vals, (e.group(1) if e else "?"), (e.group(2) if e else "?")


def main():
    import numpy as np

    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    out = sys.argv[1]
    items = []
    for spec in sys.argv[2:]:
        label, path = spec.rsplit(":", 1)
        vals, ea, em = load(path)
        items.append((label, vals, ea, em))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2))

    # ---- 左：CDF ----
    for label, vals, ea, em in items:
        v = np.sort(vals)
        y = np.arange(1, v.size + 1) / v.size
        # 抽样绘制，避免 10 万点开销（CDF 形状不受影响）
        step = max(1, v.size // 4000)
        ax1.plot(v[::step], y[::step], lw=1.8,
                 label=f"{label}  (exec {ea} µs, max {em} µs)")
    ax1.set_xlabel("唤醒抖动（µs，1 µs 桶）")
    ax1.set_ylabel("累积占比 CDF")
    ax1.set_title(os.environ.get(
        "AX1_TITLE", "CDF：NuttX 在 6 µs 一步到位，Linux 在 20 µs 才爬上去"))
    ax1.set_ylim(0, 1.02)
    ax1.grid(alpha=0.3)
    ax1.legend(loc="lower right", fontsize=8.5)

    # ---- 右：分位数柱状图（log 轴）----
    qs = [("p50", 0.50), ("p90", 0.90), ("p99", 0.99), ("p99.9", 0.999), ("max", 1.0)]
    labels = [it[0] for it in items]
    width = 0.8 / len(qs)
    x = np.arange(len(items))
    for i, (qname, q) in enumerate(qs):
        vals = []
        for _, v, _, _ in items:
            idx = min(len(v) - 1, int(np.floor(q * (len(v) - 1))))
            vals.append(np.sort(v)[idx])
        ax2.bar(x + (i - (len(qs) - 1) / 2) * width, vals, width,
                label=qname)
        for xi, vi in zip(x + (i - (len(qs) - 1) / 2) * width, vals):
            ax2.text(xi, vi * 1.08, f"{vi:g}", ha="center", va="bottom",
                     fontsize=6.5, rotation=90)
    ax2.set_yscale("log")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, fontsize=8.5, rotation=12)
    ax2.set_ylabel("抖动（µs，对数轴）")
    ax2.set_title(os.environ.get(
        "AX2_TITLE", "分位数对比：中位差 3.3×，极值差 7–18×"))
    ax2.grid(alpha=0.3, axis="y")
    ax2.legend(fontsize=8, ncol=5, loc="upper left")
    ax2.set_ylim(3, 300)

    fig.suptitle(os.environ.get(
        "SUBTITLE",
        "同一份 bm2pid_main.c · Linux root cell（CPU3） vs Jailhouse+NuttX cell（CPU1）"
        " · 1 kHz · 100k 周期"), fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out, dpi=140)
    print(f"-> {out}")
    for label, v, ea, em in items:
        s = np.sort(v)
        print(f"  {label}: n={v.size} min={s[0]} p50={s[s.size//2]} "
              f"p99.9={s[int(s.size*0.999)]} max={s[-1]} | exec avg={ea} max={em}")


if __name__ == "__main__":
    main()
