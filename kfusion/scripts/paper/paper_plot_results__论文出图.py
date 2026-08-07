#!/usr/bin/env python3
"""从 results/paper/*.csv 生成论文数据图（PNG + SVG）。"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

BASELINE_ORDER = ["userspace", "baseline_ko", "timedc", "fused"]
BASELINE_LABELS = {
    "userspace": "Userspace\ncyclictest",
    "baseline_ko": "Hand-written\n.ko",
    "timedc": "Timed C\n(sdelay)",
    "fused": "PLCFusion\nfused",
}
KIND_ORDER = ["soak", "stress"]
KIND_LABELS = {"soak": "Soak (quiet CPU)", "stress": "Stress (hackbench)"}
COLORS = {
    "userspace": "#64748b",
    "baseline_ko": "#b45309",
    "timedc": "#7c3aed",
    "fused": "#1a4b8c",
}


def to_float(s: str) -> float | None:
    s = (s or "").strip()
    if not s or s in ("FAIL", "na", "?"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_baseline_rows(paper_dir: Path) -> list[dict]:
    rows: list[dict] = []
    bdir = paper_dir / "baseline_matrix"
    for csv_path in sorted(bdir.glob("paper_baseline_*.csv")):
        if csv_path.name == "paper_baseline_merged.csv":
            continue
        with csv_path.open(encoding="utf-8", errors="replace") as f:
            rows.extend(csv.DictReader(f))
    merged = bdir / "paper_baseline_merged.csv"
    if merged.is_file():
        with merged.open(encoding="utf-8", errors="replace") as f:
            rows.extend(csv.DictReader(f))
    # 去重：同 baseline/kind/run_idx 保留有 abs_max 的最后一条
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("baseline"), r.get("measure_kind"), r.get("run_idx"))
        v = to_float(r.get("abs_max_ns", ""))
        if v is None and key in seen:
            continue
        seen[key] = r
    return list(seen.values())


def load_ablation_rows(paper_dir: Path) -> list[dict]:
    adir = paper_dir / "ablation"
    if not adir.is_dir():
        return []
    latest = max(adir.glob("paper_ablation_*.csv"), default=None, key=lambda p: p.stat().st_mtime)
    if not latest:
        return []
    with latest.open(encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def parse_cyclictest_hist(log_path: Path) -> list[tuple[int, int]]:
    """解析 cyclictest -h 直方图: (latency_us, count)。"""
    if not log_path.is_file():
        return []
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if "# Histogram" not in text:
        return []
    out: list[tuple[int, int]] = []
    for line in text.splitlines():
        m = re.match(r"^(\d{6})\s+(\d{6})$", line.strip())
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
    return out


def parse_jitter_bin_v1(bin_path: Path) -> list[int]:
    import struct

    if not bin_path.is_file():
        return []
    data = bin_path.read_bytes()
    if len(data) < 40:
        return []
    magic, version = struct.unpack("<II", data[:8])
    if magic != 0x504C434A or version != 1:
        return []
    _, _, _, sample_count, _ = struct.unpack("<QqqII", data[8:40])
    body = data[40 : 40 + sample_count * 8]
    if len(body) < sample_count * 8:
        return []
    return list(struct.unpack(f"<{sample_count}q", body))


def parse_fused_jitter_lines(log_path: Path) -> list[int]:
    if not log_path.is_file():
        return []
    vals: list[int] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"Jitter:\s*(-?\d+)", line)
        if m:
            vals.append(int(m.group(1)))
    return vals


def hist_to_cdf(hist: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    xs, cs = zip(*sorted(hist)) if hist else ([], [])
    total = sum(cs)
    if total == 0:
        return np.array([]), np.array([])
    ys = np.cumsum(cs) / total
    return np.array(xs, dtype=float), ys


def jitter_to_cdf(vals: list[int], bins: int = 200) -> tuple[np.ndarray, np.ndarray]:
    if not vals:
        return np.array([]), np.array([])
    arr = np.array(vals, dtype=float)
    lo, hi = np.percentile(arr, [0.5, 99.5])
    if hi <= lo:
        hi = lo + 1
    counts, edges = np.histogram(arr, bins=bins, range=(lo, hi))
    centers = (edges[:-1] + edges[1:]) / 2.0
    total = counts.sum()
    if total == 0:
        return np.array([]), np.array([])
    return centers, np.cumsum(counts) / total


def plot_baseline_bars(rows: list[dict], out_base: Path) -> bool:
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        v = to_float(r.get("abs_max_ns", ""))
        if v is None:
            continue
        groups[(r.get("baseline", ""), r.get("measure_kind", ""))].append(v)

    if not groups:
        print("⚠️  无有效 abs_max_ns，跳过基线柱状图", file=sys.stderr)
        return False

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), facecolor="white")
    for ax, kind in zip(axes, KIND_ORDER):
        xpos = np.arange(len(BASELINE_ORDER))
        means, stds, ns = [], [], []
        for b in BASELINE_ORDER:
            vals = groups.get((b, kind), [])
            ns.append(len(vals))
            if vals:
                means.append(statistics.mean(vals))
                stds.append(statistics.stdev(vals) if len(vals) > 1 else 0.0)
            else:
                means.append(0.0)
                stds.append(0.0)
        bars = ax.bar(
            xpos,
            means,
            yerr=stds,
            capsize=4,
            color=[COLORS[b] for b in BASELINE_ORDER],
            edgecolor="#222",
            linewidth=0.8,
        )
        ax.set_xticks(xpos)
        ax.set_xticklabels([BASELINE_LABELS[b] for b in BASELINE_ORDER], fontsize=9)
        ax.set_ylabel("abs max jitter (ns)")
        ax.set_title(KIND_LABELS[kind], fontsize=11, fontweight="bold")
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        for i, (bar, n) in enumerate(zip(bars, ns)):
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, h, f"n={n}", ha="center", va="bottom", fontsize=8)
            if n == 0:
                ax.text(bar.get_x() + bar.get_width() / 2, 0, "no data", ha="center", va="bottom", fontsize=8, color="#888")

    fig.suptitle(
        "Figure 4. Baseline comparison incl. Timed C (Raspberry Pi PREEMPT_RT)",
        fontsize=12,
        fontweight="bold",
    )
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(f"{out_base}.{ext}", dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"✅ 基线柱状图 → {out_base}.png / .svg")
    return True


def plot_cdf_from_logs(rows: list[dict], out_base: Path) -> bool:
    """从 raw_log 解析 histogram / Jitter 行画 CDF（有数据才画）。"""
    fig, ax = plt.subplots(figsize=(8, 5), facecolor="white")
    plotted = 0
    for kind in KIND_ORDER:
        for b in BASELINE_ORDER:
            # 取该组第一条有 log 且 PASS 的记录
            cand = [
                r for r in rows
                if r.get("baseline") == b and r.get("measure_kind") == kind and r.get("exit_code") == "0"
            ]
            if not cand:
                continue
            log = Path(cand[0].get("raw_log", ""))
            if b == "userspace":
                hist = parse_cyclictest_hist(log)
                if not hist:
                    continue
                x, y = hist_to_cdf(hist)
                x = x * 1000.0  # us -> ns
            elif b == "fused":
                vals = parse_fused_jitter_lines(log)
                if not vals:
                    continue
                x, y = jitter_to_cdf(vals)
            elif b == "timedc":
                bin_hint = log.with_suffix(".jitter.bin")
                if not bin_hint.is_file():
                    m = re.search(r"JITTER_BIN=([^\s]+)", log.read_text(encoding="utf-8", errors="replace"))
                    if m:
                        bin_hint = Path(m.group(1))
                vals = parse_jitter_bin_v1(bin_hint)
                if not vals:
                    continue
                x, y = jitter_to_cdf(vals)
            else:
                continue
            if len(x) == 0:
                continue
            label = f"{b} ({kind})"
            ax.plot(x, y, label=label, color=COLORS.get(b, "#333"), linewidth=1.8)
            plotted += 1

    if plotted == 0:
        plt.close(fig)
        print("ℹ️  日志中无 histogram/Jitter 样本，跳过 CDF（需 cyclictest -h 或 fused Jitter: 行）", file=sys.stderr)
        return False

    ax.set_xlabel("latency (ns)")
    ax.set_ylabel("CDF")
    ax.set_title("Figure 5. Jitter CDF (from measurement logs)", fontsize=12, fontweight="bold")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(fontsize=9)
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(f"{out_base}.{ext}", dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"✅ CDF 图 → {out_base}.png / .svg")
    return True


def plot_ablation(rows: list[dict], out_base: Path) -> bool:
    notes_vals: list[tuple[str, float]] = []
    for r in rows:
        v = to_float(r.get("abs_max_ns", ""))
        note = (r.get("notes") or "").strip()
        if v is not None and note:
            notes_vals.append((note, v))
    if not notes_vals:
        print("ℹ️  无消融 CSV 数据，跳过消融图", file=sys.stderr)
        return False

    labels, vals = zip(*notes_vals)
    fig, ax = plt.subplots(figsize=(10, 4.5), facecolor="white")
    x = np.arange(len(labels))
    ax.bar(x, vals, color="#6366f1", edgecolor="#222", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=9)
    ax.set_ylabel("abs max jitter (ns)")
    ax.set_title("Figure 6. Ablation (soak, fused cyclictest)", fontsize=12, fontweight="bold")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(f"{out_base}.{ext}", dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"✅ 消融图 → {out_base}.png / .svg")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="论文实验数据出图")
    ap.add_argument("--paper-dir", default="", help="results/paper 目录")
    ap.add_argument("--out-dir", default="", help="输出目录，默认 docs/paper/figures")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    paper_dir = Path(args.paper_dir) if args.paper_dir else root / "results" / "paper"
    out_dir = Path(args.out_dir) if args.out_dir else root / "docs" / "paper" / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    baseline_rows = load_baseline_rows(paper_dir)
    ablation_rows = load_ablation_rows(paper_dir)

    ok = False
    ok |= plot_baseline_bars(baseline_rows, out_dir / "fig4_baseline_absmax__三基线对照")
    ok |= plot_cdf_from_logs(baseline_rows, out_dir / "fig5_baseline_cdf__抖动CDF")
    ok |= plot_ablation(ablation_rows, out_dir / "fig6_ablation__消融对照")

    if not ok:
        print("❌ 未生成任何数据图：请先完成 run_paper_baseline_matrix（含 userspace）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
