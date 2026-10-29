#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-17：UKU 生成器能力声明。规划与生成共用，禁止「选了却发不出」。

范围刻意收窄：同步 ioctl、至多一个内核组。多释放只保证已声明的 U–K–U
三组拓扑。其它混合拓扑可以枚举，但不得 deployment_ready。
"""
from __future__ import print_function

CAPS = {
    "version": 1,
    "kind": "uku_backend_capabilities",
    "adapter": "uku_ioctl",
    "abi": "kfusion_uku_v1",
    "modes": ["gold", "mixed", "plan", "plan_user"],
    "timebase": "CLOCK_MONOTONIC TIMER_ABSTIME",
    "uku_single_release": {
        "supported": True,
        "max_kernel_groups": 1,
        "topology": "U-K-U",
        "n_groups": 3,
        "note": "一次用户线程唤醒，连续执行；driver 的 gold|mixed",
    },
    "uku_multi_release": {
        "supported": True,
        "max_kernel_groups": 1,
        "topology": "U-K-U",
        "n_groups": 3,
        "n_releases": 3,
        "sync": "ioctl",
        "note": "t_{k,g}=t0+kP+φ_g；不得相对睡 200µs；driver 的 plan|plan_user",
    },
}


def unique_phases(plan):
    phases = []
    for g in plan.get("groups") or []:
        ph = g.get("release_phase_us")
        if ph is None:
            ph = g.get("phase_us")
        phases.append(round(float(ph or 0.0), 3))
    if not phases:
        return []
    seen = []
    for p in phases:
        if p not in seen:
            seen.append(p)
    return seen


def uku_release_kind(plan):
    """单释放 vs 多释放：看组释放相位去重个数，不看组数。"""
    n = len(unique_phases(plan))
    if n <= 1:
        return "uku_single_release"
    return "uku_multi_release"


def topology(plan):
    groups = plan.get("groups") or []
    doms = [g.get("domain") for g in groups]
    n_k = sum(1 for d in doms if d == "linux_kernel")
    return {
        "n_groups": len(groups),
        "n_kernel_groups": n_k,
        "domains": list(doms),
        "n_releases": len(unique_phases(plan)),
        "uku": (len(groups) == 3
                and doms == ["linux_userspace", "linux_kernel",
                             "linux_userspace"]
                and n_k == 1),
    }


def check_plan(plan):
    """生成器能否执行这份 UKU plan。不通过则不得 deployment_ready。"""
    meta = plan.get("meta") or {}
    if meta.get("xdom_adapter") != "uku_ioctl":
        return {"ok": True, "kind": None, "codes": [], "errors": []}
    top = topology(plan)
    kind = uku_release_kind(plan)
    errors = []
    if not top["uku"]:
        errors.append({
            "code": "E_UKU_PLAN_TOPOLOGY",
            "msg": ("UKU 生成器只支持 U–K–U、单内核组；当前 groups=%s kernel=%d"
                    % (top["domains"], top["n_kernel_groups"])),
        })
    spec = CAPS.get(kind) or {}
    if not spec.get("supported"):
        errors.append({
            "code": "E_UKU_PLAN_UNSUPPORTED",
            "msg": "生成器未声明 %s" % kind,
        })
    elif kind == "uku_multi_release" and top["n_groups"] != spec.get("n_groups"):
        errors.append({
            "code": "E_UKU_PLAN_SHAPE",
            "msg": "多释放 UKU 只支持 %s 组，当前 %d"
                   % (spec.get("n_groups"), top["n_groups"]),
        })
    return {
        "ok": not errors,
        "kind": kind,
        "topology": top,
        "codes": [e["code"] for e in errors],
        "errors": errors,
        "capabilities": CAPS,
    }
