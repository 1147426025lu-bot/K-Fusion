#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan_enrich__计划补全.py — U-2：给已验证入选 plan 补 state_ownership / handoffs / runtime。

不改分组与相位；只把 TEIR 里已经知道的「谁写跨拍状态、跨域边传什么」
写成 plan 顶层字段，让后端与宿主按同一份计划生成交接，而不是各自猜。

域名统一成 user / kernel（与架构方案示例一致）；plan.groups[].domain
仍保留 linux_userspace / linux_kernel / mixed 原值，供现有门禁使用。
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys


def _strip_at(name):
    if not name:
        return name
    return name[1:] if name.startswith("@") else name


def _short_domain(d):
    if d == "linux_kernel":
        return "kernel"
    if d in ("linux_userspace", "mixed"):
        return "user"
    return d or "user"


def stage_domain(plan):
    """阶段名 → 短域名（user/kernel）。未入任何组则不算。"""
    m = {}
    for g in plan.get("groups") or []:
        dom = _short_domain(g.get("domain"))
        for s in g.get("members") or g.get("stages") or []:
            m[s] = dom
    return m


def enrich(plan, teir):
    sdom = stage_domain(plan)
    ownership = {}
    for p in teir.get("persistent_state") or []:
        region = _strip_at(p.get("region") or "")
        writer = p.get("writer")
        if not region or not writer:
            continue
        ownership[region] = sdom.get(writer, "user")

    # 同拍数据边：两端域不同 → handoff
    handoffs = []
    seen = set()
    for e in teir.get("edges") or []:
        if e.get("kind") not in (None, "data"):
            # 允许缺省 kind；非 data 跳过
            if e.get("kind") and e.get("kind") != "data":
                continue
        frm, to = e.get("from"), e.get("to")
        if not frm or not to:
            continue
        d0, d1 = sdom.get(frm), sdom.get(to)
        if not d0 or not d1 or d0 == d1:
            continue
        fields = [_strip_at(r) for r in (e.get("regions") or []) if r]
        key = (d0, d1, tuple(fields))
        if key in seen:
            continue
        seen.add(key)
        method = "ioctl" if {d0, d1} == {"user", "kernel"} else "local"
        handoffs.append({
            "from": d0, "to": d1, "fields": fields, "method": method,
            "edge": "%s→%s" % (frm, to),
            "version": e.get("version"),
        })

    # 释放责任：有 linux_kernel 组 → 内核 hrtimer；纯用户态 → 用户驱动；
    # mixed → 用户驱动负责周期，内核只应答 ioctl。
    domains = sorted({g.get("domain") for g in (plan.get("groups") or [])})
    if domains == ["linux_kernel"]:
        release_owner = "kernel_hrtimer"
    elif "linux_kernel" in domains or "mixed" in domains:
        release_owner = "userspace_driver"
    else:
        release_owner = "userspace_driver"

    meta = plan.get("meta") or {}
    runtime = plan.get("runtime") or {}
    runtime.setdefault("cpu", meta.get("pin_cpu", 3))
    runtime.setdefault("policy", meta.get("sched_policy", "SCHED_FIFO"))
    runtime.setdefault("priority", meta.get("rt_priority", 80))
    runtime["release_owner"] = release_owner

    plan["state_ownership"] = ownership
    plan["handoffs"] = handoffs
    plan["runtime"] = runtime
    plan.setdefault("meta", meta)["enriched"] = {
        "by": "plan_enrich__计划补全.py",
        "version": 1,
        "n_owned": len(ownership),
        "n_handoffs": len(handoffs),
    }
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--teir", required=True)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    with io.open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    with io.open(a.teir, encoding="utf-8") as f:
        teir = json.load(f)
    enrich(plan, teir)
    out = os.path.abspath(a.out)
    parent = os.path.dirname(out)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("plan enrich: ownership=%d handoffs=%d release=%s → %s"
          % (len(plan["state_ownership"]), len(plan["handoffs"]),
             plan["runtime"]["release_owner"], out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
