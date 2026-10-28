#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr10b_matrix__矩阵.py — PR-10B v2：加压 + 重复实验 + 统计口径。

v1 的教训：1 ms 周期对这个负载太宽松，两种部署都是 0 超期，端到端差异被淹没，
而且每个格子只跑一次，尾部指标方向自相矛盾。v2 因此改三件事：

  1. **加压**：把周期压到 deadline 真会被触碰（1000 → 400 µs），并可选加背景
     CPU 负载；周期/相位从源码注释按比例改写（生成变体源并记 sha256，
     不是手写第二份程序）。
  2. **重复**：每个条件跑 N 轮，报**每轮数值 + 中位数 + 最差**，
     不用单次数字当结论。
  3. **统计**：同条件同轮次做配对差值（内核化 − 用户态），报差值中位数与
     方向一致率；**不做**显著性声明——重复运行来自同一台机器，独立性本来就弱。

只跑验证器允许的部署；选型仍由成本输入决定，脚本不指定部署。

用法：
  python3 kfusion/scripts/pr10b_matrix__矩阵.py --repeat 5 --cycles 5000
  python3 kfusion/scripts/pr10b_matrix__矩阵.py --no-load
"""
from __future__ import print_function

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)

SRC = os.path.join(KF, "examples", "ecos", "mixed_pid_stateful__积分混合.c")
ECOS_FROM_C = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
OUT_ROOT = os.path.join(KF, "docs", "pr10b_matrix")

SCENARIOS = {
    "user_only": {"why": "内核域远贵于用户态 → 全用户态", "u": 5.0, "k": 50.0,
                  "ioctl": 30.0},
    "partial_kernel": {"why": "内核域远便宜 → 中间组进内核（UKU）", "u": 5.0,
                       "k": 0.05, "ioctl": 0.3},
}

_STAT = re.compile(
    r"# stat_([A-Za-z0-9_]+)_us: .*?p50=(\S+) .*?p99=(\S+) .*?max=(\S+).*?over=(\d+)")


def run(cmd, env=None):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=env)
    out = p.communicate()[0]
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return p.returncode, out


def parse_stats(text):
    d = {}
    for m in _STAT.finditer(text):
        d[m.group(1)] = {"p50": float(m.group(2)), "p99": float(m.group(3)),
                         "max": float(m.group(4)), "over": int(m.group(5))}
    return d


def parse_one(text, key):
    m = re.search(r"# %s: (-?\d+)" % key, text)
    return int(m.group(1)) if m else None


def variant_source(dst_dir, period_us):
    """按比例改写周期与相位，生成变体源（同一份程序，只改时基）。

    相位锚点保持在周期内：sample@0 / pid@0.25P / sink@0.5P。
    """
    os.makedirs(dst_dir, exist_ok=True)
    text = io.open(SRC, encoding="utf-8").read()
    text = re.sub(r"period_us=\d+", "period_us=%d" % period_us, text)
    text = re.sub(r"deadline_us=\d+", "deadline_us=%d" % period_us, text)
    ph = [0, int(period_us * 0.25), int(period_us * 0.5)]
    text = re.sub(r"\* sample@\d+ pid@\d+ sink@\d+",
                  "* sample@%d pid@%d sink@%d" % (ph[0], ph[1], ph[2]), text)
    p = os.path.join(dst_dir, "src_p%d.c" % period_us)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p, hashlib.sha256(text.encode("utf-8")).hexdigest()


def start_load(n):
    """背景负载：n 个忙循环（不依赖 stress-ng）。"""
    procs = []
    for _ in range(n):
        procs.append(subprocess.Popen(
            ["bash", "-c", "while :; do :; done"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return procs


def stop_load(procs):
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=5)
        except Exception:
            pass


def write_cost(path, u, k, names):
    doc = {
        "_note": ("PR-10B 场景成本（域级量级假设，不是逐阶段实测）。"
                  "用于让 STKP 在两种经济性下各选一次；换值会改入选方案。"),
        "linux_userspace": {n: {"value": u, "evidence": "scenario"} for n in names},
        "linux_kernel": {n: {"value": k, "evidence": "scenario"} for n in names},
    }
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def build_cell(scn_name, spec, src_p, period_us, base):
    """每个 (部署, 周期) 只构建一次；重复运行复用同一份产物。"""
    out = os.path.join(base, "%s_p%d" % (scn_name, period_us))
    os.makedirs(out, exist_ok=True)
    gen = os.path.join(out, "gen")
    if os.path.isdir(gen):
        shutil.rmtree(gen)

    env = dict(os.environ)
    env.update({"OUT_DIR": gen, "CYCLES": "1"})
    rc, log = run(["bash", ECOS_FROM_C, src_p], env=env)
    if rc != 0:
        return {"status": "pipeline_failed", "log_tail": log.splitlines()[-6:]}

    p = os.path.join(gen, "stages.json")
    names = []
    if os.path.isfile(p):
        names = [s["name"] for s in
                 (json.load(io.open(p, encoding="utf-8")).get("stages") or [])]
    cost_p = write_cost(os.path.join(out, "stage_cost.json"),
                        spec["u"], spec["k"], names)
    env.update({"STAGE_COST": cost_p,
                "IOCTL_COST": json.dumps({"value": spec["ioctl"],
                                          "evidence": "scenario"})})
    rc, log = run(["bash", ECOS_FROM_C, src_p], env=env)
    with io.open(os.path.join(out, "pipeline.log"), "w", encoding="utf-8") as f:
        f.write(log)
    if rc != 0:
        return {"status": "pipeline_failed", "log_tail": log.splitlines()[-6:]}

    plan = json.load(io.open(os.path.join(gen, "plan.json"), encoding="utf-8"))
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
    sess = ((plan.get("meta") or {}).get("uku_session") or {}).get("region")
    return {"status": "ok", "dir": out, "driver": driver, "ko": ko,
            "session": sess, "mode": "mixed" if sess else "gold",
            "chosen": (json.load(io.open(os.path.join(gen, "stkp.json"),
                                         encoding="utf-8"))).get("chosen")}


def measure(cell, cycles, repeat, load_n, out_dir):
    runs = []
    for i in range(repeat):
        procs = start_load(load_n) if load_n else []
        time.sleep(0.2)
        insmoded = False
        if cell["mode"] == "mixed" and cell.get("ko"):
            rc_i, _ = run(["insmod", cell["ko"]])
            insmoded = (rc_i == 0)
            if not insmoded:
                stop_load(procs)
                return {"status": "insmod_failed", "runs": runs}
        try:
            rc, txt = run([cell["driver"], cell["mode"], str(cycles)])
        finally:
            if insmoded:
                run(["rmmod", os.path.basename(cell["ko"])[:-3]])
            stop_load(procs)
        with io.open(os.path.join(out_dir, "r%d.log" % i), "w",
                     encoding="utf-8") as f:
            f.write(txt)
        st = parse_stats(txt)
        runs.append({
            "repeat": i, "cycles": cycles, "rc": rc,
            "response": st.get("response"), "exec": st.get("exec"),
            "ioctl": st.get("ioctl_roundtrip"),
            "miss": parse_one(txt, "deadline_miss"),
            "seq_bad": parse_one(txt, "uku_seq_mismatch"),
        })
    return {"status": "ok", "runs": runs}


def agg(runs, key):
    def col(k):
        out = [(r[key] or {}).get(k) for r in runs if r.get(key)]
        return [v for v in out if v is not None]
    p50, p99, mx = col("p50"), col("p99"), col("max")
    if not p50:
        return None
    return {"p50_med": statistics.median(p50), "p50_min": min(p50),
            "p50_max": max(p50),
            "p99_med": statistics.median(p99) if p99 else None,
            "max_med": statistics.median(mx) if mx else None,
            "max_worst": max(mx) if mx else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=5000)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--periods", type=int, nargs="+", default=[1000, 400])
    ap.add_argument("--loads", type=int, nargs="+", default=[0, 4])
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--out", default=OUT_ROOT)
    a = ap.parse_args()
    loads = [0] if a.no_load else a.loads

    base = os.path.join(a.out, "v2")
    os.makedirs(base, exist_ok=True)
    src_dir = os.path.join(base, "src")

    cells = []
    for period in a.periods:
        src_p, sha = variant_source(src_dir, period)
        for scn, spec in SCENARIOS.items():
            print("[build] %s period=%d" % (scn, period), file=sys.stderr)
            built = build_cell(scn, spec, src_p, period, base)
            entry = {"scenario": scn, "why": spec["why"], "period_us": period,
                     "src_sha256": sha, "cost": spec, "status": built["status"]}
            if built["status"] != "ok":
                entry["log_tail"] = built.get("log_tail")
                cells.append(entry)
                continue
            entry.update({k: built[k] for k in
                          ("chosen", "mode", "session", "dir")})
            entry["conditions"] = []
            for load in loads:
                cdir = os.path.join(built["dir"], "load%d" % load)
                os.makedirs(cdir, exist_ok=True)
                print("  [run] load=%d repeat=%d cycles=%d"
                      % (load, a.repeat, a.cycles), file=sys.stderr)
                res = measure(built, a.cycles, a.repeat, load, cdir)
                cond = {"load": load, "cycles": a.cycles,
                        "status": res["status"], "runs": res["runs"],
                        "response": agg(res["runs"], "response"),
                        "ioctl": agg(res["runs"], "ioctl"),
                        "miss_total": sum(r["miss"] or 0 for r in res["runs"])}
                entry["conditions"].append(cond)
            cells.append(entry)

    # 配对比较：同周期同负载，逐轮算 (内核化 − 用户态) 的 p50 差
    compare = []
    for period in a.periods:
        for load in loads:
            u = next((c for c in cells if c["scenario"] == "user_only"
                      and c["period_us"] == period), None)
            m = next((c for c in cells if c["scenario"] == "partial_kernel"
                      and c["period_us"] == period), None)
            if not u or not m or u["status"] != "ok" or m["status"] != "ok":
                compare.append({"period_us": period, "load": load,
                                "note": "某侧不可用（未入选/不合法）"})
                continue
            uc = next(c for c in u["conditions"] if c["load"] == load)
            mc = next(c for c in m["conditions"] if c["load"] == load)
            diffs = []
            for ru, rm in zip(uc["runs"], mc["runs"]):
                x = (ru.get("response") or {}).get("p50")
                y = (rm.get("response") or {}).get("p50")
                if x is not None and y is not None:
                    diffs.append(y - x)      # 正 = 内核化更慢
            compare.append({
                "period_us": period, "load": load,
                "p50_diff_med": statistics.median(diffs) if diffs else None,
                "p50_diff_min": min(diffs) if diffs else None,
                "p50_diff_max": max(diffs) if diffs else None,
                "all_positive": all(d > 0 for d in diffs) if diffs else None,
                "n": len(diffs),
                "miss_user": uc["miss_total"], "miss_partial": mc["miss_total"],
            })

    doc = {"kind": "pr10b_matrix_v2", "cycles": a.cycles, "repeat": a.repeat,
           "periods": a.periods, "loads": loads, "cells": cells,
           "paired_p50": compare,
           "boundary": ("实测不是 WCET；重复运行来自同一台机器，独立性弱，"
                        "只报差值与方向一致率，不报显著性；"
                        "数据年龄用 ioctl 往返代理")}
    with io.open(os.path.join(a.out, "matrix_v2.json"), "w",
                 encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("\n%-16s %6s %5s %8s %8s %8s %8s %s"
          % ("cell", "period", "load", "p50med", "p99med", "maxmed",
             "maxworst", "miss"), file=sys.stderr)
    for c in cells:
        if c["status"] != "ok":
            print("%-16s %6d %5s  %s"
                  % (c["scenario"], c["period_us"], "-", c["status"]),
                  file=sys.stderr)
            continue
        for cond in c["conditions"]:
            r = cond["response"] or {}
            print("%-16s %6d %5d %8s %8s %8s %8s %s"
                  % (c["scenario"], c["period_us"], cond["load"],
                     r.get("p50_med"), r.get("p99_med"), r.get("max_med"),
                     r.get("max_worst"), cond["miss_total"]), file=sys.stderr)
    print("\n配对差值：")
    for p in compare:
        if p.get("note"):
            print("  period=%s load=%s  %s" % (p["period_us"], p["load"],
                                               p["note"]), file=sys.stderr)
        else:
            print("  period=%s load=%s  中位差 %.1f µs（全正=%s, n=%d）miss %s/%s"
                  % (p["period_us"], p["load"], p["p50_diff_med"],
                     p["all_positive"], p["n"], p["miss_user"],
                     p["miss_partial"]), file=sys.stderr)
    print("\n结果：%s" % os.path.join(a.out, "matrix_v2.json"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
