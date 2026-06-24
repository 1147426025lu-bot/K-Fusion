#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plc_fusion_wcet_search__WCET搜索核心.py — RTSS 2025 有序 pass 序列遗传 WCET 搜索

基因: (kernel, cold_passes[], module_passes[]) — 顺序即 opt pipeline 顺序
适应度: abs_max_ns（板级）或 obj_bytes 代理
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PASS_LIB = os.path.join(_SCRIPT_DIR, "plc_fusion_wcet_passes__Pass序列库.py")
_spec = importlib.util.spec_from_file_location("plc_wcet_passes", _PASS_LIB)
_plib = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_plib)

KERNELS = _plib.KERNELS
FUNCTION_PASSES = _plib.FUNCTION_ATOMS  # compat alias
MODULE_PASSES = _plib.MODULE_PASSES
PAPER_COLD_SEEDS = _plib.PAPER_COLD_SEEDS
PAPER_MODULE_SEEDS = _plib.PAPER_MODULE_SEEDS
build_opt_passes = _plib.build_opt_passes
atoms_to_env = _plib.atoms_to_env
random_individual_genes = _plib.random_individual_genes
crossover_genes = _plib.crossover_genes
mutate_genes = _plib.mutate_genes
make_variant_name = _plib.make_variant_name
sequence_key = _plib.sequence_key


@dataclass
class Individual:
    kernel: str
    cold_passes: list[str] = field(default_factory=list)
    module_passes: list[str] = field(default_factory=list)
    name: str = ""
    opt_passes: str = ""
    obj_path: str = ""
    obj_bytes: int = 0
    abs_max_ns: Optional[int] = None
    fitness: Optional[int] = None
    probe_error: Optional[str] = None
    generation: int = 0

    def spec_key(self) -> str:
        return f"{self.kernel}|{sequence_key(self.cold_passes, self.module_passes)}"

    def refresh_name(self, prefix: str = "g") -> None:
        self.name = make_variant_name(self.kernel, self.cold_passes, self.module_passes, prefix)
        self.opt_passes = build_opt_passes(self.kernel, self.cold_passes, self.module_passes)


def individual_from_genes(kernel: str, cold: list[str], module: list[str]) -> Individual:
    ind = Individual(kernel=kernel, cold_passes=list(cold), module_passes=list(module))
    ind.refresh_name()
    return ind


def random_individual(rng: random.Random) -> Individual:
    k, c, m = random_individual_genes(rng)
    return individual_from_genes(k, c, m)


def crossover(a: Individual, b: Individual, rng: random.Random) -> Individual:
    k, c, m = crossover_genes(
        a.kernel, a.cold_passes, a.module_passes,
        b.kernel, b.cold_passes, b.module_passes,
        rng,
    )
    return individual_from_genes(k, c, m)


def mutate(ind: Individual, rng: random.Random, rate: float = 0.25) -> Individual:
    k, c, m = mutate_genes(ind.kernel, ind.cold_passes, ind.module_passes, rng, rate)
    return individual_from_genes(k, c, m)


def compile_variant(
    ind: Individual,
    *,
    pre_ll: str,
    out_dir: str,
    opt_bin: str,
    llc_bin: str,
    fusion_so: str,
    llc_arch: str,
    llc_attr: str,
    env: dict,
) -> bool:
    os.makedirs(out_dir, exist_ok=True)
    ind.refresh_name(f"g{ind.generation}")
    kll = os.path.join(out_dir, f"{ind.name}.ll")
    obj = os.path.join(out_dir, f"{ind.name}.o")
    err = os.path.join(out_dir, f"{ind.name}.opt.err")

    run_env = {**os.environ, **env}
    if ind.kernel == "plc-kernelize-wcet":
        hot = env.get("PLC_FUSION_WCET_HOT_FUNCTIONS") or env.get("PLC_FUSION_HOT_PATH_FUNCTIONS")
        if hot:
            run_env["PLC_FUSION_WCET_HOT_FUNCTIONS"] = hot

    try:
        r = subprocess.run(
            [
                opt_bin,
                "-load-pass-plugin=" + fusion_so,
                "-passes=" + ind.opt_passes,
                pre_ll,
                "-S",
                "-o",
                kll,
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env=run_env,
        )
        if r.returncode != 0:
            with open(err, "w", encoding="utf-8") as f:
                f.write(r.stderr or r.stdout)
            return False
        r2 = subprocess.run(
            [
                llc_bin,
                "-O3",
                "-relocation-model=pic",
                "-march=" + llc_arch,
                "-mattr=" + llc_attr,
                "-filetype=obj",
                kll,
                "-o",
                obj,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if r2.returncode != 0 or not os.path.isfile(obj):
            return False
        ind.obj_path = obj
        ind.obj_bytes = os.path.getsize(obj)
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False


def probe_wcet(
    ind: Individual,
    *,
    probe_sh: str,
    manifest: str,
    probe_sec: int,
    work_dir: str,
) -> None:
    stats = os.path.join(work_dir, f".wcet_genetic_{ind.name}.stats")
    env = {**os.environ, "WCET_PROBE_STATS": stats, "WCET_PROBE_TAG": ind.name}
    try:
        out = subprocess.run(
            ["bash", probe_sh, manifest, ind.obj_path, str(probe_sec)],
            capture_output=True,
            text=True,
            timeout=probe_sec + 120,
            env=env,
        )
        for line in out.stdout.splitlines():
            if line.startswith("abs_max_ns="):
                ind.abs_max_ns = int(line.split("=", 1)[1])
        if ind.abs_max_ns is None:
            ind.probe_error = (out.stderr or out.stdout)[-500:]
    except Exception as e:
        ind.probe_error = str(e)


def evaluate(
    ind: Individual,
    *,
    skip_insmod: bool,
    probe_sh: str,
    manifest: str,
    probe_sec: int,
    work_dir: str,
) -> int:
    if skip_insmod:
        ind.fitness = ind.obj_bytes
        return ind.fitness
    probe_wcet(ind, probe_sh=probe_sh, manifest=manifest, probe_sec=probe_sec, work_dir=work_dir)
    if ind.abs_max_ns is None:
        ind.fitness = 2**62
        return ind.fitness
    ind.fitness = ind.abs_max_ns
    return ind.fitness


def init_population(rng: random.Random, size: int) -> list[Individual]:
    pop: list[Individual] = []
    for cold in PAPER_COLD_SEEDS:
        if len(pop) >= size:
            break
        pop.append(individual_from_genes("plc-kernelize-wcet", list(cold), ("globaldce",)))
    while len(pop) < size:
        pop.append(random_individual(rng))
    rng.shuffle(pop)
    return pop[:size]


def run_genetic_search(
    *,
    pre_ll: str,
    out_dir: str,
    out_json: str,
    out_env: str,
    manifest: str,
    fuse_name: str,
    opt_bin: str,
    llc_bin: str,
    fusion_so: str,
    llc_arch: str = "aarch64",
    llc_attr: str = "-fp-armv8,-neon",
    pass_env: dict,
    probe_sh: str,
    skip_insmod: bool = True,
    probe_sec: int = 30,
    population: int = 8,
    generations: int = 4,
    elite: int = 2,
    seed: int = 42,
    apply: bool = False,
    work_dir: str = "",
) -> dict:
    rng = random.Random(seed)
    pop = init_population(rng, population)
    history: list[dict] = []
    best: Optional[Individual] = None

    for gen in range(generations):
        print(f"    🧬 generation {gen + 1}/{generations} (pop={len(pop)})")
        for ind in pop:
            ind.generation = gen
            if not compile_variant(
                ind,
                pre_ll=pre_ll,
                out_dir=out_dir,
                opt_bin=opt_bin,
                llc_bin=llc_bin,
                fusion_so=fusion_so,
                llc_arch=llc_arch,
                llc_attr=llc_attr,
                env=pass_env,
            ):
                ind.fitness = 2**62
                ind.probe_error = "compile_failed"
                continue
            evaluate(
                ind,
                skip_insmod=skip_insmod,
                probe_sh=probe_sh,
                manifest=manifest,
                probe_sec=probe_sec,
                work_dir=work_dir or out_dir,
            )
            if best is None or (ind.fitness or 2**62) < (best.fitness or 2**62):
                best = Individual(**{**asdict(ind)})

        ranked = sorted(pop, key=lambda x: x.fitness or 2**62)
        history.append(
            {
                "generation": gen,
                "best": asdict(ranked[0]) if ranked else None,
                "median_fitness": ranked[len(ranked) // 2].fitness if ranked else None,
            }
        )

        if gen + 1 >= generations:
            break

        next_pop = ranked[:elite]
        while len(next_pop) < population:
            p1, p2 = rng.sample(ranked[: max(elite * 2, 4)], 2)
            child = mutate(crossover(p1, p2, rng), rng)
            next_pop.append(child)
        pop = next_pop

    metric = "obj_bytes_proxy_no_insmod" if skip_insmod else "abs_max_ns"
    doc = {
        "fuse_name": fuse_name,
        "manifest": manifest,
        "algorithm": "genetic_wcet_ordered_passes_rtss2025",
        "generated": datetime.now(timezone.utc).astimezone().isoformat(),
        "population": population,
        "generations": generations,
        "elite": elite,
        "seed": seed,
        "skip_insmod": skip_insmod,
        "probe_sec": probe_sec,
        "metric": metric,
        "winner": asdict(best) if best else None,
        "history": history,
        "search_space": {
            "kernels": list(KERNELS),
            "function_passes": list(FUNCTION_PASSES),
            "module_passes": list(MODULE_PASSES),
            "paper_cold_seeds": [list(x) for x in PAPER_COLD_SEEDS],
        },
    }

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")

    if best:
        write_autotune_env(best, out_env, doc)
        if apply and not skip_insmod and best.obj_path:
            import shutil

            shipped = os.path.join(work_dir or out_dir, f"{fuse_name}_kernel.o")
            shipped_s = shipped + "_shipped"
            shutil.copy2(best.obj_path, shipped)
            shutil.copy2(best.obj_path, shipped_s)
            doc["applied_kernel_o"] = shipped
            doc["applied_kernel_o_shipped"] = shipped_s
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)
                f.write("\n")

    return doc


def write_autotune_env(best: Individual, out_env: str, doc: dict) -> None:
    cold_env = atoms_to_env(best.cold_passes)
    mod_csv = ",".join(best.module_passes)
    kernel_tag = best.kernel.replace("plc-kernelize-", "")
    lines = [
        f"# PLCFusion genetic WCET winner — {doc['generated']}",
        f"# metric={doc['metric']} fitness={best.fitness}",
        f"# variant={best.name}",
        f"# opt_passes={best.opt_passes}",
        f"FUSE_COLD_PASS_SEQUENCE={cold_env}",
        f"FUSE_MODULE_PASS_SEQUENCE={mod_csv}",
    ]
    if kernel_tag == "hotpath" and not best.cold_passes and not best.module_passes:
        lines += ["FUSE_PIPELINE=hotpath", "FUSE_WCET_MODE=1"]
    elif kernel_tag == "wcet":
        lines += ["FUSE_PIPELINE=wcet", "FUSE_WCET_MODE=1"]
    elif kernel_tag == "mainline" and not best.cold_passes:
        lines += ["FUSE_PIPELINE=mainline"]
    else:
        lines += [
            "FUSE_PIPELINE=custom",
            f"FUSE_KERNEL_PASS={best.kernel}",
        ]
    with open(out_env, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: plc_fusion_wcet_search__WCET搜索核心.py config.json", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        cfg = json.load(f)
    doc = run_genetic_search(**cfg)
    w = doc.get("winner")
    if not w:
        print("    winner=none", file=sys.stderr)
        return 1
    print(f"    winner={w['name']} {doc['metric']}={w.get('fitness')}")
    print(f"    opt_passes={w.get('opt_passes')}")
    print(f"    json={cfg['out_json']}")
    print(f"    env={cfg['out_env']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
