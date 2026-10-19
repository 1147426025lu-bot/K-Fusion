#!/usr/bin/env python3
# cyclictest_hist_compare__同尺子窗口对比.py
#
# 把 cyclictest -q -h 输出的直方图（"000003 000001" 六位格式）画成对比图，
# 用于展示「同一把尺子、不同窗口」的结果是否一致。
#
# 用法:
#   python3 cyclictest_hist_compare__同尺子窗口对比.py <out.png> <label:log1> <label:log2> [...]
# 例:
#   ... out.png "100 s (10万次):.../100s.log" "1 h (360万次):.../1h.log"

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

COLORS = ["#185FA5", "#A32D2D", "#854F0B", "#3B6D11", "#534AB7"]


def parse_hist(path: Path):
    bins, cnt = [], []
    for ln in path.read_text(errors="ignore").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        bins.append(int(parts[0]))
        cnt.append(int(parts[1]))
    if not bins:
        raise SystemExit(f"{path}: 未解析到直方图")
    total = sum(cnt)
    nz = [(b, c) for b, c in zip(bins, cnt) if c > 0]
    return bins, cnt, total, nz


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1])
    series = []
    for spec in sys.argv[2:]:
        label, _, path = spec.partition(":")
        bins, cnt, total, nz = parse_hist(Path(path))
        series.append((label, bins, cnt, total, nz))

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.0, 4.6),
                                  gridspec_kw={"width_ratios": [1.55, 1]})

    for i, (label, bins, cnt, total, nz) in enumerate(series):
        color = COLORS[i % len(COLORS)]
        # step 折线（避免 bar 在小桶宽下糊成一团）
        xs = [b for b, _ in nz]
        ys = [c for _, c in nz]
        ax.step(xs, ys, where="mid", linewidth=1.6, color=color,
                label=f"{label}  (min {xs[0]} / max {xs[-1]} µs)")
        ax.scatter(xs, ys, s=18, color=color, zorder=3)

    ax.set_yscale("log")
    ax.set_xlabel("延迟（µs，1 µs 桶）")
    ax.set_ylabel("样本数（log）")
    ax.set_title("cyclictest 直方图：同一把尺子、不同窗口", fontsize=11)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25, linewidth=0.5)

    # 右：占百分比（归一化后对比形状）
    for i, (label, bins, cnt, total, nz) in enumerate(series):
        color = COLORS[i % len(COLORS)]
        xs = [b for b, _ in nz]
        ys = [c / total * 100 for _, c in nz]
        ax2.step(xs, ys, where="mid", linewidth=1.6, color=color, label=label)

    ax2.set_xlabel("延迟（µs）")
    ax2.set_ylabel("占比（%）")
    ax2.set_title("归一化形状对比", fontsize=11)
    ax2.legend(fontsize=8, frameon=False)
    ax2.grid(alpha=0.25, linewidth=0.5)

    fig.suptitle("Pi4B / NuttX inmate / 1 kHz / cyclictest -q / 无负载", fontsize=10, y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out, dpi=150)
    print(f"-> {out}")

    for label, bins, cnt, total, nz in series:
        mx = nz[-1][0]
        print(f"{label:28s} total={total:>9,d}  min={nz[0][0]}µs  max={mx}µs  "
              f"非零桶={len(nz)}  max计数={nz[-1][1]}")


if __name__ == "__main__":
    main()
