#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_cdf_plot__三域CDF.py — 用统一探针的稀疏直方图画三域 response CDF

为什么单独一个脚本：
  `# stat_*` 只有 6 个分位点，连不出可信的 CDF；尾部形态（重尾 vs 点分布）
  恰恰是这批实验最重要的结论。所以探针块补了 `# hist_<series>_us: <桶> <计数>`。

用法：
  python probe_cdf_plot__三域CDF.py OUT.png "标签:日志路径" "标签:日志路径" ...

例：
  python probe_cdf_plot__三域CDF.py out/three_domain_cdf.png \
      "内核态 ko:crtos/results/raw/rsf/kernel_host_k1.log" \
      "Linux root cell:crtos/results/raw/rsf/rsf_L1.log" \
      "NuttX cell:crtos/results/raw/bm2_nuttx_60000_20261023_115921.log"

说明：
  - 同时吃用户态日志（行首 `# hist_...`）与内核 dmesg（`[时间戳] PLCPROBE # hist_...`）
  - 只画 response（端到端响应 R）；需要别的序列改 SERIES
  - 某个日志缺直方图 → 直接报错退出，不静默跳过（缺数据画出来的图会误导）
"""
import io
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                          "Noto Sans CJK SC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

SERIES = "response"
PREFIX = re.compile(r"^\s*(?:\[\s*[\d.]+\]\s*)?(?:PLCPROBE\s+)?")
HIST = re.compile(r"^#\s*hist_(\w+)_us:\s*(\S+)\s+(\d+)\s*$")


def load_hist(path, series=SERIES):
    """返回 [(桶µs, 计数)]，桶可能含 'over'（表示 >= 桶顶）。"""
    if not os.path.exists(path):
        raise SystemExit(f"找不到 {path}")
    buckets, over = {}, 0
    for raw in io.open(path, encoding="utf-8", errors="replace"):
        ln = PREFIX.sub("", raw.rstrip("\n"))
        m = HIST.match(ln)
        if not m or m.group(1) != series:
            continue
        k, v = m.group(2), int(m.group(3))
        if k == "over":
            over += v
        else:
            buckets[int(k)] = buckets.get(int(k), 0) + v
    if not buckets and not over:
        raise SystemExit(
            f"{path} 里没有 `# hist_{series}_us` 行。\n"
            f"  探针版本太旧（只有 stat_ 摘要），请用补了 plc_probe_hist() 的版本重跑。")
    xs, cs = [], []
    total = sum(buckets.values()) + over
    acc = 0
    for k in sorted(buckets):
        acc += buckets[k]
        xs.append(k)
        cs.append(acc / total)
    # over（>= 桶顶的样本）**不**并进曲线：
    # 拼成水平线会让人以为"从桶顶到无穷都有这个超出概率"，而我们只知道它 >= 桶顶。
    return xs, cs, total, over


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        raise SystemExit(__doc__)
    out = args[0]
    specs = []
    for a in args[1:]:
        if ":" not in a:
            raise SystemExit(f"参数格式应为 标签:路径，收到 {a}")
        label, path = a.split(":", 1)
        specs.append((label, path))

    data = []
    for label, path in specs:
        xs, cs, n, over = load_hist(path)
        data.append((label, xs, cs, n, over))
        print(f"{label:22s} n={n:>8d}  over={over:>3d}  非零桶={len(xs)}")

    # ---- 图 1：CDF（线性），尾部最能说明问题 ----
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.6),
                                   gridspec_kw={"width_ratios": [1.25, 1]})

    colors = ["#c0392b", "#2471a3", "#1e8449", "#7d3c98", "#b9770e"]
    for i, (label, xs, cs, n, over) in enumerate(data):
        c = colors[i % len(colors)]
        ax1.step(xs, cs, where="post", label=f"{label}（n={n:,}）",
                 color=c, linewidth=1.9)
        ax2.step(xs, [1.0 - v for v in cs], where="post", color=c,
                 linewidth=1.9, label=label)

    ax1.annotate("NuttX：6–12 µs\n99.95% 落在 7 µs",
                 xy=(9, 0.999), xytext=(13.5, 0.55),
                 fontsize=8.5, color="#1e8449",
                 arrowprops=dict(arrowstyle="->", color="#1e8449", lw=1.2))
    ax1.set_xlabel("端到端响应 R（µs，1 µs 桶）")
    ax1.set_ylabel("累积概率 CDF")
    ax1.set_title("三域 response CDF：Linux 侧爬坡，NuttX 侧一步到位", fontsize=11)
    ax1.set_xlim(0, 45)
    ax1.set_ylim(0, 1.02)
    ax1.grid(alpha=0.3, linestyle=":")
    # 不要画 Q0.999 水平虚线：它和 NuttX 曲线在 1.0 附近重合，会被误读成同一条线
    ax1.legend(loc="lower right", fontsize=8.5)

    # ---- 图 2：尾部（对数纵轴），看谁有长尾 ----
    ax2.set_xlabel("端到端响应 R（µs）")
    ax2.set_ylabel("超出比例 P(R > x)（对数）")
    ax2.set_title("尾部放大：NuttX 在 8.4 µs 之后直接归零", fontsize=11)
    ax2.set_yscale("log")
    # 放到 210：直方图桶顶是 199 µs，那里可能有一个孤立桶。
    # 若只画到 130，CDF 的水平段会被误读成"这里一直有样本"。
    ax2.set_xlim(5, 210)
    ax2.set_ylim(1e-5, 1.2)
    ax2.grid(alpha=0.3, linestyle=":", which="both")
    ax2.legend(loc="upper right", fontsize=8.5)

    over_note = "；".join(f"{lb} 另有 {ov} 个样本 ≥199 µs（未计入曲线）"
                          for lb, xs, cs, n, ov in data if ov)
    if over_note:
        fig.text(0.5, 0.015, over_note + "（直方图桶顶 199 µs）",
                 ha="center", fontsize=8, color="#7b241c")
    fig.suptitle("同时钟（CLOCK_MONOTONIC）· 同周期 1 kHz · 同窗口 60 s · 同 1 µs 桶",
                 fontsize=10.5)
    fig.tight_layout(rect=(0, 0.04 if over_note else 0, 1, 0.94))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    fig.savefig(out, dpi=160)
    print(f"\n写出 {out}")


if __name__ == "__main__":
    main()
