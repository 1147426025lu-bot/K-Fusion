#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTSS 2025 (Magnani et al.) 对齐的 LLVM NewPM pass 序列库。

冷路径序列 = 有序 pipeline 原子（function(...) 内逗号连接）：
  - 标量 function pass：instcombine, gvn, ...
  - loop 块（LLVM 17）：loop-mssa(loop-rotate,licm)

环境变量 FUSE_COLD_PASS_SEQUENCE 用 | 分隔原子（避免 loop-mssa 内逗号冲突）。
"""
from __future__ import annotations

import random
from typing import Iterable

# function(...) 内可直接排列的标量 pass
FUNCTION_ATOMS: tuple[str, ...] = (
    "mem2reg",
    "simplifycfg",
    "sroa",
    "early-cse",
    "instcombine",
    "reassociate",
    "lcssa",
    "dse",
    "gvn",
    "sccp",
    "adce",
    "bdce",
    "tailcallelim",
    "memcpyopt",
    "loop-unroll",
)

# loop pass 必须以 loop-mssa(...) 整块出现（LLVM 17 已验证）
LOOP_MSSA_BLOCKS: tuple[str, ...] = (
    "loop-mssa(loop-rotate,licm)",
    "loop-mssa(loop-rotate,licm,loop-simplifycfg)",
)

COLD_MUTATION_PALETTE: tuple[str, ...] = FUNCTION_ATOMS + LOOP_MSSA_BLOCKS

MODULE_PASSES: tuple[str, ...] = (
    "globaldce",
    "globalopt",
    "ipsccp",
)

KERNELS: tuple[str, ...] = (
    "plc-kernelize-hotpath",
    "plc-kernelize-wcet",
    "plc-kernelize-mainline",
    "plc-kernelize-minimal",
)

# ≈ O1 / O2 / O3 冷路径（PassBuilder 分解 + NewPM 语法）
PAPER_COLD_SEEDS: tuple[tuple[str, ...], ...] = (
    (),
    ("instcombine", "simplifycfg"),
    ("simplifycfg", "sroa", "early-cse", "instcombine"),
    (
        "simplifycfg",
        "sroa",
        "instcombine",
        "loop-mssa(loop-rotate,licm)",
        "loop-unroll",
        "gvn",
        "adce",
        "instcombine",
    ),
    (
        "simplifycfg",
        "sroa",
        "instcombine",
        "loop-mssa(loop-rotate,licm)",
        "loop-unroll",
        "gvn",
        "dse",
        "adce",
        "bdce",
        "instcombine",
    ),
)

PAPER_MODULE_SEEDS: tuple[tuple[str, ...], ...] = (
    (),
    ("globaldce",),
    ("globaldce", "globalopt"),
    ("globalopt", "globaldce"),
)

PAPER_NAMED_PRESETS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "paper-hot-none": ("plc-kernelize-wcet", (), ()),
    "paper-o1-cold": (
        "plc-kernelize-wcet",
        ("simplifycfg", "sroa", "early-cse", "instcombine"),
        ("globaldce",),
    ),
    "paper-o2-cold": (
        "plc-kernelize-wcet",
        PAPER_COLD_SEEDS[3],
        ("globaldce",),
    ),
    "paper-o3-cold": (
        "plc-kernelize-wcet",
        PAPER_COLD_SEEDS[4],
        ("globaldce", "globalopt"),
    ),
    "paper-o2-no-unroll": (
        "plc-kernelize-wcet",
        (
            "simplifycfg",
            "sroa",
            "instcombine",
            "loop-mssa(loop-rotate,licm)",
            "gvn",
            "adce",
        ),
        ("globaldce",),
    ),
    "paper-hotpath": ("plc-kernelize-hotpath", (), ()),
}

# pipeline wcet profile 默认 O2 冷路径
DEFAULT_WCET_COLD = PAPER_COLD_SEEDS[3]
DEFAULT_WCET_MODULE = ("globaldce",)

ATOM_SEP = "|"


def atoms_to_env(atoms: Iterable[str]) -> str:
    return ATOM_SEP.join(p for p in atoms if p)


def env_to_atoms(spec: str) -> list[str]:
    if not spec or not spec.strip():
        return []
    if ATOM_SEP in spec:
        return [a.strip() for a in spec.split(ATOM_SEP) if a.strip()]
    return parse_legacy_cold_csv(spec)


def parse_legacy_cold_csv(spec: str) -> list[str]:
    """兼容旧逗号格式；识别 loop-mssa(...) 块。"""
    atoms: list[str] = []
    i = 0
    s = spec.strip()
    while i < len(s):
        if s.startswith("loop-mssa(", i):
            j = s.index(")", i)
            atoms.append(s[i : j + 1])
            i = j + 1
            if i < len(s) and s[i] == ",":
                i += 1
            continue
        j = s.find(",", i)
        if j == -1:
            j = len(s)
        token = s[i:j].strip()
        if token.startswith("function(") and token.endswith(")"):
            inner = token[len("function(") : -1]
            atoms.extend(parse_legacy_cold_csv(inner))
        elif token:
            atoms.append(token)
        i = j + 1 if j < len(s) else len(s)
    return atoms


def format_function_pipeline(atoms: Iterable[str]) -> str:
    seq = [a for a in atoms if a]
    if not seq:
        return ""
    return "function(" + ",".join(seq) + ")"


def format_module_passes(passes: Iterable[str]) -> str:
    return ",".join(p for p in passes if p)


def build_opt_passes(
    kernel: str,
    cold_atoms: Iterable[str],
    module_passes: Iterable[str],
) -> str:
    parts = [kernel]
    fn = format_function_pipeline(cold_atoms)
    if fn:
        parts.append(fn)
    mod = format_module_passes(module_passes)
    if mod:
        parts.append(mod)
    return ",".join(parts)


def parse_pass_sequence(spec: str) -> tuple[list[str], list[str]]:
    cold = env_to_atoms(spec)
    return cold, []


def sequence_key(cold: Iterable[str], module: Iterable[str]) -> str:
    return atoms_to_env(cold) + "||" + "|".join(module)


def abbrev_passes(passes: Iterable[str], max_len: int = 28) -> str:
    parts = []
    for p in passes:
        if p.startswith("loop-mssa("):
            parts.append("lmssa")
        elif p == "instcombine":
            parts.append("ic")
        elif p == "simplifycfg":
            parts.append("scfg")
        elif p.startswith("loop-"):
            parts.append(p.replace("loop-", "l"))
        else:
            parts.append(p[:4])
    s = "+".join(parts) if parts else "none"
    return s[:max_len]


def make_variant_name(
    kernel: str,
    cold: Iterable[str],
    module: Iterable[str],
    prefix: str = "g",
) -> str:
    k = kernel.replace("plc-kernelize-", "")
    c = abbrev_passes(cold)
    m = abbrev_passes(module) if module else "nomod"
    return f"{prefix}-{k}-{c}-{m}"[:96]


def random_cold_sequence(rng: random.Random, min_len: int = 2, max_len: int = 10) -> list[str]:
    n = rng.randint(min_len, max_len)
    return [rng.choice(COLD_MUTATION_PALETTE) for _ in range(n)]


def random_module_sequence(rng: random.Random) -> list[str]:
    if rng.random() < 0.35:
        return []
    n = rng.randint(1, min(2, len(MODULE_PASSES)))
    picks = list(MODULE_PASSES)
    rng.shuffle(picks)
    return picks[:n]


def random_individual_genes(rng: random.Random) -> tuple[str, list[str], list[str]]:
    if rng.random() < 0.5:
        kernel = rng.choice(KERNELS)
        cold = list(rng.choice(PAPER_COLD_SEEDS))
        module = list(rng.choice(PAPER_MODULE_SEEDS))
    else:
        kernel = rng.choice(KERNELS)
        cold = random_cold_sequence(rng)
        module = random_module_sequence(rng)
    return kernel, cold, module


def ox_crossover_list(a: list[str], b: list[str], rng: random.Random) -> list[str]:
    if not a:
        return list(b)
    if not b:
        return list(a)
    if len(a) == 1:
        return list(a)
    i, j = sorted(rng.sample(range(len(a)), 2))
    segment = a[i : j + 1]
    rest = [x for x in b if x not in segment]
    out: list[str] = []
    seg_i = 0
    rest_i = 0
    for _ in range(len(a)):
        if seg_i < len(segment) and (rest_i >= len(rest) or rng.random() < 0.5):
            out.append(segment[seg_i])
            seg_i += 1
        elif rest_i < len(rest):
            out.append(rest[rest_i])
            rest_i += 1
        elif seg_i < len(segment):
            out.append(segment[seg_i])
            seg_i += 1
    seen: set[str] = set()
    dedup: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup or list(a)


def mutate_pass_list(
    seq: list[str],
    palette: tuple[str, ...],
    rng: random.Random,
    rate: float = 0.2,
    max_len: int = 12,
) -> list[str]:
    out = list(seq)
    if rng.random() < rate and len(out) < max_len:
        pos = rng.randint(0, len(out))
        out.insert(pos, rng.choice(palette))
    if rng.random() < rate and len(out) > 1:
        del out[rng.randrange(len(out))]
    if rng.random() < rate and len(out) >= 2:
        i, j = rng.sample(range(len(out)), 2)
        out[i], out[j] = out[j], out[i]
    if rng.random() < rate and out:
        out[rng.randrange(len(out))] = rng.choice(palette)
    if rng.random() < rate * 0.5 and len(out) >= 2:
        i = rng.randrange(len(out) - 1)
        out[i], out[i + 1] = out[i + 1], out[i]
    return out


def crossover_genes(
    ak: str,
    ac: list[str],
    am: list[str],
    bk: str,
    bc: list[str],
    bm: list[str],
    rng: random.Random,
) -> tuple[str, list[str], list[str]]:
    kernel = ak if rng.random() < 0.5 else bk
    cold = ox_crossover_list(ac, bc, rng)
    module = ox_crossover_list(am, bm, rng)
    return kernel, cold, module


def mutate_genes(
    kernel: str,
    cold: list[str],
    module: list[str],
    rng: random.Random,
    rate: float = 0.25,
) -> tuple[str, list[str], list[str]]:
    k = kernel
    if rng.random() < rate:
        k = rng.choice(KERNELS)
    c = mutate_pass_list(cold, COLD_MUTATION_PALETTE, rng, rate)
    m = mutate_pass_list(module, MODULE_PASSES, rng, rate, max_len=3)
    return k, c, m


def tail_only(cold: Iterable[str], module: Iterable[str]) -> str:
    parts = []
    fn = format_function_pipeline(cold)
    if fn:
        parts.append(fn)
    mp = format_module_passes(module)
    if mp:
        parts.append(mp)
    return ",".join(parts)


def sweep_variant_specs() -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for name, (kernel, cold, module) in PAPER_NAMED_PRESETS.items():
        rows.append((name, kernel, atoms_to_env(cold), ",".join(module)))
    return rows


def sweep_shell_specs() -> list[str]:
    out: list[str] = []
    for name, kernel, cold_env, mod_csv in sweep_variant_specs():
        cold = env_to_atoms(cold_env)
        mod = [p for p in mod_csv.split(",") if p] if mod_csv else []
        out.append(f"{name}|{kernel}|{tail_only(cold, mod)}")
    return out


def greedy_shell_specs() -> list[str]:
    o2 = list(PAPER_COLD_SEEDS[3])
    specs: list[str] = []
    specs.append(
        "greedy+paper-o2-no-unroll|plc-kernelize-wcet|"
        + tail_only(PAPER_NAMED_PRESETS["paper-o2-no-unroll"][1], ("globaldce",))
    )
    rev = list(reversed(o2))
    specs.append(
        "greedy+paper-o2-rev|plc-kernelize-wcet|" + tail_only(rev, ("globaldce",))
    )
    ic_first = ["instcombine"] + [p for p in o2 if p != "instcombine"]
    specs.append(
        "greedy+paper-ic-first|plc-kernelize-wcet|" + tail_only(ic_first, ("globaldce",))
    )
    specs.append(
        "greedy+paper-o1-only|plc-kernelize-wcet|"
        + tail_only(PAPER_NAMED_PRESETS["paper-o1-cold"][1], ("globaldce",))
    )
    return specs


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "--sweep-specs":
        for line in sweep_shell_specs():
            print(line)
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--greedy-specs":
        for line in greedy_shell_specs():
            print(line)
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--format-tail":
        cold = env_to_atoms(sys.argv[2]) if len(sys.argv) > 2 else []
        mod = [p for p in sys.argv[3].split(",") if p] if len(sys.argv) > 3 else []
        print(tail_only(cold, mod))
        sys.exit(0)
