#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr10e_baseline__统计基线.py — PR-10E 阶段 A：冻结可运行内核的统计基线。

评审要求：不改内核、先把基线做扎实——三种部署在**等价调度条件**下各做 ≥10 轮
独立重复，保留**逐拍原始数据**与每轮分位数，并记录影响延迟的环境变量。

本脚本做的事：
  1) 用同一条流水线重建两种用户态参与的部署（全用户态 / 部分内核化 UKU），
     驱动带 `csv` 模式，逐拍输出 (k, start_lat_ns, exec_ns, resp_ns)。
  2) 每种部署 × 调度类(CFS / FIFO 99) 各跑 N 轮，CPU 亲和性与优先级固定并记录。
  3) 逐轮从**原始样本**重算 p50/p99/p99.9/max/超期，再跨轮给中位数与极差——
     轮间波动本身就是要复现的对象（12.41 → 23.30 µs）。
  4) 记录 governor / 频率 / 温度 / loadavg / RT 运行时配额，便于解释轮间差异。

阶段 A 明确不做的事：不改内核、不开 tracing、不做机制归因。

用法：
  python3 kfusion/scripts/pr10e_baseline__统计基线.py --repeat 10 --cycles 5000
"""
from __future__ import print_function

import argparse
import io
import json
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)

SRC = os.path.join(KF, "examples", "ecos", "mixed_pid_stateful__积分混合.c")
ECOS_FROM_C = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
OUT_ROOT = os.path.join(KF, "docs", "pr10e_baseline")

# 只构建一份 UKU 产物：全用户态与部分内核化**共用同一份驱动**，
# 二者只差中间组是否走 ioctl —— 这样比较的是部署差异，不是两份代码的差异。
BUILD = {"u": 5.0, "k": 0.05, "ioctl": 0.3}
MODES = {"user_only": "gold", "partial_kernel": "mixed"}
SCHEDS = ["cfs", "fifo99"]
PIN_CPU = 3


def run(cmd, env=None):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=env)
    out = p.communicate()[0]
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return p.returncode, out


def readf(path, default=None):
    try:
        return io.open(path, encoding="utf-8").read().strip()
    except Exception:
        return default


def env_snapshot():
    return {
        "governor": readf("/sys/devices/system/cpu/cpu%d/cpufreq/scaling_governor"
                          % PIN_CPU),
        "cur_freq_khz": readf("/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq"
                              % PIN_CPU),
        "temp_mc": readf("/sys/class/thermal/thermal_zone0/temp"),
        "loadavg": readf("/proc/loadavg"),
        "rt_runtime_us": readf("/proc/sys/kernel/sched_rt_runtime_us"),
        "nproc": readf("/proc/cpuinfo") is not None and
                 len([l for l in (readf("/proc/cpuinfo") or "").splitlines()
                      if l.startswith("processor")]) or None,
        "pin_cpu": PIN_CPU,
    }


def build(scn, spec, out):
    gen = os.path.join(out, "gen_%s" % scn)
    os.makedirs(gen, exist_ok=True)
    env = dict(os.environ)
    env.update({"OUT_DIR": gen, "CYCLES": "1"})
    rc, log = run(["bash", ECOS_FROM_C, SRC], env=env)
    if rc != 0:
        return {"status": "pipeline_failed", "log_tail": log.splitlines()[-6:]}
    st = os.path.join(gen, "stages.json")
    names = [s["name"] for s in
             (json.load(io.open(st, encoding="utf-8")).get("stages") or [])] \
        if os.path.isfile(st) else []
    cost = {
        "_note": "PR-10E 阶段A：与 PR-10B 相同的域级量级假设，用于选定部署",
        "linux_userspace": {n: {"value": spec["u"], "evidence": "scenario"}
                            for n in names},
        "linux_kernel": {n: {"value": spec["k"], "evidence": "scenario"}
                         for n in names},
    }
    cp = os.path.join(out, "cost_%s.json" % scn)
    with io.open(cp, "w", encoding="utf-8") as f:
        json.dump(cost, f, ensure_ascii=False, indent=2)
        f.write("\n")
    env.update({"STAGE_COST": cp,
                "IOCTL_COST": json.dumps({"value": spec["ioctl"],
                                          "evidence": "scenario"})})
    rc, log = run(["bash", ECOS_FROM_C, SRC], env=env)
    with io.open(os.path.join(out, "pipeline_%s.log" % scn), "w",
                 encoding="utf-8") as f:
        f.write(log)
    if rc != 0:
        return {"status": "pipeline_failed", "log_tail": log.splitlines()[-6:]}
    driver = os.path.join(gen, "driver")
    ko = None
    kd = os.path.join(gen, "kmod_uku")
    if os.path.isdir(kd):
        for fn in sorted(os.listdir(kd)):
            if fn.endswith(".ko"):
                ko = os.path.join(kd, fn)
                break
    if not os.path.isfile(driver):
        return {"status": "no_driver"}
    plan = json.load(io.open(os.path.join(gen, "plan.json"), encoding="utf-8"))
    sess = ((plan.get("meta") or {}).get("uku_session") or {}).get("region")
    return {"status": "ok", "driver": driver, "ko": ko,
            "mode": "mixed" if sess else "gold", "session": sess}


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def parse_csv(text):
    """兼容 4 列（k,start,exec,resp）与 6 列（+cpu_in_work,preempt_in_work）。"""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 4:
            continue
        try:
            rows.append(tuple(int(x) for x in parts))
        except ValueError:
            continue
    return rows


def measure(cell, mode, sched, cycles, repeat, out_dir):
    runs = []
    for i in range(repeat):
        before = env_snapshot()
        insmoded = False
        if mode == "mixed" and cell.get("ko"):
            rc_i, _ = run(["insmod", cell["ko"]])
            insmoded = (rc_i == 0)
            if not insmoded:
                return {"status": "insmod_failed", "runs": runs}
        cmd = ["taskset", "-c", str(PIN_CPU)]
        if sched == "fifo99":
            cmd += ["chrt", "-f", "99"]
        cmd += [cell["driver"], mode, str(cycles), "csv"]
        try:
            rc, txt = run(cmd)
        finally:
            if insmoded:
                run(["rmmod", os.path.basename(cell["ko"])[:-3]])
        after = env_snapshot()
        rows = parse_csv(txt)
        ncol = len(rows[0]) if rows else 4
        hdr = {4: "k,start_lat_ns,exec_ns,resp_ns",
               6: ("k,start_lat_ns,exec_ns,resp_ns,"
                   "cpu_in_work_ns,preempt_in_work_ns")}.get(ncol, "k")
        with io.open(os.path.join(out_dir, "r%02d.csv" % i), "w",
                     encoding="utf-8", newline="\n") as f:
            f.write(hdr + "\n")
            for r in rows:
                f.write(",".join(str(x) for x in r) + "\n")
        if not rows:
            runs.append({"repeat": i, "rc": rc, "n": 0, "env_before": before,
                         "env_after": after})
            continue
        start = [r[1] for r in rows]
        resp = [r[3] for r in rows]
        deadline_ns = 1000 * 1000
        extra = {}
        if len(rows[0]) >= 6:
            cpu = [r[4] for r in rows]
            pre = [r[5] for r in rows]
            extra["cpu_in_work"] = {
                "p50_us": pct(cpu, .5) / 1000.0, "p99_us": pct(cpu, .99) / 1000.0,
                "max_us": max(cpu) / 1000.0,
                "avg_us": round(sum(cpu) / float(len(cpu)) / 1000.0, 3)}
            extra["preempt_in_work"] = {
                "p50_us": pct(pre, .5) / 1000.0, "p99_us": pct(pre, .99) / 1000.0,
                "max_us": max(pre) / 1000.0,
                "avg_us": round(sum(pre) / float(len(pre)) / 1000.0, 3)}
            # 核心归因：超期那一拍到底是在等 CPU 还是在跑？
            mr = [r for r in rows if r[3] > deadline_ns]
            orr = [r for r in rows if r[3] <= deadline_ns]
            def _p50(rows, idx):
                return (pct([r[idx] for r in rows], .5) / 1000.0) if rows else None
            extra["miss_breakdown"] = {
                "n_miss": len(mr),
                # start_lat = 名义释放到真正开始执行（等调度/唤醒延迟）
                "miss_start_p50_us": _p50(mr, 1),
                "ok_start_p50_us": _p50(orr, 1),
                # work = 开始执行到结束（wall）
                "miss_work_p50_us": _p50(mr, 2),
                "ok_work_p50_us": _p50(orr, 2),
                "miss_resp_p50_us": _p50(mr, 3),
                "ok_resp_p50_us": _p50(orr, 3),
                # cpu = 这段时间真正在 CPU 上的时间
                "miss_cpu_p50_us": _p50(mr, 4),
                "ok_cpu_p50_us": _p50(orr, 4),
                # preempt = work - cpu（修正顺序后应 >= 0，负值说明测量区间错）
                "miss_preempt_p50_us": _p50(mr, 5),
                "ok_preempt_p50_us": _p50(orr, 5),
                # 最重要的单指标：响应时间里「不在 CPU 上」的部分
                "miss_offcpu_p50_us": _p50([(0, 0, 0, r[3], r[3] - r[4]) for r in mr], 4),
                "ok_offcpu_p50_us": _p50([(0, 0, 0, r[3], r[3] - r[4]) for r in orr], 4),
            }
        runs.append({
            "repeat": i, "rc": rc, "n": len(rows),
            "start_lat": {"p50_us": pct(start, .5) / 1000.0,
                          "p99_us": pct(start, .99) / 1000.0,
                          "p999_us": pct(start, .999) / 1000.0,
                          "max_us": max(start) / 1000.0,
                          "avg_us": round(sum(start) / float(len(start)) / 1000.0, 3)},
            "response": {"p50_us": pct(resp, .5) / 1000.0,
                         "p99_us": pct(resp, .99) / 1000.0,
                         "p999_us": pct(resp, .999) / 1000.0,
                         "max_us": max(resp) / 1000.0,
                         "avg_us": round(sum(resp) / float(len(resp)) / 1000.0, 3)},
            "miss": sum(1 for r in rows if r[3] > deadline_ns),
            "env_before": before, "env_after": after,
        })
        runs[-1].update(extra)
    return {"status": "ok", "runs": runs}


def agg(runs, key, field):
    vals = [r[key][field] for r in runs if r.get(key) and r[key].get(field)
            is not None]
    if not vals:
        return None
    return {"median": statistics.median(vals), "min": min(vals), "max": max(vals),
            "spread": round(max(vals) - min(vals), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=10)
    ap.add_argument("--cycles", type=int, default=5000)
    ap.add_argument("--scheds", nargs="+", default=SCHEDS)
    ap.add_argument("--out", default=OUT_ROOT)
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    print("[build] UKU 产物（gold/mixed 共用）", file=sys.stderr)
    b = build("uku", BUILD, a.out)
    if b["status"] != "ok":
        print("构建失败：%s" % (b.get("log_tail") or b["status"]), file=sys.stderr)
        return 3

    cells = []
    for deploy, mode in MODES.items():
        entry = {"scenario": deploy, "status": "ok", "mode": mode,
                 "driver": os.path.relpath(b["driver"], REPO),
                 "ko": os.path.relpath(b["ko"], REPO) if b.get("ko") else None,
                 "conditions": []}
        for sched in a.scheds:
            d = os.path.join(a.out, "runs_%s_%s" % (deploy, sched))
            os.makedirs(d, exist_ok=True)
            print("  [run] %s(%s) × %s × %d 轮"
                  % (deploy, mode, sched, a.repeat), file=sys.stderr)
            res = measure(b, mode, sched, a.cycles, a.repeat, d)
            cond = {"sched": sched, "status": res["status"], "runs": res["runs"],
                    "dir": os.path.relpath(d, REPO)}
            r = res["runs"]
            cond["summary"] = {
                "start_p50": agg(r, "start_lat", "p50_us"),
                "start_p99": agg(r, "start_lat", "p99_us"),
                "resp_p50": agg(r, "response", "p50_us"),
                "resp_max": agg(r, "response", "max_us"),
                "miss_total": sum(x.get("miss") or 0 for x in r),
                "n_ok": sum(1 for x in r if x.get("n")),
            }
            entry["conditions"].append(cond)
        cells.append(entry)

    doc = {"kind": "pr10e_baseline", "repeat": a.repeat, "cycles": a.cycles,
           "pin_cpu": PIN_CPU, "scheds": a.scheds, "cells": cells,
           "env": env_snapshot(),
           "boundary": ("阶段A：不改内核、不开 tracing。逐拍原始数据在 runs_*/*.csv。"
                        "跨轮极差本身是复现对象，不是噪声。")}
    with io.open(os.path.join(a.out, "baseline.json"), "w",
                 encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("\n%-16s %-8s %14s %14s %10s %8s" %
          ("deploy", "sched", "start_p50(med)", "start_p99(med)", "resp_max(med)",
           "miss"), file=sys.stderr)
    for c in cells:
        if c["status"] != "ok":
            print("%-16s %-8s %s" % (c["scenario"], "-", c["status"]),
                  file=sys.stderr)
            continue
        for cond in c["conditions"]:
            s = cond["summary"]
            def f(x, k):
                return "%.2f" % x[k] if x and x.get(k) is not None else "-"
            sp = s["start_p50"]
            rsp = s["resp_max"]
            print("%-16s %-8s %8s±%-5s %8s±%-5s %10s %8s"
                  % (c["scenario"], cond["sched"],
                     f(sp, "median"), f(sp, "spread"),
                     f(s["start_p99"], "median"), f(s["start_p99"], "spread"),
                     f(rsp, "median"), s["miss_total"]), file=sys.stderr)
    print("\n结果：%s" % os.path.join(a.out, "baseline.json"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
