#!/usr/bin/env python3
"""Summarize paper multitask (W5) CSV → markdown table."""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def num(v: str) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def fmt_stats(vals: list[float]) -> str:
    if not vals:
        return "n/a"
    if len(vals) == 1:
        return f"{vals[0]:.0f} (n=1)"
    return f"{statistics.mean(vals):.0f}±{statistics.pstdev(vals):.0f} (n={len(vals)})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = load_rows(Path(args.csv))
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    ok = 0
    for r in rows:
        if r.get("exit_code", "1") == "0" and r.get("abs_max_ns"):
            ok += 1
        key = (r.get("baseline", "?"), r.get("measure_kind", "?"))
        v = num(r.get("abs_max_ns", ""))
        if v is not None:
            groups[key].append(v)

    lines = [
        "# W5 plc_multitask 测量汇总",
        "",
        f"- 总 run 数: {len(rows)}",
        f"- 有效 abs_max 行: {ok}",
        "",
        "## 主循环 1ms 周期偏差 abs_max_ns（mean±std）",
        "",
        "| Baseline | Measure | abs_max_ns |",
        "|----------|---------|------------|",
    ]
    for (baseline, kind) in sorted(groups.keys()):
        lines.append(f"| {baseline} | {kind} | {fmt_stats(groups[(baseline, kind)])} |")

    if not groups:
        lines.append("| — | — | （无有效数据，检查 MtSummary 日志） |")

    lines += [
        "",
        "## 说明",
        "",
        "- **userspace**: gcc 原生二进制，同 CPU/优先级隔离",
        "- **fused**: PLCFusion `.ko`，`MT_RUN_LOOPS=0` 由 duration 结束",
        "- **soak/stress**: 与主矩阵相同 L2/L1 profile + hackbench 背景",
        "- 构造效度：多 TU 控制负载，非 cyclictest micro-benchmark",
        "",
    ]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
