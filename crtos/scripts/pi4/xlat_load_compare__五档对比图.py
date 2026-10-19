#!/usr/bin/env python3
# xlat_load_compare__五档对比图.py
#
# 读 xlat_load_matrix 产出的 5 个 XLATREC1 文件，画一页对比图：
#   左：五档抖动的分布曲线（直方图，log y）
#   右：五档的分位数柱状图（p50 / p99 / p99.9 / abs_max）
#
# 用法:
#   python3 xlat_load_compare__五档对比图.py <结果目录> <输出.png>
#   结果目录内应有 load_{noload,cpu,cache,vmem,stream}.bin

import struct
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文字体（Windows 本地出图；Pi 侧可退化为 Noto）
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

REC = np.dtype([("lat", "<i8"), ("jit", "<i4"), ("seq", "<u4")])
FILE_HDR = 32
MODES = ["noload", "cpu", "cache", "vmem", "stream"]
LABEL = {"noload": "空载", "cpu": "CPU", "cache": "Cache", "vmem": "VMem", "stream": "Stream"}
COLORS = ["#185FA5", "#A32D2D", "#854F0B", "#3B6D11", "#534AB7"]


def load(path: Path):
    d = path.read_bytes()
    if d[:8] != b"XLATREC1":
        raise SystemExit(f"{path}: 非 XLATREC1")
    _v, rec, n, iv = struct.unpack("<IIQQ", d[8:32])
    arr = np.frombuffer(d[FILE_HDR:FILE_HDR + n * rec], dtype=REC)
    return arr["jit"].astype(np.int64), arr["lat"].astype(np.int64)


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    root = Path(sys.argv[1])
    out = Path(sys.argv[2])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.0))

    rows = []
    for mode, color in zip(MODES, COLORS):
        p = root / f"load_{mode}.bin"
        if not p.is_file():
            print(f"skip {mode}: 缺文件")
            continue
        jit, _lat = load(p)
        us = jit / 1000.0
        rows.append((mode, us, color))

        lo = max(0.0, np.percentile(us, 0.01) - 0.5)
        hi = np.percentile(us, 99.99) + 0.5
        bins = np.linspace(lo, hi, 260)
        ax1.hist(us, bins=bins, histtype="step", linewidth=1.4,
                 color=color, label=f"{LABEL[mode]}  n={us.size:,}")

    ax1.set_yscale("log")
    ax1.set_xlabel("inmate 域内抖动 (µs)")
    ax1.set_ylabel("样本数（log）")
    ax1.set_title("五档负载下的抖动分布", fontsize=11)
    ax1.legend(fontsize=8, frameon=False)
    ax1.grid(alpha=0.25, linewidth=0.5)

    # 右侧：分位数柱状图（abs_max 用对数轴更公平）
    qs = ["p50", "p99", "p99.9", "abs_max"]
    width = 0.2
    x = np.arange(len(qs))
    for i, (mode, us, color) in enumerate(rows):
        vals = [np.percentile(us, 50), np.percentile(us, 99),
                np.percentile(us, 99.9), us.max()]
        ax2.bar(x + (i - 2) * width, vals, width, color=color,
                label=LABEL[mode], edgecolor="white", linewidth=0.5)

    ax2.set_xticks(x)
    ax2.set_xticklabels(qs, fontsize=9)
    ax2.set_ylabel("抖动 (µs)")
    ax2.set_title("分位数与最坏值对比", fontsize=11)
    ax2.set_yscale("log")
    ax2.grid(alpha=0.25, linewidth=0.5, axis="y")
    ax2.legend(fontsize=8, frameon=False, ncol=5, loc="upper left")

    fig.suptitle("Pi4B / NuttX inmate / 1 kHz / 每档 15 min；负载施加在 root cell 的 CPU 0,2,3",
                 fontsize=10, y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out, dpi=150)
    print(f"-> {out}")

    print(f"{'tier':8s} {'n':>10s} {'p50':>8s} {'p99':>8s} {'p99.9':>8s} {'max':>9s}")
    for mode, us, _c in rows:
        print(f"{mode:8s} {us.size:10,d} {np.percentile(us,50):8.3f} "
              f"{np.percentile(us,99):8.3f} {np.percentile(us,99.9):8.3f} {us.max():9.3f}")


if __name__ == "__main__":
    main()
