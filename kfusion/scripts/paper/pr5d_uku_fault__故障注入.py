#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-5D：UKU 板级故障注入 + 逐拍 gold 对照。

二次打开 EBUSY；坏 magic/容量 EINVAL；坏序号 EILSEQ 且不改用户 out。
40 拍 ioctl 输出与公式 g_in=seq*17+3 一致。再跑 gold/mixed digest。
经验路径，不是 WCET。
"""
from __future__ import print_function

import json
import os
import re
import shutil
import subprocess
import sys

KF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(KF, "examples/ecos/mixed_uku__无状态混合.c")
PIPE = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
INJECT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "pr5d_uku_inject.c")
OUT = os.environ.get("PR5D_OUT", os.path.join(KF, "docs", "pr5d_uku_fault"))
WORK = os.environ.get("PR5D_WORK", "/tmp/pr5d_uku")
CYCLES = int(os.environ.get("CYCLES", "80"))
CPU = os.environ.get("MATRIX_CPU", "0")
FIFO = int(os.environ.get("MATRIX_FIFO", "80"))
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


def parse_driver(text):
    rec = {}
    m = re.search(r"# digest: ([0-9a-f]+)", text)
    if m:
        rec["digest"] = m.group(1)
    m = re.search(r"# last_seq: (\d+) g_in=(-?\d+) g_out=(-?\d+)", text)
    if m:
        rec["last_seq"] = int(m.group(1))
        rec["g_in"] = int(m.group(2))
        rec["g_out"] = int(m.group(3))
    m = re.search(r"# uku_seq_mismatch: (\d+)/", text)
    if m:
        rec["seq_mismatch"] = int(m.group(1))
    m = re.search(r"# uku_ioctl_error: (\d+)/", text)
    if m:
        rec["ioctl_error"] = int(m.group(1))
    return rec


def main():
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    cost = {
        "linux_userspace": {
            "stage_sample": 1.0, "stage_add": 80.0,
            "stage_shift": 80.0, "stage_sink": 1.0,
        },
        "linux_kernel": {"stage_add": 1.0, "stage_shift": 1.0},
    }
    costp = os.path.join(WORK, "stage_cost.json")
    dump(costp, cost)
    pdir = os.path.join(WORK, "pipe")
    if os.path.isdir(pdir):
        shutil.rmtree(pdir)
    print("== 1) 生成 UKU ==")
    log = sh(["bash", PIPE, SRC], env={
        "OUT_DIR": pdir, "SKIP_HOST": "0", "SKIP_KMOD": "0",
        "C_USER_US": "21", "C_KERNEL_US": "10", "HANDOFF_US": "1",
        "STAGE_COST": costp, "KERNELDIR": KDIR, "CYCLES": str(CYCLES),
    }).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs", "pipe.log"), "w").write(log)
    kdir = os.path.join(pdir, "kmod_uku")
    kos = [f for f in os.listdir(kdir) if f.endswith(".ko")] if os.path.isdir(kdir) else []
    if not kos:
        dump(os.path.join(OUT, "report.json"), {"error": "no_ko"})
        return 2
    hdr = os.path.join(kdir, "kfusion_uku.h")
    inj_bin = os.path.join(WORK, "inject")
    print("== 2) 编译注入器 ==")
    sh(["gcc", "-O2", "-std=gnu11", "-Wall", "-Werror",
        "-I", kdir, INJECT, "-o", inj_bin])
    name = kos[0][:-3]
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    sh(["insmod", os.path.join(kdir, kos[0])])
    print("== 3) 故障注入 ==")
    inj = sh([inj_bin]).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs", "inject.log"), "w").write(inj)
    checks = {
        "exclusive_ebusy": "exclusive_ebusy_ok" in inj,
        "bad_magic": "bad_magic_einval_ok" in inj,
        "bad_nin": "bad_nin_einval_ok" in inj,
        "bad_seq": "bad_seq_eilseq_ok" in inj,
        "replay_seq": "replay_seq1_eilseq_ok" in inj,
        "poison_nout": "poison_nout_einval_ok" in inj,
        "per_cycle_gold": "per_cycle_gold_ok" in inj,
        "inject_all": "all_ok" in inj,
    }
    print("== 4) gold / mixed digest ==")
    binp = os.path.join(pdir, "driver")
    gold = sh(["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
               binp, "gold", str(CYCLES)]).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs", "gold.log"), "w").write(gold)
    mixed = sh(["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
                binp, "mixed", str(CYCLES)]).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs", "mixed.log"), "w").write(mixed)
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    g, m = parse_driver(gold), parse_driver(mixed)
    checks["digest_match"] = bool(g.get("digest") and g.get("digest") == m.get("digest"))
    checks["same_final"] = g.get("g_out") == m.get("g_out") and g.get("last_seq") == m.get("last_seq")
    checks["driver_seq_ok"] = m.get("seq_mismatch") == 0 and m.get("ioctl_error") == 0
    report = {
        "kind": "pr5d_uku_fault",
        "claim": "fault injection + per-cycle gold; empirical; not WCET",
        "host": open("/proc/version").read().strip(),
        "inject_log_tail": inj.strip().splitlines()[-12:],
        "gold": g,
        "mixed": m,
        "checks": checks,
        "all_ok": all(checks.values()),
        "header": hdr,
    }
    dump(os.path.join(OUT, "report.json"), report)
    md = os.path.join(OUT, "RESULT_PR5D__故障注入.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# PR-5D UKU 故障注入\n\n")
        for k, v in checks.items():
            f.write("- %s: %s\n" % (k, v))
        f.write("\ngold/mixed digest %s / %s。经验值，不是 WCET。\n" % (
            g.get("digest"), m.get("digest")))
    print(json.dumps(checks, indent=2, ensure_ascii=False))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
