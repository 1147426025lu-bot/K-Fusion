#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lavinium 风格函数级分区：从 LLVM IR + manifest 热路径标记，划分 hot / cold 函数。

hot  → optnone（plc-fusion-wcet-mark，周期体/回调）
cold → 可独立 autotune 冷路径 pass 序列（每函数一条 pipeline）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


DEFINE_RE = re.compile(r"^define\s+(?:[^@]*\s+)?@([A-Za-z_][\w.]*)", re.M)
DECLARE_RE = re.compile(r"^declare\s+(?:[^@]*\s+)?@([A-Za-z_][\w.]*)", re.M)
CALL_RE = re.compile(r"\s+call[^@]*@([A-Za-z_][\w.]*)")
LABEL_LINE_RE = re.compile(r"^\s*\w+:\s*(?:;.*)?$")


def slice_function_body(ir_text: str, name: str) -> str:
    pat = re.compile(
        rf"^define\s+(?:[^@]*\s+)?@{re.escape(name)}\b.*?^(?:define\s|declare\s|\Z)",
        re.M | re.S,
    )
    body_m = pat.search(ir_text)
    return body_m.group(0) if body_m else ""


def count_function_instructions(body: str) -> int:
    """Rough LLVM IR instruction count (CI WCET proxy for hot path size)."""
    n = 0
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(";") or LABEL_LINE_RE.match(line):
            continue
        if s.startswith("attributes ") or s.startswith("!"):
            continue
        n += 1
    return n


def count_hot_instructions(ir_text: str, hot_names: Iterable[str]) -> int:
    total = 0
    for name in hot_names:
        body = slice_function_body(ir_text, name)
        if body:
            total += count_function_instructions(body)
    return total


@dataclass
class FunctionInfo:
    name: str
    kind: str  # hot | cold | helper | external
    callers: list[str]
    callees: list[str]
    basic_blocks: int = 0
    lines: int = 0


def parse_ir_functions(ir_text: str) -> dict[str, FunctionInfo]:
    funcs: dict[str, FunctionInfo] = {}
    for m in DEFINE_RE.finditer(ir_text):
        name = m.group(1)
        funcs[name] = FunctionInfo(name=name, kind="cold", callers=[], callees=[])

    for m in DECLARE_RE.finditer(ir_text):
        name = m.group(1)
        if name not in funcs:
            funcs[name] = FunctionInfo(name=name, kind="external", callers=[], callees=[])

    for name, info in funcs.items():
        if info.kind == "external":
            continue
        # crude function body slice for metrics / calls
        pat = re.compile(
            rf"^define\s+(?:[^@]*\s+)?@{re.escape(name)}\b.*?^(?:define\s|declare\s|\Z)",
            re.M | re.S,
        )
        body_m = pat.search(ir_text)
        body = body_m.group(0) if body_m else ""
        info.lines = body.count("\n")
        info.basic_blocks = len(re.findall(r"^\s*\w+:", body, re.M))
        info.callees = sorted(set(CALL_RE.findall(body)))
        info.callees = [c for c in info.callees if c != name]

    callers: dict[str, set[str]] = defaultdict(set)
    for name, info in funcs.items():
        for callee in info.callees:
            if callee in funcs:
                callers[callee].add(name)
    for name, info in funcs.items():
        info.callers = sorted(callers.get(name, []))

    return funcs


def normalize_hot_names(raw: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for item in raw:
        for part in item.split(","):
            p = part.strip()
            if p:
                out.add(p)
    return out


def classify_functions(
    funcs: dict[str, FunctionInfo],
    hot_names: set[str],
    roots: set[str] | None = None,
) -> None:
    hot_closure: set[str] = set(hot_names)
    # 从 hot 向下 1 跳仍标 hot（周期体内联 helper）
    changed = True
    while changed:
        changed = False
        for name in list(hot_closure):
            if name not in funcs:
                continue
            for callee in funcs[name].callees:
                if callee in funcs and funcs[callee].kind != "external":
                    if callee not in hot_closure:
                        hot_closure.add(callee)
                        changed = True

    for name, info in funcs.items():
        if info.kind == "external":
            continue
        if name in hot_closure:
            info.kind = "hot"
        elif roots and name in roots:
            info.kind = "helper"
        elif not info.callers and name.startswith(("plc_", "main", "thread")):
            info.kind = "helper"
        else:
            info.kind = "cold"


def build_schedule_template(
    funcs: dict[str, FunctionInfo],
    default_cold: list[str],
    module_passes: list[str],
) -> dict:
    cold: dict[str, list[str]] = {}
    hot: list[str] = []
    for name, info in sorted(funcs.items()):
        if info.kind == "hot":
            hot.append(name)
        elif info.kind == "cold":
            cold[name] = list(default_cold)
    return {
        "version": 1,
        "hot_functions": hot,
        "cold_sequences": cold,
        "module_passes": list(module_passes),
        "functions": {n: asdict(f) for n, f in funcs.items() if f.kind != "external"},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="K-Fusion WCET function partition (Lavinium-style)")
    ap.add_argument("pre_ll", type=Path, help="kernel 化前的 _pre.ll")
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--hot", default="", help="逗号分隔热路径函数")
    ap.add_argument("--roots", default="", help="DCE 根符号（可选）")
    ap.add_argument(
        "--default-cold",
        default="simplifycfg|sroa|instcombine|loop-mssa(loop-rotate,licm)|gvn|adce",
        help="冷函数默认 pass 原子（| 分隔）",
    )
    ap.add_argument("--module", default="globaldce", help="module 级 pass，逗号分隔")
    args = ap.parse_args()

    ir_text = args.pre_ll.read_text(encoding="utf-8", errors="replace")
    funcs = parse_ir_functions(ir_text)
    hot = normalize_hot_names([args.hot] if args.hot else [])
    roots = normalize_hot_names([args.roots] if args.roots else [])
    classify_functions(funcs, hot, roots or None)

    cold_atoms = [p.strip() for p in args.default_cold.split("|") if p.strip()]
    mod = [p.strip() for p in args.module.split(",") if p.strip()]
    doc = build_schedule_template(funcs, cold_atoms, mod)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    n_hot = sum(1 for f in funcs.values() if f.kind == "hot")
    n_cold = sum(1 for f in funcs.values() if f.kind == "cold")
    print(f"partition: hot={n_hot} cold={n_cold} total_defined={sum(1 for f in funcs.values() if f.kind != 'external')}")
    print(f"schedule={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
