#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load platform WCET probe golden thresholds."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_GOLDEN_DIR = Path(__file__).resolve().parent.parent / "test" / "golden"


def load_golden(platform_id: str) -> dict:
    for name in (f"wcet_probe_{platform_id}.json", "wcet_probe_generic.json"):
        path = _GOLDEN_DIR / name
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["_path"] = str(path)
            return doc
    raise FileNotFoundError(f"no golden for platform={platform_id} under {_GOLDEN_DIR}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="generic")
    ap.add_argument("--field", default="max_abs_max_ns")
    ap.add_argument("--update", type=Path, help="write abs_max_ns into golden (WCET_PROBE_UPDATE_GOLDEN=1)")
    ap.add_argument("--abs-max-ns", type=int, default=0)
    args = ap.parse_args()
    doc = load_golden(args.platform)
    if args.update:
        if args.abs_max_ns <= 0:
            print("need --abs-max-ns for --update", file=sys.stderr)
            return 1
        path = Path(doc["_path"])
        doc["max_abs_max_ns"] = args.abs_max_ns
        doc.pop("_path", None)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"updated {path} max_abs_max_ns={args.abs_max_ns}")
        return 0
    val = doc.get(args.field)
    if val is None:
        print(f"missing field {args.field}", file=sys.stderr)
        return 1
    print(val)
    return 0


if __name__ == "__main__":
    sys.exit(main())
