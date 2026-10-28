#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板上 A/B/C/D 短测：同一 CPU、同一 FIFO、同一 digest、统一 r/s/f。

自变量只有域（用户态 vs 内核）和每周期释放次数（N vs 1）。
报的是经验分位，不是 WCET。
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
OUT = os.environ.get("OUT_DIR", "/tmp/align_pipe")
REPORT = os.environ.get(
    "MATRIX_OUT",
    os.path.join(KF, "docs/pr3_abcd_matrix/report.json"))
LOGDIR = os.environ.get(
    "MATRIX_LOGDIR",
    os.path.join(os.path.dirname(REPORT), "logs"))
CYCLES = int(os.environ.get("CYCLES", "200"))
CPU = os.environ.get("MATRIX_CPU", "0")
FIFO = int(os.environ.get("MATRIX_FIFO", "80"))
PERIOD_US = int(os.environ.get("PERIOD_US", "1000"))
KDIR = os.environ.get("KERNELDIR", "/root/linux-5.15.86")
PY = os.environ.get("PYTHON", sys.executable)
CAPS = os.path.join(KF, "contracts/examples/platform_caps.json")


def sh(cmd, **kw):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, **kw)


def parse_user(text):
    rec = {"raw_tail": "\n".join(text.strip().splitlines()[-12:])}
    m = re.search(r"# digest: ([0-9a-f]+)", text)
    if m:
        rec["digest"] = m.group(1)
    m = re.search(r"# deadline_miss: (\d+)/(\d+)", text)
    if m:
        rec["miss"] = int(m.group(1))
        rec["cycles"] = int(m.group(2))
    for name in ("start_lat", "exec", "response", "span"):
        m = re.search(
            r"# stat_%s_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*max=([0-9.]+) over=(\d+)"
            % name, text)
        if m:
            rec[name] = {
                "min_us": float(m.group(1)),
                "avg_us": float(m.group(2)),
                "p50_us": int(m.group(3)),
                "max_us": float(m.group(4)),
                "over": int(m.group(5)),
            }
    return rec


def parse_rsf(text):
    rec = {"raw": text.strip()}
    m = re.search(r"miss=(\d+) periods=(\d+)", text)
    if m:
        rec["miss"] = int(m.group(1))
        rec["n"] = int(m.group(2))
    for name in ("start_lat", "exec", "response"):
        m = re.search(
            r"%s n=(\d+) over=(\d+) min=(\d+)ns p50=(-?\d+)us p99=(-?\d+)us "
            r"avg=(\d+)ns max=(\d+)ns" % name, text)
        if m:
            rec[name] = {
                "n": int(m.group(1)),
                "over": int(m.group(2)),
                "min_ns": int(m.group(3)),
                "p50_us": int(m.group(4)),
                "p99_us": int(m.group(5)),
                "avg_ns": int(m.group(6)),
                "max_ns": int(m.group(7)),
            }
    return rec


def compile_driver():
    src = os.path.join(OUT, "driver.c")
    binp = os.path.join(OUT, "driver")
    subprocess.check_call([
        "gcc", "-O2", "-std=gnu11", "-Wall", "-Werror",
        "-I", os.path.join(KF, "include"), src, "-o", binp, "-lrt",
    ])
    return binp


def run_user(binp, mode):
    cmd = [
        "taskset", "-c", CPU,
        "chrt", "-f", str(FIFO),
        binp, mode, str(CYCLES),
    ]
    out = sh(cmd).decode("utf-8", "replace")
    rec = parse_user(out)
    rec["cmd"] = cmd
    rec["domain"] = "linux_userspace"
    rec["fused"] = mode == "plan"
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, "user_%s.log" % mode), "w") as f:
        f.write(out)
    return rec


def emit_kmod(plan_path, dest, name):
    stages = json.load(open(os.path.join(OUT, "stages.json")))
    src = stages["source"]
    calls, digest = [], []
    for n, expr in (stages.get("calls") or {}).items():
        calls += ["--call", "%s=%s" % (n, expr.replace(" ", ""))]
    for g in stages.get("globals") or []:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", g):
            digest += ["--digest-var", g]
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    cmd = [
        PY, os.path.join(KF, "backend/synthesis/emit.py"),
        "--plan", plan_path, "--dag", os.path.join(OUT, "task.dag.json"),
        "--source", src, "--out-dir", dest, "--target", "linux-kernel",
        "--name", name,
    ] + calls + digest
    subprocess.check_call(cmd)
    shutil.copy(src, os.path.join(dest, "stage_impl.c"))
    subprocess.check_call(["make", "-C", KDIR, "M=" + dest, "modules"])
    return os.path.join(dest, name + ".ko")


def stamp_plan(plan_path, platform):
    subprocess.check_call([
        PY, os.path.join(KF, "scripts/ecos/plan_verify.py"),
        "--dag", os.path.join(OUT, "task.dag.json"),
        "--plan", plan_path,
        "--platforms", CAPS, "--platform", platform,
        "--stamp-plan",
        "-o", plan_path + ".verify.json",
    ])


def make_unfused_plan():
    fused = json.load(open(os.path.join(OUT, "plan.json")))
    dag = json.load(open(os.path.join(OUT, "task.dag.json")))
    offs = ((dag.get("phase") or {}).get("offsets") or {})
    order = fused.get("order") or []
    plan = json.loads(json.dumps(fused))
    plan["groups"] = [{
        "id": "g%d" % i,
        "members": [n],
        "release_phase_us": float(offs.get(n, i * 200)),
    } for i, n in enumerate(order)]
    plan["meta"] = dict(plan.get("meta") or {})
    plan["meta"]["fusion"] = False
    plan["meta"]["platform"] = "linux_kernel"
    if "verification" in plan:
        del plan["verification"]
    path = os.path.join(OUT, "plan_C_unfused.json")
    with open(path, "w") as f:
        json.dump(plan, f, indent=2)
        f.write("\n")
    stamp_plan(path, "linux_kernel")
    return path


def run_kmod(ko, name, hold_s=0.4):
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    sh(["insmod", ko, "period_us=%d" % PERIOD_US,
        "rt_priority=%d" % FIFO, "cpu=%s" % CPU])
    time.sleep(hold_s)
    rsf = open("/sys/module/%s/parameters/rsf_probe" % name).read()
    periods = open("/sys/module/%s/parameters/periods" % name).read().strip()
    missed = open("/sys/module/%s/parameters/missed" % name).read().strip()
    obs = {}
    base = "/sys/module/%s/parameters" % name
    for fn in os.listdir(base):
        if fn.startswith("g_"):
            obs[fn] = open(os.path.join(base, fn)).read().strip()
    subprocess.check_call(["rmmod", name])
    rec = parse_rsf(rsf)
    rec["periods"] = int(periods)
    rec["missed_backlog"] = int(missed)
    rec["sysfs"] = obs
    rec["domain"] = "linux_kernel"
    rec["ko"] = ko
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, "rsf_%s.txt" % name), "w") as f:
        f.write(rsf)
    return rec


def main():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    host = open("/proc/version").read().strip()
    report = {
        "kind": "abcd_factor_short",
        "claim": "empirical r/s/f on this host; not WCET",
        "controls": {
            "cpu": CPU, "fifo": FIFO, "period_us": PERIOD_US,
            "cycles_user": CYCLES,
        },
        "host": host,
        "cells": {},
    }

    binp = compile_driver()
    report["cells"]["A"] = run_user(binp, "flat")
    report["cells"]["A"]["id"] = "A"
    report["cells"]["B"] = run_user(binp, "plan")
    report["cells"]["B"]["id"] = "B"

    ko_d = os.path.join(OUT, "kmod_hrtimer", "five_stage_loop.ko")
    if not os.path.isfile(ko_d):
        stamp_plan(os.path.join(OUT, "plan.json"), "linux_kernel")
        ko_d = emit_kmod(os.path.join(OUT, "plan.json"),
                         os.path.join(OUT, "kmod_hrtimer"), "five_stage_loop")
    report["cells"]["D"] = run_kmod(ko_d, "five_stage_loop")
    report["cells"]["D"]["id"] = "D"
    report["cells"]["D"]["fused"] = True

    try:
        plan_c = make_unfused_plan()
        ko_c = emit_kmod(plan_c, os.path.join(OUT, "kmod_C"), "five_stage_c")
        report["cells"]["C"] = run_kmod(ko_c, "five_stage_c")
        report["cells"]["C"]["id"] = "C"
        report["cells"]["C"]["fused"] = False
    except Exception as e:
        report["cells"]["C"] = {"id": "C", "error": str(e), "legal_run": False}

    da, db = report["cells"]["A"].get("digest"), report["cells"]["B"].get("digest")
    report["digest_match_AB"] = bool(da and da == db)

    def r50(cell):
        return ((report["cells"].get(cell) or {}).get("response") or {}).get("p50_us")

    cand = os.path.join(OUT, "candidates.json")
    auto = {}
    if os.path.isfile(cand):
        doc = json.load(open(cand))
        auto["formula_chosen_this_pipe"] = {
            "id": doc.get("chosen"),
            "domain": (doc.get("chosen_record") or {}).get("domain"),
            "why": (doc.get("chosen_record") or {}).get("why"),
            "cost_us": ((doc.get("chosen_record") or {}).get("cost") or {}).get("total_us"),
        }
    # 公式：无内核附加税应选 D；有税应选 B。经验 R：融合 (B/D) ≪ 不融合 (A/C)。
    ra, rb, rc, rd = r50("A"), r50("B"), r50("C"), r50("D")
    auto["empirical_R_p50_us"] = {"A": ra, "B": rb, "C": rc, "D": rd}
    auto["fusion_helps"] = (
        ra is not None and rb is not None and rb < ra / 2
    )
    auto["kernel_fused_matches_user_fused"] = (
        rb is not None and rd is not None and abs(rb - rd) <= max(20, rb)
    )
    auto["interpretable"] = (
        "不融合 R≈相位跨度（A/C）；融合后 R 落到唤醒延迟量级（B/D）。"
        "c 相同时同分选用户态（不强制内核化）；内核 c 明显更低才选 D。"
        "B 与 D 经验 R 同量级，拒内核不是漏掉数量级收益。"
    )
    report["auto"] = auto
    report["note"] = (
        "A=userspace unfused (flat); B=userspace fused (plan); "
        "C=kernel unfused; D=kernel fused; auto=candidate_generate. "
        "Same taskset CPU and SCHED_FIFO. empirical, not WCET."
    )
    with open(REPORT, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(json.dumps({
        "wrote": REPORT,
        "digest_match_AB": report["digest_match_AB"],
        "A_R_p50": (report["cells"]["A"].get("response") or {}).get("p50_us"),
        "B_R_p50": (report["cells"]["B"].get("response") or {}).get("p50_us"),
        "D_R_p50": (report["cells"]["D"].get("response") or {}).get("p50_us"),
        "C": "error" if report["cells"]["C"].get("error") else
             (report["cells"]["C"].get("response") or {}).get("p50_us"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
