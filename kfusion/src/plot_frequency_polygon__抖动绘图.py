#!/usr/bin/env python3
r"""plot_frequency_polygon__抖动绘图.py

从测量产物生成 K-Fusion 论文同款的「抖动分布图」与「延迟时间线图」。

被 scripts/paper/plot_dual_from_artifact__双图出图.sh 调用，CLI 契约固定为：
    plot_frequency_polygon__抖动绘图.py
        (--input-jitter-bin JITTER.BIN | --input-log LOG)
        --output DIST.png [--latency-output TIME.png]

支持的输入格式（自动识别）：
  1) K-Fusion 内核 ringbuf 导出的 jitter.bin（magic 0x504C434A, version 1/2）
        —— v1=40B 头、v2=56B 头（多 3 个 hist 字段），sample_count 同位于 offset 32；
           v2 与 plc_runner_official__cyclictest宿主.c 的 struct fused_ring_file_hdr 同源
  2) 日志内 `Jitter: <int>` 行（fused 测量格式，单位 ns）
  3) rtlat 逐样本行 `rtlat SAMPLE <idx> <ns>`（带符号 ns）
  4) rtlat 全样本直方图 `rtlat HIST bin_ns=<b> lo_ns=<l> nbins=<N>` +
     `rtlat HISTDATA <bin>:<count> ...`  —— **由全部样本累加，非抽样**
  5) cyclictest -h 直方图块（`^(\d{6})\s+(\d{6})$`，仅可出分布图）

关键口径：
  - 分布/统计优先用「全样本直方图」（若存在），保证 N = 周期数；
  - 时间线用逐样本序列（可能按 -d 步长抽取，图注标明步长与点数）；
  - jitter 为**带符号**偏差（ns），负值=提前唤醒，与 cyclictest 一致。
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

PURPLE = "#8b5cf6"
PURPLE_FILL = "#ede9fe"
C_AVG = "#2563eb"
C_MIN = "#16a34a"
C_MAX = "#dc2626"
C_P99 = "#ea580c"

JITTER_BIN_MAGIC = 0x504C434A
JITTER_BIN_VERSION = 1


# --------------------------------------------------------------------------- #
# 解析层
# --------------------------------------------------------------------------- #
def parse_jitter_bin(path: Path) -> list[int]:
    """K-Fusion ringbuf jitter.bin v1/v2 → 带符号 ns 样本列表。

    头部布局（magic=0x504C434A）：
      v1 (40 B): <IIQqqII>   magic,I / version,I / cycles,Q / min_ns,q /
                            max_ns,q / sample_count,I / reserved,I
      v2 (56 B): <IIQqqIIqq> v1 前 40 B 完全相同，追加 hist_bins,I /
                            hist_lo_ns,q / hist_step_ns,q；样本体从 offset 56 起。
    sample_count 在两版都位于 offset 32（4 B, <I），故两版共用同一读取位置。
    """
    if not path.is_file():
        return []
    data = path.read_bytes()
    if len(data) < 8:
        return []
    magic, version = struct.unpack("<II", data[:8])
    if magic != JITTER_BIN_MAGIC:
        return []
    if version == 1:
        hdr_len = 40
    elif version == 2:
        hdr_len = 56
    else:
        return []
    if len(data) < hdr_len:
        return []
    sample_count = struct.unpack("<I", data[32:36])[0]
    body = data[hdr_len:hdr_len + sample_count * 8]
    if len(body) < sample_count * 8:
        return []
    return list(struct.unpack(f"<{sample_count}q", body))


def parse_log(path: Path) -> dict:
    """从日志解析：逐样本序列 / 全样本直方图 / 元信息。"""
    out = {
        "samples": [],          # [(idx, ns), ...]  逐样本
        "hist": None,           # (bin_ns, lo_ns, {bin_index: count})
        "clkres_ns": None,
        "stats": None,          # rtlat STATS 行原文
        "stride": None,
        "interval_us": None,
        "loops": None,
        "hist_n": None,         # 直方图总样本数
        "clt": None,            # cyclictest 汇总行（Total/Min/Avg/Max/Overflows）
    }
    if not path.is_file():
        return out

    text = path.read_text(encoding="utf-8", errors="replace")
    # 去掉 ANSI 控制序列：cyclictest 非 quiet 模式会发 ESC[<n>A 做原地刷新，
    # 原始串口抓取里它们是散落的噪声字节。
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)

    m = re.search(r"rtlat CLKRES granularity_ns=(-?\d+)", text)
    if m:
        out["clkres_ns"] = int(m.group(1))

    m = re.search(r"rtlat START interval=(\d+)us loops=(\d+).*?stride=(\d+)", text)
    if m:
        out["interval_us"] = int(m.group(1))
        out["loops"] = int(m.group(2))
        out["stride"] = int(m.group(3))

    m = re.search(r"rtlat STATS .*", text)
    if m:
        out["stats"] = m.group(0).strip()

    # rtlat 全样本直方图
    mh = re.search(r"rtlat HIST bin_ns=(-?\d+) lo_ns=(-?\d+) nbins=(\d+)", text)
    md = re.search(r"rtlat HISTDATA([^\n]*)", text)
    if mh and md:
        bin_ns = int(mh.group(1))
        lo_ns = int(mh.group(2))
        counts: dict[int, int] = {}
        total = 0
        for tok in md.group(1).split():
            if ":" not in tok:
                continue
            b, c = tok.split(":", 1)
            try:
                counts[int(b)] = int(c)
                total += int(c)
            except ValueError:
                continue
        if counts:
            out["hist"] = (bin_ns, lo_ns, counts)
            out["hist_n"] = total

    # 逐样本
    smp = re.findall(r"rtlat SAMPLE (\d+) (-?\d+)", text)
    if smp:
        out["samples"] = [(int(a), int(b)) for a, b in smp]
    # fused 测量格式：Jitter: <ns>
    if not out["samples"]:
        jl = re.findall(r"Jitter:\s*(-?\d+)", text)
        if jl:
            out["samples"] = [(i, int(v)) for i, v in enumerate(jl)]

    # cyclictest -h 直方图（仅分布）
    if out["hist"] is None and "# Histogram" in text:
        pairs: dict[int, int] = {}
        for line in text.splitlines():
            # 计数按 %06d 打印：短跑是 6 位，长跑（如 1h/360 万次）会到 7 位以上，
            # 所以**不能用 \d{6}**，否则长跑直方图会被整块跳过。
            mm = re.match(r"^(\d+)\s+(\d+)$", line.strip())
            if mm:
                pairs[int(mm.group(1))] = int(mm.group(2))
        if pairs:
            # cyclictest 直方图单位为 us → 转成 ns 桶
            out["hist"] = (1000, 0, pairs)
            out["hist_n"] = sum(pairs.values())

    # cyclictest 汇总行（**权威统计**）：直方图以 1us 为桶，桶中心与 cyclictest
    # 直接报告的值存在 ≤0.5us 的取整差，故两者都保留、以报告值为准。
    m = re.search(r"#\s*Total:\s*(\d+)", text)
    if m:
        clt: dict[str, int] = {"total": int(m.group(1))}
        for key, pat in (
            ("min", r"#\s*Min Latencies:\s*(\d+)"),
            ("avg", r"#\s*Avg Latencies:\s*(\d+)"),
            ("max", r"#\s*Max Latencies:\s*(\d+)"),
            ("overflow", r"#\s*Histogram Overflows:\s*(\d+)"),
        ):
            mm2 = re.search(pat, text)
            if mm2:
                clt[key] = int(mm2.group(1))
        if len(clt) >= 5:
            out["clt"] = clt

    # cyclictest **非 quiet** 状态行（每 ~100 ms 一行），形如：
    #   T: 0 (    4) P:200 I:1000 C:     99 Min:      4 Act:    5 Avg:    4 Max:       6
    # C = 已完成周期数、Act = 最近一个周期的实测延迟(us) → 可直接出延迟时间线。
    # ⚠ 这是 verbose 模式的数据：打印动作本身会干扰 RT 路径，max 会明显虚高
    #   （Pi4B 实测 20 s：quiet max=9us vs verbose max=64us，7 倍）。引用必须标注。
    if not out["samples"]:
        rows = re.findall(
            r"T:\s*(\d+)\s*\(\s*(\d+)\s*\)\s*P:\s*(\d+)\s*I:\s*(\d+)\s+"
            r"C:\s*(\d+)\s+Min:\s*(\d+)\s+Act:\s*(\d+)\s+Avg:\s*(\d+)\s+Max:\s*(\d+)",
            text,
        )
        if rows:
            out["samples"] = [(int(r[4]), int(r[6]) * 1000) for r in rows]
            out["clt_verbose"] = {
                "n": len(rows),
                "cycles_first": int(rows[0][4]),
                "cycles_last": int(rows[-1][4]),
                "final_max_us": int(rows[-1][8]),
            }

    return out


# --------------------------------------------------------------------------- #
# 数据整形
# --------------------------------------------------------------------------- #
def hist_to_arrays(hist: tuple) -> tuple[np.ndarray, np.ndarray]:
    """(bin_ns, lo_ns, {idx:count}) → (中心值 ns, 计数) 升序数组。

    ⚠ **丢弃计数为 0 的空桶**：cyclictest `-h N` 会打印全部 N 个桶（大量为 0），
    若把空桶算进统计，导出的 min/max 会被拉到 0.5us / (N-0.5)us 这种**虚假范围**。
    """
    bin_ns, lo_ns, counts = hist
    nz = sorted((int(i), int(c)) for i, c in counts.items() if int(c) > 0)
    if not nz:
        return np.array([], dtype=float), np.array([], dtype=float)
    idxs = np.array([i for i, _ in nz], dtype=float)
    cnts = np.array([c for _, c in nz], dtype=float)
    centers = lo_ns + (idxs + 0.5) * bin_ns
    return centers, cnts


def fold_absolute(centers_ns: np.ndarray, cnts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """按 |x| 折叠成绝对抖动分布（桶宽 = 原桶宽）。"""
    absx = np.abs(centers_ns)
    order = np.argsort(absx)
    absx, cnts = absx[order], cnts[order]
    # 归并到同一桶（原桶宽）
    keys = np.round(absx).astype(np.int64)
    uniq, inv = np.unique(keys, return_inverse=True)
    acc = np.zeros(len(uniq), dtype=float)
    np.add.at(acc, inv, cnts)
    return uniq.astype(float), acc


def weighted_percentile(centers: np.ndarray, cnts: np.ndarray, q: float) -> float:
    if len(centers) == 0:
        return 0.0
    order = np.argsort(centers)
    x, c = centers[order], cnts[order]
    cum = np.cumsum(c) / c.sum()
    k = np.searchsorted(cum, q)
    k = min(max(k, 0), len(x) - 1)
    return float(x[k])


def stats_from_hist(centers: np.ndarray, cnts: np.ndarray) -> dict:
    n = float(cnts.sum())
    if n == 0:
        return {}
    avg = float((centers * cnts).sum() / n)
    return {
        "n": int(n),
        "min": float(centers.min()),
        "max": float(centers.max()),
        "avg": avg,
        "p99": weighted_percentile(centers, cnts, 0.99),
        "maxabs": float(np.abs(centers).max()),
        "p99abs": weighted_percentile(np.abs(centers), cnts, 0.99),
    }


# --------------------------------------------------------------------------- #
# 绘图
# --------------------------------------------------------------------------- #
def _step_hist(ax, x_us, cnts, logy: bool):
    ax.step(x_us, cnts, where="mid", color=PURPLE, linewidth=1.2)
    ax.fill_between(x_us, cnts, step="mid", color=PURPLE_FILL, alpha=0.85)
    if logy:
        ax.set_yscale("log")
        # 顶部留 3x 余量：统计框放在右上角空白处，避免压住直方图主体
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi * 3.0)
    ax.grid(True, linestyle=":", alpha=0.45)
    ax.set_ylabel("Samples (log)" if logy else "Samples")


def _apply_display_range(ax, centers_ns: np.ndarray, cnts: np.ndarray,
                         signed: bool, full: bool = False) -> bool:
    """设定**显示**范围（统计始终用全样本）。

    full=False（线性面板）：按 0.1%..99.9% 分位收窄，让主体分布的形状/紧密度可见；
    full=True （log-y 面板）：显示全范围，让离群尾部可见。
    两者配合：线性面板回答"抖多紧"，log-y 面板回答"尾巴多长"。
    返回是否发生裁剪。
    """
    vals = centers_ns if signed else np.abs(centers_ns)
    d_lo, d_hi = float(vals.min()), float(vals.max())
    if full:
        margin = max((d_hi - d_lo) * 0.05, 200.0)
        xlo, xhi = d_lo - margin, d_hi + margin
        if xhi <= xlo:
            xhi = xlo + 1000.0
        ax.set_xlim(xlo / 1000.0, xhi / 1000.0)
        return False
    lo_p = weighted_percentile(vals, cnts, 0.001)
    hi_p = weighted_percentile(vals, cnts, 0.999)
    span = max(hi_p - lo_p, 200.0)
    margin = span * 0.10
    xlo, xhi = lo_p - margin, hi_p + margin
    if xhi <= xlo:
        xlo, xhi = d_lo, d_hi if d_hi > d_lo else d_lo + 1000.0
    clipped = (d_lo < xlo) or (d_hi > xhi)
    ax.set_xlim(xlo / 1000.0, xhi / 1000.0)
    return clipped


def plot_distribution(centers_ns, cnts, meta: dict, out_png: Path,
                      out_svg: bool, title_note: str) -> None:
    st = stats_from_hist(centers_ns, cnts)
    if not st:
        raise SystemExit("no usable samples for distribution")

    # 桶宽 >= 1us 时（cyclictest 就是 1us 桶），桶内分布未知：
    # 用**桶中心**当代表值会系统性高估 0.5us，与 cyclictest 自己的 "µs 截断" 口径对不上
    # （实测：桶中心均值 5.45us vs 工具报告 avg 4us）。改用**桶下沿**作代表值，两边即自洽
    # （下沿均值 4.95us vs 报告 4us，差 0.95us 正是截断所致）。
    step_ns = float(centers_ns[1] - centers_ns[0]) if len(centers_ns) > 1 else 0.0
    coarse = step_ns >= 1000.0
    centers_plot = (centers_ns - step_ns / 2.0) if coarse else centers_ns
    st_plot = stats_from_hist(centers_plot, cnts) if coarse else st

    x_us = centers_plot / 1000.0
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), facecolor="white")
    fig.suptitle("Full Jitter Distribution Histogram", fontsize=14, fontweight="bold")

    # 左上：带符号（线性）
    ax = axes[0][0]
    _step_hist(ax, x_us, cnts, logy=False)
    ax.axvline(st_plot["avg"], color=C_AVG, ls="--", lw=1.2, label="avg")
    ax.axvline(st_plot["min"], color=C_MIN, ls=":", lw=1.2, label="min")
    ax.axvline(st_plot["max"], color=C_MAX, ls="--", lw=1.2, label="max")
    ax.set_title("Signed Jitter Histogram", fontsize=12, fontweight="bold")
    ax.set_xlabel("jitter (us)")
    ax.legend(fontsize=8, loc="upper left")
    clipped = _apply_display_range(ax, centers_plot, cnts, signed=True)

    # 右上：带符号（log-y）+ 统计文本框
    ax = axes[0][1]
    _step_hist(ax, x_us, cnts, logy=True)
    ax.axvline(st_plot["avg"], color=C_AVG, ls="--", lw=1.2)
    ax.axvline(st_plot["min"], color=C_MIN, ls=":", lw=1.2)
    ax.axvline(st_plot["max"], color=C_MAX, ls="--", lw=1.2)
    ax.set_title("Signed Jitter Histogram (log-y)", fontsize=12, fontweight="bold")
    ax.set_xlabel("jitter (us)")
    _apply_display_range(ax, centers_plot, cnts, signed=True, full=True)
    # 统计框：**刻意保持短**——长框会把直方图主体盖住（实测 11 行的版本几乎糊满右上格）。
    # 全为非负数据时（cyclictest 的 latency 即如此）signed 与 absolute 完全重合，只列一行。
    nonneg = float(centers_plot.min()) >= 0.0
    box_lines = [f"samples = {st_plot['n']:,}"]
    unit = "us bins, lower edge" if coarse else ""
    box_lines.append(
        f"min/avg/max = {st_plot['min']/1000:.2f} / {st_plot['avg']/1000:.2f} / "
        f"{st_plot['max']/1000:.2f} us" + (f"  ({unit})" if unit else "")
    )
    if nonneg:
        box_lines.append(f"p99 = {st_plot['p99abs']/1000:.2f} us   (>=0: signed==absolute)")
    else:
        box_lines.append(
            f"p99 = {st_plot['p99abs']/1000:.2f} us   |max| = {st_plot['maxabs']/1000:.2f} us"
        )
    clt_m = meta.get("clt")
    if clt_m:
        box_lines.append(
            f"cyclictest: {clt_m['min']}/{clt_m['avg']}/{clt_m['max']} us "
            f"(us-truncated), overflows {clt_m['overflow']}"
        )
    box_lines.append(f"source: {meta.get('source', '?')}")
    box = "\n".join(box_lines)
    ax.text(0.975, 0.965, box, transform=ax.transAxes, ha="right", va="top",
            fontsize=7.4, family="monospace", linespacing=1.35,
            bbox=dict(boxstyle="round,pad=0.32", fc="white", ec="#aaa", lw=0.6,
                      alpha=0.88))

    # 左下：绝对（线性）
    axabs, cabs = fold_absolute(centers_plot, cnts)
    ax = axes[1][0]
    _step_hist(ax, axabs / 1000.0, cabs, logy=False)
    ax.axvline(st_plot["p99abs"] / 1000.0, color=C_P99, ls="--", lw=1.2, label="p99")
    ax.axvline(st_plot["maxabs"] / 1000.0, color=C_MAX, ls="--", lw=1.2, label="max")
    ax.set_title("Absolute Jitter Histogram", fontsize=12, fontweight="bold")
    ax.set_xlabel("|jitter| (us)")
    ax.legend(fontsize=8, loc="upper right")
    clipped_abs = _apply_display_range(ax, axabs, cabs, signed=False)

    # 右下：绝对（log-y）
    ax = axes[1][1]
    _step_hist(ax, axabs / 1000.0, cabs, logy=True)
    ax.axvline(st_plot["p99abs"] / 1000.0, color=C_P99, ls="--", lw=1.2)
    ax.axvline(st_plot["maxabs"] / 1000.0, color=C_MAX, ls="--", lw=1.2)
    ax.set_title("Absolute Jitter Histogram (log-y)", fontsize=12, fontweight="bold")
    ax.set_xlabel("|jitter| (us)")
    _apply_display_range(ax, axabs, cabs, signed=False, full=True)

    note = (title_note + " | linear panels: x zoomed to p0.1-p99.9; "
            "log-y panels: full range (tail visible)")
    if coarse:
        note += (" | 1-us bins: values shown at the LOWER edge, matching cyclictest's "
                 "us-truncation (a bucket-center reading would overstate by 0.5 us)")
    fig.text(0.5, 0.005, note, ha="center", fontsize=8, color="#555")

    fig.tight_layout(rect=(0, 0.015, 1, 0.965))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, bbox_inches="tight", facecolor="white")
    if out_svg:
        fig.savefig(out_png.with_suffix(".svg"), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"jitter distribution -> {out_png}")


def plot_timeline(samples, meta: dict, out_png: Path, out_svg: bool,
                  title_note: str) -> bool:
    if not samples:
        print("(no per-sample series; latency timeline skipped)", file=sys.stderr)
        return False

    idx = np.array([s[0] for s in samples], dtype=float)
    val = np.array([s[1] for s in samples], dtype=float) / 1000.0  # ns -> us
    avg, mn, mx = float(val.mean()), float(val.min()), float(val.max())

    fig, ax = plt.subplots(figsize=(11, 3.6), facecolor="white")
    ax.plot(idx, val, color=PURPLE, lw=0.5, alpha=0.9, label="latency")
    ax.axhline(avg, color=C_AVG, ls="--", lw=1.1, label="avg")
    ax.axhline(mx, color=C_MAX, ls="--", lw=1.1, label="max")
    ax.axhline(mn, color=C_MIN, ls=":", lw=1.1, label="min")
    ax.set_xlabel("Sample Index")
    ax.set_ylabel("Latency / jitter (us)")
    ax.set_title("Latency Timeline", fontsize=13, fontweight="bold")
    ax.grid(True, linestyle=":", alpha=0.45)
    ax.legend(fontsize=8, loc="upper right", ncol=4)

    if title_note:
        fig.text(0.5, 0.005, title_note, ha="center", fontsize=8, color="#555")

    fig.tight_layout(rect=(0, 0.03, 1, 1))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, bbox_inches="tight", facecolor="white")
    if out_svg:
        fig.savefig(out_png.with_suffix(".svg"), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"latency timeline    -> {out_png}")
    return True


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="K-Fusion jitter / latency plotting")
    ap.add_argument("--input-jitter-bin", default="")
    ap.add_argument("--input-log", default="")
    ap.add_argument("--output", required=True, help="jitter distribution png")
    ap.add_argument("--latency-output", default="", help="latency timeline png")
    ap.add_argument("--label", default="", help="dataset label for footnote")
    ap.add_argument("--svg", action="store_true", help="also write .svg next to png")
    args = ap.parse_args()

    if not args.input_jitter_bin and not args.input_log:
        print("need --input-jitter-bin or --input-log", file=sys.stderr)
        return 2

    samples: list = []
    hist = None
    clkres = None
    clt = None
    meta = {"source": "?"}

    if args.input_jitter_bin:
        p = Path(args.input_jitter_bin)
        vals = parse_jitter_bin(p)
        if vals:
            samples = list(enumerate(vals))
            meta["source"] = f"jitter.bin ({p.name})"
        else:
            print(f"jitter.bin unreadable/empty: {p}", file=sys.stderr)

    if args.input_log:
        p = Path(args.input_log)
        info = parse_log(p)
        if info["clkres_ns"]:
            clkres = info["clkres_ns"]
        if info["hist"]:
            hist = info["hist"]
            meta["source"] = f"histogram ({p.name})"
        if info.get("clt"):
            clt = info["clt"]
            meta["clt"] = clt
            meta["source"] = f"cyclictest -h histogram ({p.name})"
        if info["samples"]:
            if not samples:
                samples = info["samples"]
            if hist is None:
                meta["source"] = f"per-sample ({p.name})"
            meta["stride"] = info.get("stride")
        if info.get("clt_verbose"):
            cv = info["clt_verbose"]
            meta["clt_verbose"] = cv
            if hist is None:
                meta["source"] = f"cyclictest verbose status ({p.name})"

    # 分布：优先全样本直方图
    if hist is not None:
        centers, cnts = hist_to_arrays(hist)
        meta["hist_n"] = int(cnts.sum())
    elif samples:
        vals = np.array([s[1] for s in samples], dtype=float)
        meta["source"] = meta["source"] + " [from per-sample]"
        centers, cnts = None, None
    else:
        print("no usable data", file=sys.stderr)
        return 1

    notes = []
    if args.label:
        notes.append(args.label)
    if clkres:
        notes.append(f"clock granularity={clkres} ns")
    if clt:
        notes.append(
            f"cyclictest reported: Min={clt['min']}us Avg={clt['avg']}us "
            f"Max={clt['max']}us Overflows={clt['overflow']}"
        )
    if meta.get("hist_n"):
        notes.append(f"distribution N={meta['hist_n']} (all samples, no decimation)")
    if samples and meta.get("stride"):
        notes.append(f"timeline: {len(samples)} pts (stride={meta['stride']})")
    if meta.get("clt_verbose"):
        cv = meta["clt_verbose"]
        notes.append(
            f"timeline: {cv['n']} cyclictest status lines "
            f"(cycles {cv['cycles_first']}..{cv['cycles_last']}); "
            "VERBOSE MODE - printing itself perturbs the RT path (max inflated)"
        )
    note = " | ".join(notes)

    if centers is not None:
        plot_distribution(centers, cnts, meta, Path(args.output), args.svg, note)
    else:
        vals = np.array([s[1] for s in samples], dtype=float)
        fr = np.unique(vals)
        centers = fr.astype(float)
        cnts = np.array([float((vals == v).sum()) for v in fr])
        plot_distribution(centers, cnts, meta, Path(args.output), args.svg, note)

    if args.latency_output:
        plot_timeline(samples, meta, Path(args.latency_output), args.svg, note)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
