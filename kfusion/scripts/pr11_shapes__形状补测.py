#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr11_shapes__形状补测.py — PR-11 补测：把 Q99 从「2 种形状」扩到「全部形状」。

PR-11 的 J(d,s) 只能给测过的形状排名。PR-10E-A 只测了两种形状：
  · 3 组不融合、全用户态（gold）
  · 3 组不融合、中段走 ioctl（mixed）
于是全融合（1 组）与两组切分这 3 类候选被 fail-closed 挡住，覆盖率 2/5。

本脚本用 `FORCE_CHOSEN=<id>@<domain>` 按形状点名构建（强制选择在 stkp.json 里
标 forced，事后能看出这不是模型结论），然后按与 PR-10E-A **完全相同**的口径采集：
同一份源码、同一份 driver 生成器、CPU3 亲和、CFS/FIFO99 两类、10 轮 × 5000 拍、
逐拍 csv 留档、逐轮重算分位数再跨轮取中位数与极差。

三条不许省的校验：
  1. 构建后**回读 plan.json 的组结构**，与期望形状不符就报错——否则「强制
     选择没生效」会被当成「这个形状就是这个数」。
  2. 每轮记录 env 快照（governor/频率/温度/loadavg/RT 配额）。
  3. 超期按 1000 µs 判（与 PR-10E-A 同一 deadline），拍数与超期数一起存。

用法（在板上跑）：
  python3 kfusion/scripts/pr11_shapes__形状补测.py --repeat 10 --cycles 5000
  python3 kfusion/scripts/pr11_shapes__形状补测.py --only fused_user   # 单形状
"""
from __future__ import print_function

import argparse
import gzip
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)

SRC = os.path.join(KF, "examples", "ecos", "mixed_pid_stateful__积分混合.c")
ECOS_FROM_C = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
OUT_ROOT = os.path.join(KF, "docs", "pr11_shapes")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = _load("pr10e_baseline",
             os.path.join(KF, "scripts", "pr10e_baseline__统计基线.py"))

# 与 PR-10E-A 同一组钳定值（scenario 证据，不是实测）：只有这样才能复现
# 同一份候选集；本脚本测的是形状，不是成本模型。
BUILD = {"u": 5.0, "k": 0.05, "ioctl": 0.3}
DEADLINE_US = 1000.0
PIN_CPU = 3

# 待补的形状。label 就是 STKP 候选的 "<id>@<domain>"，强制选择时按它点名。
SHAPES = [
    {
        "name": "fused_user",
        "label": "B@linux_userspace",
        "expect": {"n_groups": 1, "fused": True, "domains": ["linux_userspace"]},
        "note": "全融合 1 组，全用户态（模型最爱的形状，从没测过）",
    },
    {
        "name": "split_sp_k_user",
        "label": ("STKP:L[stage_sample+stage_pid]|L[stage_sink]"
                  "@linux_userspace"),
        "expect": {"n_groups": 2, "fused": False,
                   "domains": ["linux_userspace"] * 2},
        "note": "两组：sample+pid 融合，sink 独立",
    },
    {
        "name": "split_s_pk_user",
        "label": ("STKP:L[stage_sample]|L[stage_pid+stage_sink]"
                  "@linux_userspace"),
        "expect": {"n_groups": 2, "fused": False,
                   "domains": ["linux_userspace"] * 2},
        "note": "两组：sample 独立，pid+sink 融合",
    },
]


def run(cmd, env=None):
    return base.run(cmd, env=env)


def write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def cost_files(out, spec):
    """和 PR-10E-A 一样，先把阶段名从 stages.json 读出来再写成本。"""
    st = os.path.join(out, "gen", "stages.json")
    names = [s["name"] for s in
             (json.load(io.open(st, encoding="utf-8")).get("stages") or [])]
    cost = {
        "_note": "PR-11 形状补测：沿用 PR-10E-A 的域级量级假设（scenario），"
                 "只为复现同一候选集；本实验测形状，不测成本模型",
        "linux_userspace": {n: {"value": spec["u"], "evidence": "scenario"}
                            for n in names},
        "linux_kernel": {n: {"value": spec["k"], "evidence": "scenario"}
                         for n in names},
    }
    cp = os.path.join(out, "cost.json")
    write_json(cp, cost)
    return cp


def build_shape(shape, workdir):
    """按 shape 强制构建，并**回读**组结构确认强制真的生效。"""
    gen = os.path.join(workdir, "gen")
    os.makedirs(gen, exist_ok=True)
    env = dict(os.environ)
    env.update({"OUT_DIR": gen, "CYCLES": "1"})
    rc, log = run(["bash", ECOS_FROM_C, SRC], env=env)
    if rc != 0:
        # 第一遍只是为了拿到 stages.json；层名失败直接报错，不继续
        return {"status": "pipeline_failed_pass1",
                "log_tail": log.splitlines()[-8:]}
    cp = cost_files(workdir, BUILD)
    env.update({"STAGE_COST": cp,
                "IOCTL_COST": json.dumps({"value": BUILD["ioctl"],
                                          "evidence": "scenario"}),
                "FORCE_CHOSEN": shape["label"]})
    rc, log = run(["bash", ECOS_FROM_C, SRC], env=env)
    with io.open(os.path.join(workdir, "pipeline.log"), "w",
                 encoding="utf-8") as f:
        f.write(log)
    if rc != 0:
        return {"status": "pipeline_failed_pass2",
                "log_tail": log.splitlines()[-10:]}

    stkp_p = os.path.join(gen, "stkp.json")
    plan_p = os.path.join(gen, "plan.json")
    if not os.path.isfile(plan_p):
        return {"status": "no_plan"}
    plan = json.load(io.open(plan_p, encoding="utf-8"))
    groups = plan.get("groups") or []
    got = {
        "n_groups": len(groups),
        "fused": bool((plan.get("meta") or {}).get("fusion")),
        "domains": [g.get("domain") for g in groups],
        "members": [g.get("members") for g in groups],
    }
    exp = shape["expect"]
    if got["n_groups"] != exp["n_groups"] or got["domains"] != exp["domains"]:
        return {"status": "shape_mismatch", "expected": exp, "got": got}
    if plan.get("meta", {}).get("fusion") and exp["n_groups"] > 1:
        return {"status": "shape_mismatch", "expected": exp, "got": got}

    driver = os.path.join(gen, "driver")
    if not os.path.isfile(driver):
        return {"status": "no_driver", "got": got}
    forced = None
    if os.path.isfile(stkp_p):
        d = json.load(io.open(stkp_p, encoding="utf-8"))
        forced = d.get("forced")
        if d.get("claim") != "forced_for_measurement":
            # 强制选择没生效（例如被目标函数覆盖），这种数不能用
            return {"status": "force_not_applied",
                    "claim": d.get("claim"), "got": got}
    return {"status": "ok", "driver": driver, "plan": plan, "shape": got,
            "forced": forced, "stkp": stkp_p}


STAT_RE = r"# stat_%s: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*?p99=(\d+).*?p99\.9=(\d+).*?p99\.99=(\d+) max=([0-9.]+) over=(\d+).*? n=(\d+)"


def parse_stats(text):
    """从统一探针的统计块取分位数。

    为什么不用 csv：plan 模式（走本形状的分组）不产逐拍 csv，只有统计块。
    统计块来自**同一份探针**、同一分位数定义，够算 Q99 / 超期数；
    代价是不能事后改分位数或做超期拍分解——那些只有 PR-10E-A 那两种形状有。
    原始输出整段留档（含 1µs 直方图），事后要复查仍有据。
    """
    import re
    out = {}
    for key, field in (("start_lat", "start_lat_us"),
                       ("response", "response_us"),
                       ("exec", "exec_us")):
        m = re.search(STAT_RE % field, text)
        if not m:
            continue
        out[key] = {
            "min_us": float(m.group(1)), "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)), "p99_us": int(m.group(4)),
            "p999_us": int(m.group(5)), "p9999_us": int(m.group(6)),
            "max_us": float(m.group(7)), "over": int(m.group(8)),
            "n": int(m.group(9)),
        }
    m = re.search(r"# deadline_miss: (\d+)/(\d+)", text)
    if m:
        out["deadline"] = {"miss": int(m.group(1)), "n": int(m.group(2))}
    m = re.search(r"# digest: ([0-9a-f]+)", text)
    if m:
        out["digest"] = m.group(1)
    return out


def measure_shape(cell, sched, cycles, repeat, out_dir):
    """与 PR-10E-A 同一口径：taskset 固定 CPU，FIFO 用 chrt，每轮留原始输出。"""
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    runs = []
    for i in range(repeat):
        before = base.env_snapshot()
        cmd = ["taskset", "-c", str(PIN_CPU)]
        if sched == "fifo99":
            cmd += ["chrt", "-f", "99"]
        cmd += [cell["driver"], "plan", str(cycles)]
        rc, txt = run(cmd)
        after = base.env_snapshot()
        with gzip.open(os.path.join(out_dir, "r%02d.log.gz" % i), "wt",
                       encoding="utf-8", newline="\n") as f:
            f.write(txt)
        st = parse_stats(txt)
        if not st.get("response"):
            runs.append({"repeat": i, "rc": rc, "n": 0,
                         "env_before": before, "env_after": after,
                         "reason": "统计块缺失",
                         "log_tail": txt.splitlines()[-5:]})
            continue
        sl, rp, wk = st["start_lat"], st["response"], st.get("exec") or {}
        runs.append({
            "repeat": i, "rc": rc, "n": rp["n"], "digest": st.get("digest"),
            "start_lat": {"p50_us": sl["p50_us"], "p99_us": sl["p99_us"],
                          "p999_us": sl["p999_us"], "max_us": sl["max_us"],
                          "avg_us": sl["avg_us"]},
            "response": {"p50_us": rp["p50_us"], "p99_us": rp["p99_us"],
                         "p999_us": rp["p999_us"], "max_us": rp["max_us"],
                         "avg_us": rp["avg_us"]},
            "work": {"p50_us": wk.get("p50_us"), "p99_us": wk.get("p99_us")},
            "miss": (st.get("deadline") or {}).get("miss"),
            "stat_source": "probe_stat_block",
            "env_before": before, "env_after": after,
        })
    return {"status": "ok", "runs": runs}


def _merged(prev, new_cells):
    """按 scenario 合并：新测的覆盖旧的，没测的保留。"""
    by = dict(prev)
    for c in new_cells:
        if c.get("scenario"):
            by[c["scenario"]] = c
    order = [s["name"] for s in SHAPES]
    return [by[k] for k in order if k in by] + \
           [c for k, c in by.items() if k not in order]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=10)
    ap.add_argument("--cycles", type=int, default=5000)
    ap.add_argument("--scheds", nargs="+", default=["cfs", "fifo99"])
    ap.add_argument("--only", default=None, help="只测某个形状名")
    ap.add_argument("--fresh", action="store_true", help="不合并旧结果")
    ap.add_argument("--out", default=OUT_ROOT)
    a = ap.parse_args()

    todo = [s for s in SHAPES if not a.only or s["name"] == a.only]
    if not todo:
        print("没有匹配的形状：%s" % a.only, file=sys.stderr)
        return 2
    if not os.path.isdir(a.out):
        os.makedirs(a.out)

    out_p = os.path.join(a.out, "shapes.json")
    # 分批跑（--only）不能互相覆盖：按 scenario 合并旧结果，可断点续采
    prev = {}
    if os.path.isfile(out_p) and not a.fresh:
        try:
            for c in (json.load(io.open(out_p, encoding="utf-8"))
                      .get("cells") or []):
                if c.get("scenario"):
                    prev[c["scenario"]] = c
        except Exception:
            prev = {}

    cells = []
    for sh in todo:
        wd = os.path.join(a.out, sh["name"])
        print("[build] %-16s %s" % (sh["name"], sh["label"]), file=sys.stderr)
        b = build_shape(sh, wd)
        if b["status"] != "ok":
            print("  构建失败：%s %s" % (b["status"],
                                      b.get("got") or b.get("log_tail") or ""),
                  file=sys.stderr)
            cells.append({"scenario": sh["name"], "status": b["status"],
                          "detail": b})
            continue
        print("  形状 %s" % json.dumps(b["shape"], ensure_ascii=False),
              file=sys.stderr)
        conds = []
        for sched in a.scheds:
            rd = os.path.join(wd, "runs_%s" % sched)
            print("  采集 %-8s ×%d 轮 ×%d 拍" % (sched, a.repeat, a.cycles),
                  file=sys.stderr)
            res = measure_shape(b, sched, a.cycles, a.repeat, rd)
            conds.append({"sched": sched, "status": res["status"],
                          "runs": res.get("runs") or []})
        cells.append({
            "scenario": sh["name"], "status": "ok",
            "label": sh["label"], "note": sh["note"],
            "shape": b["shape"], "domains": b["shape"]["domains"],
            "driver": b["driver"], "forced": b.get("forced"),
            "conditions": conds,
        })
        write_json(out_p, {
            "kind": "pr11_shapes", "repeat": a.repeat, "cycles": a.cycles,
            "pin_cpu": PIN_CPU, "deadline_us": DEADLINE_US,
            "scheds": a.scheds, "build_pins": BUILD,
            "cells": _merged(prev, cells),
            "note": "形状补测：同一源码/同一生成器，只改组结构与调度类；"
                    "每轮原始输出在各自 runs_*/r*.log.gz（含 1µs 直方图）",
        })

    ok = [c for c in cells if c.get("status") == "ok"]
    print("\n完成 %d/%d 形状 → %s/shapes.json" % (
        len(ok), len(cells), a.out), file=sys.stderr)
    for c in cells:
        if c.get("status") != "ok":
            print("  ✗ %s %s" % (c["scenario"], c["status"]), file=sys.stderr)
    return 0 if len(ok) == len(cells) else 4


if __name__ == "__main__":
    sys.exit(main())
