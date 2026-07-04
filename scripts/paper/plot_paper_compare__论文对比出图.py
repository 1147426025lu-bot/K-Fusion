#!/usr/bin/env python3
"""从 compare 目录 manifest + jitter.bin / log 生成三基线叠加对比图。"""
from __future__ import annotations

import argparse
import csv
import re
import struct
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

FUSED_RING_MAGIC = 0x504C434A
HDR_V1 = 40

BASELINES = ["userspace", "timedc", "fused"]
COLORS = {
    "userspace": "#64748b",
    "timedc": "#7c3aed",
    "fused": "#1a4b8c",
}
LABELS = {
    "userspace": "Userspace cyclictest",
    "timedc": "Timed C (sdelay)",
    "fused": "PLCFusion fused",
}
KIND_LABELS = {"soak": "Soak (quiet CPU)", "stress": "Stress (hackbench)"}


def load_jitter_bin_v1(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) < HDR_V1:
        return []
    magic, version = struct.unpack("<II", data[:8])
    if magic != FUSED_RING_MAGIC or version != 1:
        return []
    _, _, _, sample_count, _ = struct.unpack("<QqqII", data[8:HDR_V1])
    body = data[HDR_V1 : HDR_V1 + sample_count * 8]
    if len(body) < sample_count * 8:
        return []
    return list(struct.unpack(f"<{sample_count}q", body))


def parse_cyclictest_hist(log_path: Path) -> list[tuple[int, int]]:
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


def hist_to_abs_curve(hist: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    if not hist:
        return np.array([]), np.array([])
    xs = np.array([abs(x) * 1000.0 for x, _ in sorted(hist)], dtype=float)
    cs = np.array([c for _, c in sorted(hist)], dtype=float)
    total = cs.sum()
    if total <= 0:
        return np.array([]), np.array([])
    return xs, cs / total


def samples_to_abs_hist(vals: list[int], bins: int = 80) -> tuple[np.ndarray, np.ndarray]:
    if not vals:
        return np.array([]), np.array([])
    arr = np.abs(np.array(vals, dtype=float))
    lo, hi = np.percentile(arr, [0.5, 99.5])
    if hi <= lo:
        hi = lo + 1.0
    counts, edges = np.histogram(arr, bins=bins, range=(lo, hi))
    centers = (edges[:-1] + edges[1:]) / 2.0
    total = counts.sum()
    if total == 0:
        return np.array([]), np.array([])
    return centers, counts / total


def load_manifest(compare_dir: Path) -> list[dict]:
    manifest = compare_dir / "manifest.csv"
    if not manifest.is_file():
        return []
    with manifest.open(encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def row_for(rows: list[dict], kind: str, baseline: str) -> dict | None:
    for r in rows:
        if r.get("kind") == kind and r.get("baseline") == baseline:
            return r
    return None


def plot_overlay_distribution(rows: list[dict], kind: str, out_path: Path, duration_min: int) -> bool:
    fig, ax = plt.subplots(figsize=(9, 5), facecolor="white")
    plotted = 0
    for b in BASELINES:
        r = row_for(rows, kind, b)
        if not r:
            continue
        bin_path = Path(r.get("jitter_bin") or "")
        log_path = Path(r.get("log") or "")
        x, y = np.array([]), np.array([])
        if b == "userspace" and log_path.is_file():
            x, y = hist_to_abs_curve(parse_cyclictest_hist(log_path))
        elif bin_path.is_file():
            x, y = samples_to_abs_hist(load_jitter_bin_v1(bin_path))
        if len(x) == 0:
            continue
        ax.plot(x, y, label=LABELS[b], color=COLORS[b], linewidth=1.8, alpha=0.9)
        plotted += 1
    if plotted == 0:
        plt.close(fig)
        return False
    ax.set_xlabel("|jitter| (ns)")
    ax.set_ylabel("normalized density")
    ax.set_title(
        f"Absolute jitter distribution — {KIND_LABELS[kind]} ({duration_min} min)",
        fontsize=11,
        fontweight="bold",
    )
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(fontsize=9)
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(f"{out_path}.{ext}", dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"✅ 分布叠加 [{kind}] → {out_path}.png")
    return True


def plot_overlay_timeline(rows: list[dict], kind: str, out_path: Path, duration_min: int) -> bool:
    fig, ax = plt.subplots(figsize=(10, 4.5), facecolor="white")
    plotted = 0
    for b in ("timedc", "fused"):
        r = row_for(rows, kind, b)
        if not r:
            continue
        bin_path = Path(r.get("jitter_bin") or "")
        if not bin_path.is_file():
            continue
        vals = load_jitter_bin_v1(bin_path)
        if not vals:
            continue
        ax.plot(np.arange(len(vals)), vals, label=LABELS[b], color=COLORS[b], linewidth=0.7, alpha=0.85)
        plotted += 1
    if plotted == 0:
        plt.close(fig)
        print(f"ℹ️  跳过时序叠加 [{kind}]：无 timedc/fused jitter.bin", file=__import__("sys").stderr)
        return False
    ax.set_xlabel("decimated sample index")
    ax.set_ylabel("signed jitter (ns)")
    ax.set_title(
        f"Decimated latency timeline — {KIND_LABELS[kind]} ({duration_min} min)",
        fontsize=11,
        fontweight="bold",
    )
    ax.axhline(0, color="#999", linewidth=0.6)
    ax.grid(True, linestyle=":", alpha=0.4)
    ax.legend(fontsize=9)
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(f"{out_path}.{ext}", dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"✅ 时序叠加 [{kind}] → {out_path}.png")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="三基线对比叠加出图")
    ap.add_argument("--compare-dir", required=True)
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--duration-min", type=int, default=15)
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    compare_dir = Path(args.compare_dir)
    out_dir = Path(args.out_dir) if args.out_dir else root / "docs" / "paper" / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_manifest(compare_dir)
    if not rows:
        print(f"❌ 无 manifest: {compare_dir / 'manifest.csv'}", file=__import__("sys").stderr)
        return 1

    ok = False
    for kind in ("soak", "stress"):
        ok |= plot_overlay_distribution(
            rows, kind, out_dir / f"fig_compare_dist_{kind}__{kind}分布叠加", args.duration_min
        )
        ok |= plot_overlay_timeline(
            rows, kind, out_dir / f"fig_compare_timeline_{kind}__{kind}时序叠加", args.duration_min
        )

    if not ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
