#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成对长测 B（用户态融合）对 D（内核融合）。

同一 CPU、同一 FIFO、同一五阶段任务，交错重复。经验分位，不是 WCET。
若 p50/p99 重叠，不得写「内核融合必然更好」。
"""
from __future__ import print_function

import json
import os
import re
import shutil
import subprocess
import sys
import time

KF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(KF, "examples/ecos/five_stage_loop__闭环五阶段示例.c")
PIPE = os.path.join(KF, "scripts/ecos_from_c__从C编译.sh")
OUT = os.environ.get("BD_OUT", os.path.join(KF, "docs/pr4c_bd_paired"))
WORK = os.environ.get("BD_WORK", "/tmp/pr4c_bd")
PAIRS = int(os.environ.get("BD_PAIRS", "7"))
CYCLES = int(os.environ.get("CYCLES", "2000"))
HOLD_S = float(os.environ.get("BD_HOLD_S", "2.0"))
CPU = os.environ.get("MATRIX_CPU", "0")
FIFO = int(os.environ.get("MATRIX_FIFO", "80"))
PERIOD_US = int(os.environ.get("PERIOD_US", "1000"))
KDIR = os.environ.get("KERNELDIR", "/root/linux-5.15.86")
PY = os.environ.get("PYTHON", sys.executable)


def dump(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def sh(cmd, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, env=e)


def parse_user_r(text):
    m = re.search(
        r"# stat_response_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*?"
        r"p99=(\d+).*max=([0-9.]+) over=(\d+)",
        text)
    rec = {}
    if m:
        rec = {
            "min_us": float(m.group(1)),
            "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)),
            "p99_us": int(m.group(4)),
            "max_us": float(m.group(5)),
            "over": int(m.group(6)),
        }
    d = re.search(r"# digest: ([0-9a-f]+)", text)
    miss = re.search(r"# deadline_miss: (\d+)/(\d+)", text)
    if d:
        rec["digest"] = d.group(1)
    if miss:
        rec["miss"] = int(miss.group(1))
        rec["cycles"] = int(miss.group(2))
    return rec


def parse_rsf_r(text):
    m = re.search(
        r"response n=(\d+) over=(\d+) min=(\d+)ns p50=(-?\d+)us p99=(-?\d+)us "
        r"avg=(\d+)ns max=(\d+)ns",
        text)
    rec = {}
    if m:
        rec = {
            "n": int(m.group(1)),
            "over": int(m.group(2)),
            "min_us": int(m.group(3)) / 1000.0,
            "p50_us": int(m.group(4)),
            "p99_us": int(m.group(5)),
            "max_us": int(m.group(7)) / 1000.0,
        }
    return rec


def median(xs):
    ys = sorted(xs)
    n = len(ys)
    if not n:
        return None
    if n % 2:
        return ys[n // 2]
    return 0.5 * (ys[n // 2 - 1] + ys[n // 2])


def pipe(outdir, c_k, extra, skip_kmod, stage_cost=None):
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir)
    env = {
        "OUT_DIR": outdir, "SKIP_HOST": "1", "SKIP_KMOD": skip_kmod,
        "C_USER_US": "21", "C_KERNEL_US": str(c_k),
        "KERNEL_EXTRA_US": str(extra), "KERNELDIR": KDIR, "CYCLES": "80",
    }
    if stage_cost:
        p = os.path.join(outdir, "stage_cost.in.json")
        dump(p, stage_cost)
        env["STAGE_COST"] = p
    sh(["bash", PIPE, SRC], env=env)


def run_B(binp, i):
    cmd = ["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
           binp, "plan", str(CYCLES)]
    text = sh(cmd).decode("utf-8", "replace")
    logd = os.path.join(OUT, "logs")
    os.makedirs(logd, exist_ok=True)
    open(os.path.join(logd, "B_%d.log" % i), "w").write(text)
    rec = parse_user_r(text)
    rec["id"] = "B"
    rec["pair"] = i
    return rec


def run_D(ko, name, i):
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    sh(["insmod", ko, "period_us=%d" % PERIOD_US,
        "rt_priority=%d" % FIFO, "cpu=%s" % CPU])
    time.sleep(HOLD_S)
    rsf = open("/sys/module/%s/parameters/rsf_probe" % name).read()
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    open(os.path.join(OUT, "logs", "D_%d.txt" % i), "w").write(rsf)
    rec = parse_rsf_r(rsf)
    rec["id"] = "D"
    rec["pair"] = i
    rec["legal_run"] = True
    return rec


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    names = ["stage_sample", "stage_fir", "stage_pid", "stage_clamp",
             "stage_plant"]
    cost = {
        "linux_userspace": {n: 1.0 for n in names},
        "linux_kernel": {n: 1.0 for n in names},
    }
    print("== 生成 B 用户态驱动（占位，STKP 选用户态） ==")
    bdir = os.path.join(WORK, "B")
    pipe(bdir, 21, 0, "1")
    subprocess.check_call([
        "gcc", "-O2", "-std=gnu11", "-Wall", "-Werror",
        "-I", os.path.join(KF, "include"),
        os.path.join(bdir, "driver.c"), "-o", os.path.join(WORK, "driver"),
        "-lrt",
    ])
    print("== 生成 D 内核模块（分域成本 + c_k=10） ==")
    ddir = os.path.join(WORK, "D")
    pipe(ddir, 10, 0, "0", stage_cost=cost)
    kdir = os.path.join(ddir, "kmod_hrtimer")
    kos = [f for f in os.listdir(kdir) if f.endswith(".ko")]
    if not kos:
        print("没有 .ko", file=sys.stderr)
        return 2
    name = kos[0][:-3]
    ko = os.path.join(kdir, kos[0])
    binp = os.path.join(WORK, "driver")

    pairs = []
    print("== 交错 %d 对，每对 B=%d 周期、D≈%.1fs ==" % (PAIRS, CYCLES, HOLD_S))
    for i in range(PAIRS):
        b = run_B(binp, i)
        d = run_D(ko, name, i)
        pairs.append({"i": i, "B": b, "D": d})
        print("  pair %d  B p50=%s p99=%s max=%.1f | D p50=%s p99=%s max=%.1f"
              % (i, b.get("p50_us"), b.get("p99_us"), b.get("max_us") or 0,
                 d.get("p50_us"), d.get("p99_us"), d.get("max_us") or 0))

    bp50 = [p["B"].get("p50_us") for p in pairs if p["B"].get("p50_us") is not None]
    dp50 = [p["D"].get("p50_us") for p in pairs if p["D"].get("p50_us") is not None]
    bp99 = [p["B"].get("p99_us") for p in pairs if p["B"].get("p99_us") is not None]
    dp99 = [p["D"].get("p99_us") for p in pairs if p["D"].get("p99_us") is not None]
    bmax = [p["B"].get("max_us") for p in pairs if p["B"].get("max_us") is not None]
    dmax = [p["D"].get("max_us") for p in pairs if p["D"].get("max_us") is not None]
    diffs = [p["D"]["p50_us"] - p["B"]["p50_us"]
             for p in pairs
             if p["B"].get("p50_us") is not None
             and p["D"].get("p50_us") is not None]
    overlap = True
    if bp50 and dp50:
        overlap = not (max(bp50) < min(dp50) or max(dp50) < min(bp50))
    report = {
        "kind": "bd_paired_long",
        "claim": "empirical paired B vs D; not WCET",
        "host": open("/proc/version").read().strip(),
        "controls": {
            "cpu": CPU, "fifo": FIFO, "period_us": PERIOD_US,
            "pairs": PAIRS, "cycles_B": CYCLES, "hold_s_D": HOLD_S,
        },
        "pairs": pairs,
        "summary": {
            "B_p50_median": median(bp50),
            "D_p50_median": median(dp50),
            "B_p99_median": median(bp99),
            "D_p99_median": median(dp99),
            "B_max_obs": max(bmax) if bmax else None,
            "D_max_obs": max(dmax) if dmax else None,
            "D_minus_B_p50": diffs,
            "p50_ranges_overlap": overlap,
        },
        "conclusion": (
            "p50 范围重叠，不能断言内核融合优于用户态融合"
            if overlap else
            "本台本负载下 p50 范围不重叠；仍不是 WCET，不得外推最坏情况"
        ),
    }
    dump(os.path.join(OUT, "report.json"), report)
    md = os.path.join(OUT, "RESULT_BD__成对长测.md")
    s = report["summary"]
    with open(md, "w", encoding="utf-8") as f:
        f.write("# B/D 成对长测（经验值，不是 WCET）\n\n")
        f.write("%d 对交错；B 每对 %d 周期；D 每对约 %.1fs。CPU%s FIFO%s T=%sµs。\n\n"
                % (PAIRS, CYCLES, HOLD_S, CPU, FIFO, PERIOD_US))
        f.write("| | B 用户态融合 | D 内核融合 |\n|---|---:|---:|\n")
        f.write("| p50 中位 (µs) | %s | %s |\n" % (s["B_p50_median"], s["D_p50_median"]))
        f.write("| p99 中位 (µs) | %s | %s |\n" % (s["B_p99_median"], s["D_p99_median"]))
        f.write("| 观测最大 (µs) | %s | %s |\n\n" % (s["B_max_obs"], s["D_max_obs"]))
        f.write("各对 D−B p50：%s。p50 范围重叠：%s。\n\n"
                % (s["D_minus_B_p50"], s["p50_ranges_overlap"]))
        f.write("%s\n" % report["conclusion"])
    print(json.dumps({"wrote": os.path.join(OUT, "report.json"),
                      "summary": s, "conclusion": report["conclusion"]},
                     indent=2, ensure_ascii=False))
    return 0 if bp50 and dp50 else 1


if __name__ == "__main__":
    sys.exit(main())
