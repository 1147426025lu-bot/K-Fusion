#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plc_fusion_wcet_per_function_search__函数级Association.py

Lavinium 外层循环简化版：对每个 cold 函数独立跑 Association 搜索，
更新 schedule JSON 中 cold_sequences[fn]（不再广播同一序列）。

适应度（CI/无 insmod）：
  hot_functions IR 指令数 × 10000 + kernel.o 字节数（越小越好）。
  空 cold 序列加 EMPTY_COLD_PENALTY，避免「不做优化」伪最优。
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve().parent
EMPTY_COLD_PENALTY = 10**15
HOT_INST_SCALE = 10000


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_plib = _load_module("plc_wcet_passes", _SCRIPT / "plc_fusion_wcet_passes__Pass序列库.py")
_assoc = _load_module("plc_wcet_assoc", _SCRIPT / "plc_fusion_wcet_association__关联规则搜索.py")
_part = _load_module("plc_wcet_part", _SCRIPT / "plc_fusion_wcet_partition__函数级分区.py")

EvaluatedSequence = _assoc.EvaluatedSequence
run_association_search = _assoc.run_association_search
atoms_to_env = _plib.atoms_to_env
count_hot_instructions = _part.count_hot_instructions


def make_fitness(hot_inst: int | None, obj_bytes: int | None, cold: list[str]) -> int:
    if hot_inst is None or obj_bytes is None:
        return 2**62
    fit = hot_inst * HOT_INST_SCALE + obj_bytes
    if not cold:
        fit += EMPTY_COLD_PENALTY
    return fit


def compile_module_with_schedule(
    pre_ll: str,
    schedule: dict[str, Any],
    work_dir: Path,
    *,
    opt_bin: str,
    llc_bin: str,
    fusion_so: str,
    llc_arch: str,
    llc_attr: str,
    pass_env: dict[str, str],
    hot_names: list[str],
    tag: str,
) -> tuple[int | None, int | None, str | None, str | None]:
    """Returns (hot_inst, obj_bytes, kll_path, err_path). err_path set on opt/llc failure."""
    work_dir.mkdir(parents=True, exist_ok=True)
    sched_path = work_dir / f"sched_{tag}.json"
    sched_path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    kll = work_dir / f"{tag}.ll"
    obj = work_dir / f"{tag}.o"
    err = work_dir / f"{tag}.err"
    env = {
        **os.environ,
        **pass_env,
        "PLC_FUSION_WCET_SCHEDULE_FILE": str(sched_path),
    }
    passes = "plc-kernelize-wcet,plc-fusion-wcet-schedule"
    try:
        r = subprocess.run(
            [
                opt_bin,
                f"-load-pass-plugin={fusion_so}",
                f"-passes={passes}",
                pre_ll,
                "-S",
                "-o",
                str(kll),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if r.returncode != 0:
            err.write_text(r.stderr or r.stdout, encoding="utf-8")
            return None, None, None, str(err)
        hot_inst = count_hot_instructions(kll.read_text(encoding="utf-8", errors="replace"), hot_names)
        r2 = subprocess.run(
            [
                llc_bin,
                "-O3",
                "-relocation-model=pic",
                f"-march={llc_arch}",
                f"-mattr={llc_attr}",
                "-filetype=obj",
                str(kll),
                "-o",
                str(obj),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if r2.returncode != 0 or not obj.is_file():
            err.write_text(r2.stderr or r2.stdout, encoding="utf-8")
            return hot_inst, None, str(kll), str(err)
        return hot_inst, obj.stat().st_size, str(kll), None
    except (subprocess.TimeoutExpired, OSError) as e:
        err.write_text(str(e), encoding="utf-8")
        return None, None, None, str(err)


def cold_functions_ranked(schedule: dict[str, Any], max_funcs: int) -> list[str]:
    cold = schedule.get("cold_sequences") or {}
    meta = schedule.get("functions") or {}
    names = list(cold.keys())
    if not names:
        return []

    def sort_key(fn: str) -> tuple[int, str]:
        info = meta.get(fn) or {}
        return (-int(info.get("lines") or 0), fn)

    names.sort(key=sort_key)
    return names[:max_funcs]


def search_per_function(
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
    budget_per_fn: int,
    max_funcs: int,
    seed: int,
    work_dir: Path,
) -> dict[str, Any]:
    schedule = copy.deepcopy(schedule_in)
    module_passes = list(schedule.get("module_passes") or ["globaldce"])
    targets = cold_functions_ranked(schedule, max_funcs)
    results: list[dict[str, Any]] = []
    compile_failures = 0

    if not targets:
        out_schedule.write_text(json.dumps(schedule, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        doc = {
            "algorithm": "per_function_association",
            "cold_functions_tuned": 0,
            "results": [],
            "schedule": str(out_schedule),
        }
        out_report.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return doc

    compile_counter = 0

    def evaluate_for_fn(fn_name: str):
        initial_cold = list(schedule["cold_sequences"].get(fn_name) or [])

        def evaluate(kernel: str, cold: list[str], module: list[str]) -> EvaluatedSequence:
            nonlocal compile_counter
            compile_counter += 1
            tag = f"{fn_name}_{compile_counter}"
            sched = copy.deepcopy(schedule)
            sched["cold_sequences"][fn_name] = list(cold)
            sched["module_passes"] = list(module)
            hot_inst, obj_bytes, _kll, err_path = compile_module_with_schedule(
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
            if err_path is not None:
                compile_failures += 1
            fit = make_fitness(hot_inst, obj_bytes, cold)
            ev = EvaluatedSequence(
                kernel=kernel,
                cold=list(cold),
                module=list(module),
                fitness=fit,
            )
            ev.refresh_name()
            return ev

        return evaluate, initial_cold

    for idx, fn_name in enumerate(targets):
        evaluate, initial_cold = evaluate_for_fn(fn_name)
        print(
            f"    🔍 association [{idx + 1}/{len(targets)}] function={fn_name} "
            f"budget={budget_per_fn} initial_cold={atoms_to_env(initial_cold)}"
        )
        baseline_cold = list(schedule["cold_sequences"].get(fn_name) or [])
        winner = run_association_search(
            evaluate,
            budget=budget_per_fn,
            seed=seed + idx,
            kernel="plc-kernelize-wcet",
            module_passes=module_passes,
            initial_cold=initial_cold,
        )
        schedule["cold_sequences"][fn_name] = list(winner.cold) if winner.cold else baseline_cold
        hot_inst = (winner.fitness - (0 if winner.cold else EMPTY_COLD_PENALTY)) // HOT_INST_SCALE
        results.append(
            {
                "function": fn_name,
                "baseline_cold": baseline_cold,
                "winner_cold": winner.cold,
                "winner_fitness": winner.fitness,
                "winner_hot_inst_proxy": hot_inst,
                "winner_name": winner.name,
                "cold_env": atoms_to_env(winner.cold),
            }
        )
        print(f"       winner fitness={winner.fitness} passes={atoms_to_env(winner.cold)}")

    out_schedule.write_text(json.dumps(schedule, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    doc = {
        "algorithm": "per_function_association_lavinium",
        "metric": "hot_inst_x10000_plus_obj_bytes",
        "budget_per_function": budget_per_fn,
        "max_functions": max_funcs,
        "cold_functions_tuned": len(results),
        "total_compiles": compile_counter,
        "compile_failures": compile_failures,
        "results": results,
        "schedule": str(out_schedule),
    }
    out_report.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-function WCET association (Lavinium-style)")
    ap.add_argument("config_json", type=Path, help="JSON 配置（见 run_ci 生成）")
    args = ap.parse_args()
    cfg = json.loads(args.config_json.read_text(encoding="utf-8"))
    hot_raw = cfg.get("hot_names") or cfg.get("pass_env", {}).get("PLC_FUSION_WCET_HOT_FUNCTIONS") or ""
    hot_names = [p.strip() for p in hot_raw.split(",") if p.strip()]
    doc = search_per_function(
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
        budget_per_fn=int(cfg.get("budget_per_fn", 12)),
        max_funcs=int(cfg.get("max_funcs", 5)),
        seed=int(cfg.get("seed", 42)),
        work_dir=Path(cfg.get("work_dir") or cfg.get("out_report")).parent / ".wcet_per_fn_search",
    )
    print(f"    tuned={doc['cold_functions_tuned']} compiles={doc['total_compiles']}")
    print(f"    schedule={doc['schedule']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
