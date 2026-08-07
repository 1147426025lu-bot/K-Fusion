#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate K-Fusion per-function WCET schedule JSON (version 1)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def validate_schedule(doc: dict[str, Any], *, require_cold: bool = False) -> list[str]:
    errors: list[str] = []
    if doc.get("version") != 1:
        errors.append(f"version must be 1, got {doc.get('version')!r}")

    hot = doc.get("hot_functions")
    if not isinstance(hot, list) or not all(isinstance(x, str) for x in hot):
        errors.append("hot_functions must be a list of strings")
    elif not hot:
        errors.append("hot_functions must be non-empty")

    cold = doc.get("cold_sequences")
    if not isinstance(cold, dict):
        errors.append("cold_sequences must be an object")
    else:
        for fn, seq in cold.items():
            if not isinstance(fn, str):
                errors.append(f"cold_sequences key must be str: {fn!r}")
            if not isinstance(seq, list) or not all(isinstance(p, str) for p in seq):
                errors.append(f"cold_sequences[{fn}] must be list[str]")
            elif require_cold and not seq:
                errors.append(f"cold_sequences[{fn}] must be non-empty when require_cold=1")

    mod = doc.get("module_passes")
    if mod is not None and (
        not isinstance(mod, list) or not all(isinstance(x, str) for x in mod)
    ):
        errors.append("module_passes must be a list of strings")

    funcs = doc.get("functions")
    if funcs is not None and not isinstance(funcs, dict):
        errors.append("functions must be an object when present")

    hot_set = set(hot or [])
    if isinstance(cold, dict):
        overlap = hot_set & set(cold.keys())
        if overlap:
            errors.append(f"function cannot be both hot and cold: {sorted(overlap)}")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate WCET schedule JSON")
    ap.add_argument("schedule_json", type=Path)
    ap.add_argument(
        "--require-cold",
        action="store_true",
        help="Every cold_sequences entry must have a non-empty pass list",
    )
    args = ap.parse_args()
    doc = json.loads(args.schedule_json.read_text(encoding="utf-8"))
    errors = validate_schedule(doc, require_cold=args.require_cold)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    n_hot = len(doc.get("hot_functions") or [])
    n_cold = len(doc.get("cold_sequences") or {})
    print(f"ok hot={n_hot} cold={n_cold}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
