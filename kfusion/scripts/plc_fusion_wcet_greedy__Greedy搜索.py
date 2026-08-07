#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plc_fusion_wcet_greedy__Greedy搜索.py — 函数级 Greedy pass 序列搜索（Lavinium 对照）

对每个 cold 函数：从 default cold 出发，逐 pass 尝试插入/删除，保留改进 hot_inst 代理适应度的序列。
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

_SCRIPT = Path(__file__).resolve().parent

_assoc_mod = importlib.util.spec_from_file_location(
    "plc_pf_search",
    _SCRIPT / "plc_fusion_wcet_per_function_search__函数级Association.py",
)
assert _assoc_mod and _assoc_mod.loader
_pf = importlib.util.module_from_spec(_assoc_mod)
sys.modules["plc_pf_search"] = _pf
_assoc_mod.loader.exec_module(_pf)

EvaluatedSequence = _pf.EvaluatedSequence
compile_module_with_schedule = _pf.compile_module_with_schedule
make_fitness = _pf.make_fitness
cold_functions_ranked = _pf.cold_functions_ranked
atoms_to_env = _pf.atoms_to_env

_plib_mod = importlib.util.spec_from_file_location(
    "plc_wcet_passes",
    _SCRIPT / "plc_fusion_wcet_passes__Pass序列库.py",
)
assert _plib_mod and _plib_mod.loader
_plib = importlib.util.module_from_spec(_plib_mod)
_plib_mod.loader.exec_module(_plib)
COLD_MUTATION_PALETTE = _plib.COLD_MUTATION_PALETTE


def run_greedy_search(
    evaluate: Callable[[list[str]], EvaluatedSequence],
    *,
    initial_cold: list[str],
    palette: tuple[str, ...] = COLD_MUTATION_PALETTE,
    max_rounds: int = 3,
) -> EvaluatedSequence:
    best = evaluate(list(initial_cold))
    best.refresh_name()

    for _round in range(max_rounds):
        improved = False
        # try insert each palette pass at each position
        for p in palette:
            for pos in range(len(best.cold) + 1):
                trial = best.cold[:pos] + [p] + best.cold[pos:]
                ev = evaluate(trial)
                if ev.fitness < best.fitness:
                    best = ev
                    improved = True
        # try remove each pass
        i = 0
        while i < len(best.cold):
            trial = best.cold[:i] + best.cold[i + 1 :]
            if trial:
                ev = evaluate(trial)
                if ev.fitness < best.fitness:
                    best = ev
                    improved = True
                    continue
            i += 1
        if not improved:
            break
    return best


def search_per_function_greedy(
    *,
    pre_ll: str,
    schedule_in: dict[str, Any],
    out_schedule: Path,
    out_report: Path,
    opt_bin: str,
    llc_bin: str,
    fusion_so: str,
    llc_arch: str,
    llc_attr: str,
    pass_env: dict[str, str],
    hot_names: list[str],
    max_funcs: int,
    max_rounds: int,
    work_dir: Path,
) -> dict[str, Any]:
    schedule = copy.deepcopy(schedule_in)
    module_passes = list(schedule.get("module_passes") or ["globaldce"])
    targets = cold_functions_ranked(schedule, max_funcs)
    results: list[dict[str, Any]] = []
    compile_counter = 0

    if not targets:
        out_schedule.write_text(json.dumps(schedule, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        doc = {
            "algorithm": "per_function_greedy",
            "cold_functions_tuned": 0,
            "results": [],
            "schedule": str(out_schedule),
        }
        out_report.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return doc

    for idx, fn_name in enumerate(targets):
        initial_cold = list(schedule["cold_sequences"].get(fn_name) or [])
        baseline_cold = list(initial_cold)

        def evaluate(cold: list[str]) -> EvaluatedSequence:
            nonlocal compile_counter
            compile_counter += 1
            tag = f"greedy_{fn_name}_{compile_counter}"
            sched = copy.deepcopy(schedule)
            sched["cold_sequences"][fn_name] = list(cold)
            sched["module_passes"] = list(module_passes)
            hot_inst, obj_bytes, _kll = compile_module_with_schedule(
                pre_ll,
                sched,
                work_dir,
                opt_bin=opt_bin,
                llc_bin=llc_bin,
                fusion_so=fusion_so,
                llc_arch=llc_arch,
                llc_attr=llc_attr,
                pass_env=pass_env,
                hot_names=hot_names,
                tag=tag,
            )
            ev = EvaluatedSequence(
                kernel="plc-kernelize-wcet",
                cold=list(cold),
                module=list(module_passes),
                fitness=make_fitness(hot_inst, obj_bytes, cold),
            )
            ev.refresh_name()
            return ev

        print(
            f"    🧬 greedy [{idx + 1}/{len(targets)}] function={fn_name} "
            f"rounds={max_rounds} initial={atoms_to_env(initial_cold)}"
        )
        winner = run_greedy_search(evaluate, initial_cold=initial_cold, max_rounds=max_rounds)
        schedule["cold_sequences"][fn_name] = list(winner.cold) if winner.cold else baseline_cold
        results.append(
            {
                "function": fn_name,
                "baseline_cold": baseline_cold,
                "winner_cold": winner.cold,
                "winner_fitness": winner.fitness,
                "winner_name": winner.name,
                "cold_env": atoms_to_env(winner.cold),
            }
        )
        print(f"       winner fitness={winner.fitness} passes={atoms_to_env(winner.cold)}")

    out_schedule.write_text(json.dumps(schedule, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    doc = {
        "algorithm": "per_function_greedy_lavinium",
        "metric": "hot_inst_x10000_plus_obj_bytes",
        "max_rounds": max_rounds,
        "max_functions": max_funcs,
        "cold_functions_tuned": len(results),
        "total_compiles": compile_counter,
        "results": results,
        "schedule": str(out_schedule),
    }
    out_report.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-function WCET greedy search")
    ap.add_argument("config_json", type=Path)
    args = ap.parse_args()
    cfg = json.loads(args.config_json.read_text(encoding="utf-8"))
    hot_raw = cfg.get("hot_names") or cfg.get("pass_env", {}).get("PLC_FUSION_WCET_HOT_FUNCTIONS") or ""
    hot_names = [p.strip() for p in hot_raw.split(",") if p.strip()]
    doc = search_per_function_greedy(
        pre_ll=cfg["pre_ll"],
        schedule_in=cfg["schedule"],
        out_schedule=Path(cfg["out_schedule"]),
        out_report=Path(cfg["out_report"]),
        opt_bin=cfg["opt_bin"],
        llc_bin=cfg["llc_bin"],
        fusion_so=cfg["fusion_so"],
        llc_arch=cfg.get("llc_arch", "aarch64"),
        llc_attr=cfg.get("llc_attr", "-fp-armv8,-neon"),
        pass_env=cfg.get("pass_env") or {},
        hot_names=hot_names,
        max_funcs=int(cfg.get("max_funcs", 5)),
        max_rounds=int(cfg.get("greedy_rounds", 2)),
        work_dir=Path(cfg.get("work_dir") or cfg.get("out_report")).parent / ".wcet_per_fn_greedy",
    )
    print(f"    tuned={doc['cold_functions_tuned']} compiles={doc['total_compiles']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
