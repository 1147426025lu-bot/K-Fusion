#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lavinium Association 策略（RTSS 2025 / Dardaillon et al. 扩展版）简化实现。

对有序 pass 序列建 lattice，按 WCET 反馈给 pass 赋权 (+ / ◦ / -)，再加权采样新序列。
K-Fusion 适应度：abs_max_ns（板级 probe）或 obj_bytes（CI/无 insmod）。
"""
from __future__ import annotations

import importlib.util
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_SCRIPT = Path(__file__).resolve().parent / "plc_fusion_wcet_passes__Pass序列库.py"
_spec = importlib.util.spec_from_file_location("plc_wcet_passes", _SCRIPT)
_plib = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_plib)

COLD_MUTATION_PALETTE = _plib.COLD_MUTATION_PALETTE
MODULE_PASSES = _plib.MODULE_PASSES
build_opt_passes = _plib.build_opt_passes
make_variant_name = _plib.make_variant_name
sequence_key = _plib.sequence_key


@dataclass
class EvaluatedSequence:
    kernel: str
    cold: list[str]
    module: list[str]
    fitness: int
    name: str = ""

    def key(self) -> tuple[str, ...]:
        return tuple(self.cold)

    def refresh_name(self) -> None:
        self.name = make_variant_name(self.kernel, self.cold, self.module, prefix="assoc")


@dataclass
class PassStats:
    positive: int = 0
    neutral: int = 0
    negative: int = 0

    def weight(self) -> float:
        return (self.positive / max(1, self.neutral)) * 0.85 + 0.15


class AssociationExplorer:
    """序列级 Association（非集合级），保留 pass 顺序信息。"""

    def __init__(self, rng: random.Random, palette: tuple[str, ...] = COLD_MUTATION_PALETTE):
        self.rng = rng
        self.palette = palette
        self.lattice: dict[tuple[str, ...], int] = {}
        self.pass_stats: dict[str, PassStats] = defaultdict(PassStats)

    def note_baseline(self, fitness: int) -> None:
        self.lattice[()] = fitness

    def characterize(self, seq: tuple[str, ...], fitness: int) -> None:
        if seq in self.lattice:
            return
        self.lattice[seq] = fitness
        # 找最大真子序列（按长度）
        best_sub: tuple[str, ...] | None = None
        best_wcet = None
        for sub, wcet in self.lattice.items():
            if sub == seq or len(sub) >= len(seq):
                continue
            if sub == seq[: len(sub)]:
                if best_sub is None or len(sub) > len(best_sub):
                    best_sub = sub
                    best_wcet = wcet
        if best_sub is None:
            char = "+"
        elif fitness < best_wcet:
            char = "+"
        elif fitness == best_wcet:
            char = "◦"
        else:
            char = "-"
        new_pass = seq[-1]
        st = self.pass_stats[new_pass]
        if char == "+":
            st.positive += 1
        elif char == "◦":
            st.neutral += 1
        else:
            st.negative += 1

    def weighted_sample_sequence(
        self,
        min_len: int = 2,
        max_len: int = 10,
    ) -> list[str]:
        n = self.rng.randint(min_len, max_len)
        seq: list[str] = []
        for _ in range(n):
            weights = []
            for p in self.palette:
                w = self.pass_stats[p].weight() if p in self.pass_stats else 1.0
                if self.pass_stats[p].negative > self.pass_stats[p].positive:
                    w *= 0.25
                weights.append(max(w, 0.05))
            total = sum(weights)
            r = self.rng.random() * total
            acc = 0.0
            pick = self.palette[0]
            for p, w in zip(self.palette, weights):
                acc += w
                if r <= acc:
                    pick = p
                    break
            seq.append(pick)
        return seq

    def clean_sequence(self, cold: list[str], fitness_fn: Callable[[list[str]], int]) -> list[str]:
        """Association cleaning：逐 pass 删除中性项。"""
        base = list(cold)
        base_fit = fitness_fn(base)
        out = list(base)
        i = 0
        while i < len(out):
            trial = out[:i] + out[i + 1 :]
            if not trial:
                i += 1
                continue
            f = fitness_fn(trial)
            if f <= base_fit:
                out = trial
                continue
            i += 1
        return out


def run_association_search(
    evaluate_fn: Callable[[str, list[str], list[str]], EvaluatedSequence],
    *,
    budget: int = 40,
    seed: int = 42,
    kernel: str = "plc-kernelize-wcet",
    module_passes: list[str] | None = None,
    initial_cold: list[str] | None = None,
) -> EvaluatedSequence:
    rng = random.Random(seed)
    module_passes = list(module_passes or ["globaldce"])
    explorer = AssociationExplorer(rng)

    baseline = evaluate_fn(kernel, list(initial_cold or []), module_passes)
    baseline.refresh_name()
    explorer.note_baseline(baseline.fitness)
    best = baseline

    # 初始随机种群
    for _ in range(min(8, budget // 4)):
        cold = [rng.choice(COLD_MUTATION_PALETTE) for _ in range(rng.randint(2, 6))]
        ev = evaluate_fn(kernel, cold, module_passes)
        ev.refresh_name()
        explorer.characterize(tuple(cold), ev.fitness)
        if ev.fitness < best.fitness:
            best = ev

    remaining = budget - 8
    for _ in range(max(0, remaining)):
        cold = explorer.weighted_sample_sequence()
        ev = evaluate_fn(kernel, cold, module_passes)
        ev.refresh_name()
        explorer.characterize(tuple(cold), ev.fitness)
        if ev.fitness < best.fitness:
            best = ev

    def fit_only(c: list[str]) -> int:
        return evaluate_fn(kernel, c, module_passes).fitness

    cleaned = explorer.clean_sequence(best.cold, fit_only)
    if cleaned != best.cold:
        ev = evaluate_fn(kernel, cleaned, module_passes)
        ev.refresh_name()
        if ev.fitness <= best.fitness:
            best = ev
    return best


def write_association_result(path: Path, winner: EvaluatedSequence, extra: dict | None = None) -> None:
    doc = {
        "policy": "association",
        "winner": {
            "name": winner.name,
            "kernel": winner.kernel,
            "cold_passes": winner.cold,
            "module_passes": winner.module,
            "fitness": winner.fitness,
            "opt_passes": build_opt_passes(winner.kernel, winner.cold, winner.module),
            "sequence_key": sequence_key(winner.cold, winner.module),
        },
    }
    if extra:
        doc.update(extra)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
