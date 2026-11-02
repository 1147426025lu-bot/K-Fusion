#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户态 cyclictest 直方图 vs fused jitter.bin → 延迟摘要图 + 抖动分布图。"""
from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

MAGIC = 0x504C434A


def parse_cyclictest_hist(path: Path):
    """cyclictest --histfile / -h 块: '000008 014975' → (us, count)."""
    bins, counts = [], []
    for ln in path.read_text(errors="replace").splitlines():
        m = re.match(r"^(\d+)\s+(\d+)\s*$", ln.strip())
        if not m:
            continue
        bins.append(int(m.group(1)))
        counts.append(int(m.group(2)))
    return np.array(bins, dtype=int), np.array(counts, dtype=np.int64)


def hist_stats(bins, counts):
    n = int(counts.sum())
    if n <= 0:
        return {"min_us": None, "avg_us": None, "max_us": None, "p50_us": None,
                "p99_us": None, "samples": 0}
    # expand-free percentile via cumulative
    order = np.argsort(bins)
    b, c = bins[order], counts[order]
    cum = np.cumsum(c)
    def pct(p):
        need = max(1, int((p / 100.0) * n + 0.999999))
        i = int(np.searchsorted(cum, need, side="left"))
        return int(b[min(i, len(b) - 1)])
    avg = float(np.dot(b.astype(np.float64), c.astype(np.float64)) / n)
    return {
        "min_us": int(b[c > 0][0]) if (c > 0).any() else int(b[0]),
        "avg_us": avg,
        "max_us": int(b[c > 0][-1]) if (c > 0).any() else int(b[-1]),
        "p50_us": pct(50),
        "p99_us": pct(99),
        "samples": n,
    }


def parse_jitter_bin(path: Path):
    data = path.read_bytes()
    if len(data) < 40:
        raise SystemExit(f"jitter.bin too small: {path}")
    magic, ver = struct.unpack("<II", data[:8])
    if magic != MAGIC:
        raise SystemExit(f"bad magic {magic:#x}")
    hdr = 40 if ver == 1 else 56 if ver == 2 else None
    if hdr is None:
        raise SystemExit(f"bad version {ver}")
    cycles, min_ns, max_ns, sample_count = struct.unpack("<QqqI", data[8:36])
    body = data[hdr:]
    n = min(sample_count, len(body) // 4)
    samples = list(struct.unpack(f"<{n}i", body[: n * 4])) if n else []
    abs_max = max(abs(min_ns), abs(max_ns))
    return {
        "cycles": cycles,
        "min_ns": min_ns,
        "max_ns": max_ns,
        "abs_max_ns": abs_max,
        "samples": samples,
        "sample_count": n,
    }


def fused_stats_from_samples(samples):
    if not samples:
        return {}
    a = np.asarray(samples, dtype=np.int64)
    abs_a = np.abs(a)
    return {
        "min_ns": int(a.min()),
        "max_ns": int(a.max()),
        "abs_max_ns": int(abs_a.max()),
        "abs_p50_ns": int(np.percentile(abs_a, 50)),
        "abs_p99_ns": int(np.percentile(abs_a, 99)),
        "mean_abs_ns": float(abs_a.mean()),
        "n": int(a.size),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", required=True, help="userspace cyclictest histfile")
    ap.add_argument("--jitter-bin", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--duration-s", type=int, required=True)
    ap.add_argument("--period-us", type=int, default=1000)
    ap.add_argument("--cpu", type=int, default=3)
    a = ap.parse_args()

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    us_bins, us_counts = parse_cyclictest_hist(Path(a.hist))
    us = hist_stats(us_bins, us_counts)
    fused = parse_jitter_bin(Path(a.jitter_bin))
    fs = fused_stats_from_samples(fused["samples"])
    if fs:
        fused.update(fs)

    summary = {
        "duration_s": a.duration_s,
        "period_us": a.period_us,
        "cpu": a.cpu,
        "userspace": us,
        "fused": {
            "min_ns": fused.get("min_ns"),
            "max_ns": fused.get("max_ns"),
            "abs_max_us": (fused.get("abs_max_ns") or 0) / 1000.0,
            "abs_p50_us": (fused.get("abs_p50_ns") or 0) / 1000.0,
            "abs_p99_us": (fused.get("abs_p99_ns") or 0) / 1000.0,
            "mean_abs_us": (fused.get("mean_abs_ns") or 0) / 1000.0,
            "cycles": fused.get("cycles"),
            "ring_samples": fused.get("sample_count"),
            "note": "signed period jitter (actual−nominal), not cyclictest wakeup latency",
        },
    }
    (out / f"summary_{a.stamp}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ---- Fig1: latency summary bars ----
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    labels = ["min", "avg / mean|j|", "p99", "max / abs_max"]
    us_vals = [us["min_us"], us["avg_us"], us["p99_us"], us["max_us"]]
    fu_vals = [
        abs(fused.get("min_ns") or 0) / 1000.0,  # show |min| for bar scale honesty? better mean|j|
        (fused.get("mean_abs_ns") or 0) / 1000.0,
        (fused.get("abs_p99_ns") or 0) / 1000.0,
        (fused.get("abs_max_ns") or 0) / 1000.0,
    ]
    # first bar for fused: use abs of extreme of min side as "min |j|" approx → use abs_p50 for "typical"
    fu_vals[0] = (fused.get("abs_p50_ns") or 0) / 1000.0
    labels[0] = "p50 / |j|p50"
    x = np.arange(len(labels))
    w = 0.36
    b1 = ax.bar(x - w / 2, us_vals, w, label="userspace cyclictest (wakeup µs)", color="#2563eb")
    b2 = ax.bar(x + w / 2, fu_vals, w, label="fused .ko (|period jitter| µs)", color="#ea580c")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("µs")
    ax.set_title(f"{a.duration_s}s @ {a.period_us}µs CPU{a.cpu} — userspace vs fused")
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                    f"{b.get_height():.1f}", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    p1 = out / f"fig_latency_summary_{a.stamp}.png"
    fig.savefig(p1, dpi=140)
    plt.close()

    # ---- Fig2: jitter / latency histograms ----
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    ax.bar(us_bins, us_counts, width=1.0, alpha=0.55, label="userspace latency",
           color="#2563eb", align="center")
    samples = np.asarray(fused["samples"], dtype=np.float64) / 1000.0  # µs
    if samples.size:
        lo, hi = -50, 150
        mask = (samples >= lo) & (samples <= hi)
        ax2 = ax.twinx()
        ax2.hist(samples[mask], bins=200, histtype="step", color="#ea580c",
                 linewidth=1.4, label="fused period jitter")
        ax2.set_ylabel("fused count", color="#ea580c")
        ax2.spines["top"].set_visible(False)
    ax.set_xlabel("µs")
    ax.set_ylabel("userspace count", color="#2563eb")
    ax.set_xlim(-5, 150)
    ax.set_title(f"Histogram overlay ({a.duration_s}s)")
    ax.legend(loc="upper right", frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    fig.tight_layout()
    p2 = out / f"fig_jitter_hist_{a.stamp}.png"
    fig.savefig(p2, dpi=140)
    plt.close()

    # ---- Fig3: fused timeseries (decimated) ----
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    if samples.size:
        stride = max(1, samples.size // 20000)
        y = samples[::stride]
        t = np.arange(y.size) * (a.period_us / 1e6) * stride
        ax.plot(t, y, lw=0.4, color="#ea580c")
        ax.axhline(0, color="#94a3b8", lw=0.6)
        ax.set_xlabel("time (s)")
        ax.set_ylabel("period jitter (µs)")
        ax.set_title(f"Fused timeseries (stride={stride}, n={samples.size})")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    p3 = out / f"fig_fused_timeseries_{a.stamp}.png"
    fig.savefig(p3, dpi=140)
    plt.close()

    print(f"wrote {p1}")
    print(f"wrote {p2}")
    print(f"wrote {p3}")
    print(f"summary {out / f'summary_{a.stamp}.json'}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
