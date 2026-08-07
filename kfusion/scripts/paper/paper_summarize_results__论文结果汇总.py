#!/usr/bin/env python3
"""汇总论文实验 CSV → Markdown 表（均值/标准差/次数）。"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def to_float(s: str) -> float | None:
    s = (s or "").strip()
    if not s or s in ("FAIL", "na", "?"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def summarize(csv_path: Path) -> str:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8", errors="replace")))
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    meta: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for r in rows:
        key = (r.get("baseline", "?"), r.get("measure_kind", "?"))
        meta[key].append(r)
        v = to_float(r.get("abs_max_ns", ""))
        if v is not None:
            groups[key].append(v)

    lines = [
        f"# 论文实验汇总",
        f"",
        f"来源: `{csv_path}`",
        f"样本行数: {len(rows)}",
        f"",
        "| baseline | measure_kind | n | mean (ns) | std (ns) | min | max | PASS率 |",
        "|----------|--------------|---|-----------|----------|-----|-----|--------|",
    ]

    for key in sorted(groups.keys()):
        vals = groups[key]
        metas = meta[key]
        n = len(vals)
        if n == 0:
            lines.append(f"| {key[0]} | {key[1]} | 0 | — | — | — | — | — |")
            continue
        mean = statistics.mean(vals)
        std = statistics.stdev(vals) if n > 1 else 0.0
        pass_n = sum(1 for m in metas if str(m.get("exit_code", "1")).strip() == "0")
        lines.append(
            f"| {key[0]} | {key[1]} | {n} | {mean:.0f} | {std:.0f} | {min(vals):.0f} | {max(vals):.0f} | {pass_n}/{len(metas)} |"
        )

    # ablation / notes 子集
    notes_rows = [r for r in rows if r.get("notes")]
    if notes_rows:
        lines += ["", "## 消融 / 备注标签", "", "| notes | abs_max_ns | exit |", "|-------|------------|------|"]
        for r in notes_rows:
            lines.append(f"| {r.get('notes','')} | {r.get('abs_max_ns','')} | {r.get('exit_code','')} |")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"❌ 不存在: {csv_path}", file=sys.stderr)
        return 1
    text = summarize(csv_path)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"✅ 已写入 {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
