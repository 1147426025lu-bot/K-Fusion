#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-5B：UKU 混合部署闭环（ioctl，不是 IVSHMEM）。

gold（全用户态）与 mixed（中间计算进内核）digest / seq / g_out 必须一致。
经验值，不是 WCET。
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
SRC = os.path.join(KF, "examples/ecos/mixed_uku__无状态混合.c")
PIPE = os.path.join(KF, "scripts/ecos_from_c__从C编译.sh")
OUT = os.environ.get("PR5B_OUT", os.path.join(KF, "docs/pr5b_uku_mixed"))
WORK = os.environ.get("PR5B_WORK", "/tmp/pr5b_uku")
CYCLES = int(os.environ.get("CYCLES", "200"))
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


def parse(text):
    rec = {"raw_tail": "\n".join(text.strip().splitlines()[-20:])}
    m = re.search(r"# digest: ([0-9a-f]+)", text)
    if m:
        rec["digest"] = m.group(1)
    m = re.search(r"# uku_seq_mismatch: (\d+)/(\d+)", text)
    if m:
        rec["seq_mismatch"] = int(m.group(1))
        rec["cycles"] = int(m.group(2))
    m = re.search(r"# uku_ioctl_error: (\d+)/", text)
    if m:
        rec["ioctl_error"] = int(m.group(1))
    m = re.search(r"# last_seq: (\d+) g_in=(-?\d+) g_out=(-?\d+)", text)
    if m:
        rec["last_seq"] = int(m.group(1))
        rec["g_in"] = int(m.group(2))
        rec["g_out"] = int(m.group(3))
    m = re.search(
        r"# stat_response_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*max=([0-9.]+)",
        text)
    if m:
        rec["response"] = {
            "min_us": float(m.group(1)), "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)), "max_us": float(m.group(4)),
        }
    m = re.search(
        r"# stat_kernel_compute_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*max=([0-9.]+)",
        text)
    if m:
        rec["kernel_compute"] = {
            "min_us": float(m.group(1)), "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)), "max_us": float(m.group(4)),
        }
    m = re.search(
        r"# stat_ioctl_roundtrip_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*max=([0-9.]+)",
        text)
    if m:
        rec["ioctl_roundtrip"] = {
            "min_us": float(m.group(1)), "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)), "max_us": float(m.group(4)),
        }
    return rec


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    names = ["stage_sample", "stage_add", "stage_shift", "stage_sink"]
    cost = {
        "linux_userspace": {
            "stage_sample": 1.0, "stage_add": 80.0,
            "stage_shift": 80.0, "stage_sink": 1.0,
        },
        "linux_kernel": {"stage_add": 1.0, "stage_shift": 1.0},
    }
    costp = os.path.join(WORK, "stage_cost.json")
    os.makedirs(WORK, exist_ok=True)
    dump(costp, cost)
    pdir = os.path.join(WORK, "pipe")
    if os.path.isdir(pdir):
        shutil.rmtree(pdir)
    print("== 1) STKP 应选 UKU mixed ==")
    env = {
        "OUT_DIR": pdir, "SKIP_HOST": "0", "SKIP_KMOD": "0",
        "C_USER_US": "21", "C_KERNEL_US": "10", "HANDOFF_US": "1",
        "STAGE_COST": costp, "KERNELDIR": KDIR, "CYCLES": str(CYCLES),
    }
    log = sh(["bash", PIPE, SRC], env=env).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs", "pipe.log"), "w").write(log)
    deploy = json.load(open(os.path.join(pdir, "deploy.json")))
    stkp = json.load(open(os.path.join(pdir, "stkp.json")))
    rec = stkp.get("chosen_record") or {}
    adapter = (rec.get("plan") or {}).get("meta", {}).get("xdom_adapter")
    assert deploy.get("domain") == "mixed", deploy
    assert adapter == "uku_ioctl", adapter
    assert rec.get("deployment_ready"), rec

    binp = os.path.join(pdir, "driver")
    if not os.path.isfile(binp):
        print("无 driver", file=sys.stderr)
        return 2
    print("== 2) gold 全用户态 ==")
    gold_txt = sh(["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
                   binp, "gold", str(CYCLES)]).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs", "gold.log"), "w").write(gold_txt)
    gold = parse(gold_txt)

    print("== 3) insmod + mixed ==")
    kdir = os.path.join(pdir, "kmod_uku")
    kos = [f for f in os.listdir(kdir) if f.endswith(".ko")] if os.path.isdir(kdir) else []
    if not kos:
        print("没有 .ko", file=sys.stderr)
        dump(os.path.join(OUT, "report.json"), {
            "error": "no_ko", "deploy": deploy, "gold": gold})
        return 2
    name = kos[0][:-3]
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    sh(["insmod", os.path.join(kdir, kos[0])])
    time.sleep(0.05)
    print("== 3b) 独占 open（第二次必须 EBUSY）==")
    excl = subprocess.check_output([
        PY, "-c",
        "import errno, os\n"
        "fd = os.open('/dev/kfusion_uku', os.O_RDWR)\n"
        "try:\n"
        "    os.open('/dev/kfusion_uku', os.O_RDWR)\n"
        "    raise SystemExit('second open succeeded')\n"
        "except OSError as e:\n"
        "    if e.errno != errno.EBUSY:\n"
        "        raise SystemExit('errno=%s want EBUSY' % e.errno)\n"
        "os.close(fd)\n"
        "print('exclusive_ok')\n",
    ]).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs", "exclusive.log"), "w").write(excl)
    mixed_txt = sh(["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
                    binp, "mixed", str(CYCLES)]).decode("utf-8", "replace")
    open(os.path.join(OUT, "logs", "mixed.log"), "w").write(mixed_txt)
    mixed = parse(mixed_txt)
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))

    match = gold.get("digest") and gold.get("digest") == mixed.get("digest")
    seq_ok = mixed.get("seq_mismatch") == 0 and mixed.get("ioctl_error") == 0
    same_out = gold.get("g_out") == mixed.get("g_out") and gold.get("last_seq") == mixed.get("last_seq")
    report = {
        "kind": "pr5b_uku_mixed",
        "claim": "empirical UKU ioctl; not WCET; not IVSHMEM",
        "host": open("/proc/version").read().strip(),
        "controls": {"cpu": CPU, "fifo": FIFO, "cycles": CYCLES},
        "stkp": {
            "chosen": stkp.get("chosen"),
            "domain": rec.get("domain"),
            "adapter": adapter,
            "groups": [g.get("members") for g in (rec.get("plan") or {}).get("groups") or []],
            "domains": [g.get("domain") for g in (rec.get("plan") or {}).get("groups") or []],
            "claim": stkp.get("claim"),
        },
        "gold": gold,
        "mixed": mixed,
        "checks": {
            "stkp_uku": adapter == "uku_ioctl" and rec.get("deployment_ready"),
            "digest_match": bool(match),
            "seq_ok": bool(seq_ok),
            "same_final": bool(same_out),
            "exclusive_open": "exclusive_ok" in excl,
        },
    }
    dump(os.path.join(OUT, "report.json"), report)
    md = os.path.join(OUT, "RESULT_PR5B__混合UKU.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# PR-5B UKU 混合部署（ioctl，不是 IVSHMEM）\n\n")
        f.write("STKP chosen=%s adapter=%s groups=%s\n\n" % (
            stkp.get("chosen"), adapter, report["stkp"]["groups"]))
        f.write("| | gold 全用户态 | mixed 内核计算 |\n|---|---|---|\n")
        f.write("| digest | %s | %s |\n" % (gold.get("digest"), mixed.get("digest")))
        f.write("| last seq / g_out | %s / %s | %s / %s |\n" % (
            gold.get("last_seq"), gold.get("g_out"),
            mixed.get("last_seq"), mixed.get("g_out")))
        f.write("| seq mismatch | — | %s |\n" % mixed.get("seq_mismatch"))
        f.write("| R p50 (µs) | %s | %s |\n\n" % (
            (gold.get("response") or {}).get("p50_us"),
            (mixed.get("response") or {}).get("p50_us")))
        f.write("digest 一致：%s。seq/ioctl 无错：%s。终态一致：%s。\n"
                % (match, seq_ok, same_out))
        f.write("\n经验值，不是 WCET。内核交接走 miscdevice ioctl。\n")
    print(json.dumps(report["checks"], indent=2, ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
