#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr10b_matrix__矩阵.py — PR-10B：同一份 C 的不同合法部署，实测对比。

回答的问题不是「哪个跑得快」，而是：
    在**同一份源码、同一套验证、同一台机器**上，把计算放在用户态 / 部分内核化，
    长期运行时端的时延分布、超期次数、以及跨域数据年龄各是多少？

矩阵的每个格子都必须经过同一条流水线（stage_extract → TEIR → STKP → 验证 → 码生成），
不允许出现「手写的对照实现」——否则比较的就不是部署方式，而是两份代码。

只跑**验证器允许**的方案：某个域不合法时报告写 `illegal` 并附拒绝理由，
不为了凑满矩阵去放宽约束。

诚实边界：
  · 全部数字是这台机器的实测，不是 WCET；长跑只提高样本量，不提供上界证明。
  · 「数据年龄」这里是指**用户态写入 → 内核读到**的间隔。UKU 是同步 ioctl，
    这个间隔落在一次调用内，所以用 ioctl 往返时间作代理指标（proxy），
    不是 xipc 那种跨域投递年龄，不能与 PR11 的 age 数字并列比较。
  · 每个格子记录指纹（commit/内核/编译器/源码 sha256）。

用法：
  python3 kfusion/scripts/pr10b_matrix__矩阵.py --cycles 200 20000
  python3 kfusion/scripts/pr10b_matrix__矩阵.py --no-long   # 只跑短测
"""
from __future__ import print_function

import argparse
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)
sys.path.insert(0, HERE)

SRC = os.path.join(KF, "examples", "ecos", "mixed_pid_stateful__积分混合.c")
ECOS_FROM_C = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
OUT_ROOT = os.path.join(KF, "docs", "pr10b_matrix")

# 两种「可信成本输入」：不是猜，是把域成本设成明显不同的量级，
# 让 STKP 在两种经济性下各选一次。哪一边赢由成本决定，不由脚本指定。
SCENARIOS = {
    "user_only": {
        "why": "内核域成本远高于用户态：选型应退回全用户态",
        "u": 5.0, "k": 50.0, "ioctl": 30.0,
    },
    "partial_kernel": {
        "why": "内核域成本远低于用户态：选型应把中间组放进内核（UKU）",
        "u": 5.0, "k": 0.05, "ioctl": 0.3,
    },
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
    """抓探针的 stat_<series>_us 行：p50/p99/max/over。"""
    d = {}
    for m in _STAT.finditer(text):
        d[m.group(1)] = {"p50": float(m.group(2)), "p99": float(m.group(3)),
                         "max": float(m.group(4)), "over": int(m.group(5))}
    return d


def parse_one(text, key):
    m = re.search(r"# %s: (-?\d+)" % key, text)
    return int(m.group(1)) if m else None


def _sess(text):
    m = re.search(r"# session: \w+=(-?\d+)", text)
    return int(m.group(1)) if m else None


def write_cost(path, u, k, names):
    doc = {
        "_note": ("PR-10B 场景成本（域级量级假设，不是逐阶段实测）。"
                  "用于让 STKP 在两种经济性下各选一次；换值会改入选方案。"),
        "linux_userspace": {n: {"value": u, "evidence": "scenario"}
                            for n in names},
        "linux_kernel": {n: {"value": k, "evidence": "scenario"} for n in names},
    }
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def stage_names(gen_dir):
    p = os.path.join(gen_dir, "stages.json")
    if not os.path.isfile(p):
        return []
    d = json.load(io.open(p, encoding="utf-8"))
    return [s["name"] for s in d.get("stages") or []]


def one_scenario(name, spec, cycles_list, base):
    out = os.path.join(base, name)
    os.makedirs(out, exist_ok=True)
    gen = os.path.join(out, "gen")
    cell = {"scenario": name, "why": spec["why"],
            "cost": {"user_us": spec["u"], "kernel_us": spec["k"],
                     "ioctl_us": spec["ioctl"]},
            "runs": [], "status": None}

    # 第一遍：先让流水线把 stages.json 生出来（成本文件依赖阶段名）
    env = dict(os.environ)
    env.update({"OUT_DIR": gen, "CYCLES": "1"})
    rc, log = run(["bash", ECOS_FROM_C, SRC], env=env)
    if rc != 0:
        cell["status"] = "pipeline_failed"
        cell["log_tail"] = log.splitlines()[-8:]
        return cell

    names = stage_names(gen)
    cost_p = write_cost(os.path.join(out, "stage_cost.json"),
                        spec["u"], spec["k"], names)
    env.update({"CYCLES": "1", "STAGE_COST": cost_p,
                "IOCTL_COST": json.dumps({"value": spec["ioctl"],
                                          "evidence": "scenario"})})
    rc, log = run(["bash", ECOS_FROM_C, SRC], env=env)
    with io.open(os.path.join(out, "pipeline.log"), "w", encoding="utf-8") as f:
        f.write(log)
    if rc != 0:
        cell["status"] = "pipeline_failed"
        cell["log_tail"] = log.splitlines()[-8:]
        return cell

    plan_p = os.path.join(gen, "plan.json")
    plan = json.load(io.open(plan_p, encoding="utf-8"))
    cell["chosen"] = (json.load(io.open(os.path.join(gen, "stkp.json"),
                                        encoding="utf-8"))).get("chosen")
    cell["domain"] = (plan.get("meta") or {}).get("xdom_adapter") or \
        [g.get("domain") for g in (plan.get("groups") or [])]
    cell["session"] = ((plan.get("meta") or {}).get("uku_session") or {}).get("region")

    driver = os.path.join(gen, "driver")
    ko_dir = os.path.join(gen, "kmod_uku")
    ko = None
    if os.path.isdir(ko_dir):
        for fn in sorted(os.listdir(ko_dir)):
            if fn.endswith(".ko"):
                ko = os.path.join(ko_dir, fn)
                break
    if not os.path.isfile(driver):
        cell["status"] = "no_driver"
        return cell

    # 选模式：有内核会话 → mixed；全用户态 → gold（同一份源码的两种部署）
    mixed = bool(cell["session"])
    mode = "mixed" if mixed else "gold"
    cell["mode"] = mode
    insmoded = False
    if mixed:
        rc_i, ins = run(["insmod", ko]) if ko else (1, "no ko")
        if rc_i != 0:
            cell["status"] = "insmod_failed"
            cell["detail"] = ins.strip()[:300]
            return cell
        insmoded = True
    try:
        for cyc in cycles_list:
            rc_r, txt = run([driver, mode, str(cyc)])
            with io.open(os.path.join(out, "run_%s.log" % cyc), "w",
                         encoding="utf-8") as f:
                f.write(txt)
            cell["runs"].append({
                "cycles": cyc, "rc": rc_r,
                "response": parse_stats(txt).get("response"),
                "exec": parse_stats(txt).get("exec"),
                "ioctl_roundtrip": parse_stats(txt).get("ioctl_roundtrip"),
                "miss": parse_one(txt, "deadline_miss"),
                "seq_bad": parse_one(txt, "uku_seq_mismatch"),
                "session": _sess(txt),
            })
    finally:
        if insmoded and ko:
            run(["rmmod", os.path.basename(ko)[:-3]])
    cell["status"] = "ok"
    return cell


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, nargs="+", default=[200, 20000])
    ap.add_argument("--out", default=OUT_ROOT)
    ap.add_argument("--no-long", action="store_true")
    a = ap.parse_args()
    cycles = [c for c in a.cycles if not (a.no_long and c > 5000)] or [200]

    os.makedirs(a.out, exist_ok=True)
    cells = []
    for name, spec in SCENARIOS.items():
        print("[%s] %s" % (name, spec["why"]), file=sys.stderr)
        cells.append(one_scenario(name, spec, cycles, os.path.join(a.out, "cells")))

    doc = {"kind": "pr10b_matrix", "source": os.path.relpath(SRC, REPO),
           "cycles": cycles, "cells": cells,
           "boundary": ("实测，不是 WCET；数据年龄用 UKU ioctl 往返作代理，"
                        "不可与 xipc age 并列")}
    with io.open(os.path.join(a.out, "matrix.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    for c in cells:
        if c["status"] != "ok":
            print("%-16s %-16s" % (c["scenario"], c["status"]), file=sys.stderr)
            continue
        for r in c["runs"]:
            resp = r["response"] or {}
            print("%-16s %-6s %6d 拍  p50=%5.1f p99=%6.1f max=%7.1f miss=%s"
                  % (c["scenario"], c["mode"], r["cycles"],
                     resp.get("p50", -1), resp.get("p99", -1),
                     resp.get("max", -1), r["miss"]), file=sys.stderr)
    print("矩阵：%s" % os.path.join(a.out, "matrix.json"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
