#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_window 板上反例：STKP 合法两段 vs 非法全融合。

契约：s3 ∈ [380, 520] µs。全融合把 s3 前拉到周期开头，编译器不得选它。
本脚本生成并跑两种驱动（经验值，不是 WCET）：
  legal  = STKP 入选 s1+s2 | s3+s4
  illegal= 手工全融合（仅作对照，不标记可部署）
"""
from __future__ import print_function

import json
import os
import re
import shutil
import subprocess
import sys

KF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(KF, "examples/ecos/chain_window__四段窗口示例.c")
PIPE = os.path.join(KF, "scripts/ecos_from_c__从C编译.sh")
CODEGEN = os.path.join(KF, "scripts/fusion_codegen__生成融合代码.py")
CONTRACT = os.path.join(KF, "contracts/examples/chain_window.contract.json")
PROBE = os.path.join(KF, "include/plc_probe__统一测量探针.h")
OUT = os.environ.get(
    "CW_OUT", os.path.join(KF, "docs/pr4c_chain_window"))
WORK = os.environ.get("CW_WORK", "/tmp/pr4c_chain_window")
CYCLES = int(os.environ.get("CYCLES", "200"))
CPU = os.environ.get("MATRIX_CPU", "0")
FIFO = int(os.environ.get("MATRIX_FIFO", "80"))
PY = os.environ.get("PYTHON", sys.executable)
WIN = (380.0, 520.0)


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


def parse_log(text):
    rec = {"digest": None, "miss": None}
    m = re.search(r"# digest: ([0-9a-f]+)", text)
    if m:
        rec["digest"] = m.group(1)
    m = re.search(r"# deadline_miss: (\d+)/(\d+)", text)
    if m:
        rec["miss"] = int(m.group(1))
        rec["cycles"] = int(m.group(2))
    inst = {}
    for m in re.finditer(
            r"# instant_([A-Za-z0-9_]+)_us: min=([0-9.]+) avg=([0-9.]+) "
            r"p50=(\d+) max=([0-9.]+)", text):
        inst[m.group(1)] = {
            "min_us": float(m.group(2)),
            "avg_us": float(m.group(3)),
            "p50_us": int(m.group(4)),
            "max_us": float(m.group(5)),
        }
    rec["instant"] = inst
    m = re.search(
        r"# stat_response_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*max=([0-9.]+)",
        text)
    if m:
        rec["response"] = {
            "min_us": float(m.group(1)), "p50_us": int(m.group(3)),
            "max_us": float(m.group(4)),
        }
    return rec


def in_window(p50):
    return p50 is not None and WIN[0] <= p50 <= WIN[1]


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    names = ["s1", "s2", "s3", "s4"]
    cost = {
        "linux_userspace": {n: 0.6 for n in names},
        "linux_kernel": {n: 0.6 for n in names},
    }
    dump(os.path.join(WORK, "stage_cost.json"), cost)

    print("== 1) ecos_from_c + TIME_CONTRACT + STAGE_COST ==")
    pipe_dir = os.path.join(WORK, "pipe")
    env = {
        "OUT_DIR": pipe_dir,
        "SKIP_HOST": "1",
        "SKIP_KMOD": "1",
        "C_USER_US": "21",
        "C_KERNEL_US": "21",
        "TIME_CONTRACT": CONTRACT,
        "STAGE_COST": os.path.join(WORK, "stage_cost.json"),
        "CYCLES": str(CYCLES),
    }
    log = sh(["bash", PIPE, SRC], env=env).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs/pipe.log"), "w").write(log)
    stkp = json.load(open(os.path.join(pipe_dir, "stkp.json")))
    rec = stkp.get("chosen_record") or {}
    groups = (rec.get("plan") or {}).get("groups") or []
    members = [g.get("members") for g in groups]
    domain = rec.get("domain")
    print("  STKP", stkp.get("chosen"), members, domain)

    legal_ok = (
        members == [["s1", "s2"], ["s3", "s4"]]
        and domain == "linux_userspace"
        and not os.path.isfile(os.path.join(pipe_dir, "kmod_hrtimer", "Kbuild"))
    )

    print("== 2) 编译合法驱动，并另写非法全融合对照（不部署） ==")
    src_abs = os.path.abspath(SRC)
    # 合法：已有 pipe/driver.c
    subprocess.check_call([
        "gcc", "-O2", "-std=gnu11", "-Wall", "-Werror",
        "-I", os.path.join(KF, "include"),
        os.path.join(pipe_dir, "driver.c"),
        "-o", os.path.join(WORK, "legal"), "-lrt",
    ])

    fused = {
        "version": 2,
        "meta": {
            "platform": "linux_userspace",
            "fusion": True,
            "generator": "ablation_full_fuse_not_deployable",
            "deployment_ready": False,
        },
        "order": names,
        "groups": [{
            "id": "g0", "members": names, "domain": "linux_userspace",
            "release_phase_us": 0.0,
        }],
        "domains": {n: "linux_userspace" for n in names},
    }
    fused_p = os.path.join(WORK, "illegal_fused.plan.json")
    dump(fused_p, fused)
    subprocess.check_call([
        PY, CODEGEN, fused_p,
        "-o", os.path.join(WORK, "illegal.c"),
        "--stage-source", src_abs,
        "--probe-header", PROBE,
        "--period-us", "1000", "--step-us", "200",
        "--cycles", str(CYCLES),
        "--digest-var", "cw_out", "--digest-var", "cw_c",
        "--expect-order", "s1,s2,s3,s4",
    ])
    subprocess.check_call([
        "gcc", "-O2", "-std=gnu11", "-Wall", "-Werror",
        "-I", os.path.join(KF, "include"),
        os.path.join(WORK, "illegal.c"),
        "-o", os.path.join(WORK, "illegal"), "-lrt",
    ])

    print("== 3) 板上跑 legal plan / illegal plan / legal flat ==")
    cells = {}
    for tag, binp, mode in (
            ("legal_plan", os.path.join(WORK, "legal"), "plan"),
            ("legal_flat", os.path.join(WORK, "legal"), "flat"),
            ("illegal_fused", os.path.join(WORK, "illegal"), "plan"),
    ):
        cmd = ["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
               binp, mode, str(CYCLES)]
        text = sh(cmd).decode("utf-8", "replace")
        open(os.path.join(OUT, "logs", tag + ".log"), "w").write(text)
        cells[tag] = parse_log(text)
        cells[tag]["cmd"] = cmd
        s3 = ((cells[tag].get("instant") or {}).get("s3") or {}).get("p50_us")
        cells[tag]["s3_p50_us"] = s3
        cells[tag]["s3_in_window"] = in_window(s3)
        print("  %s s3_p50=%s in_window=%s digest=%s" % (
            tag, s3, cells[tag]["s3_in_window"], cells[tag].get("digest")))

    report = {
        "kind": "chain_window_board",
        "claim": "empirical s3 instant vs [380,520]; not WCET",
        "host": open("/proc/version").read().strip(),
        "stkp": {
            "chosen": stkp.get("chosen"),
            "claim": stkp.get("claim"),
            "groups": members,
            "domain": domain,
            "pruned": stkp.get("pruned"),
        },
        "cells": cells,
        "window_us": {"earliest": WIN[0], "latest": WIN[1]},
        "pass": {
            "stkp_split": legal_ok,
            "legal_s3_in_window": cells["legal_plan"].get("s3_in_window"),
            "illegal_s3_early": (
                cells["illegal_fused"].get("s3_p50_us") is not None
                and cells["illegal_fused"]["s3_p50_us"] < WIN[0]
            ),
            "digest_legal_plan_flat": (
                cells["legal_plan"].get("digest")
                and cells["legal_plan"]["digest"] == cells["legal_flat"].get("digest")
            ),
        },
    }
    dump(os.path.join(OUT, "report.json"), report)
    md = os.path.join(OUT, "RESULT_chain_window__窗口反例.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# chain_window 窗口反例（经验值，不是 WCET）\n\n")
        f.write("s3 契约窗口 **[%d, %d] µs**。STKP 入选 `%s`。\n\n"
                % (WIN[0], WIN[1], members))
        f.write("| 驱动 | s3 p50 (µs) | 落在窗口 | digest |\n|---|---:|---|---|\n")
        for tag, label in (("legal_plan", "STKP 合法 plan"),
                           ("legal_flat", "同驱动 flat"),
                           ("illegal_fused", "非法全融合（对照）")):
            c = cells[tag]
            f.write("| %s | %s | %s | %s |\n" % (
                label, c.get("s3_p50_us"), c.get("s3_in_window"),
                c.get("digest")))
        f.write("\nSTKP 选两段且无 kmod：%s。非法全融合 s3 提前：%s。"
                "合法 plan/flat digest 一致：%s。\n"
                % (legal_ok, report["pass"]["illegal_s3_early"],
                   report["pass"]["digest_legal_plan_flat"]))
        f.write("\n日志：`logs/`。\n")
    print(json.dumps({"wrote": os.path.join(OUT, "report.json"),
                      "pass": report["pass"]}, indent=2, ensure_ascii=False))
    p = report["pass"]
    return 0 if all(p.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
