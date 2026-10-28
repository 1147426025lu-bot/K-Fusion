#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candidate_generate.py — A–F 固定方案对照基线（不驱动代码生成）。

正式求解入口是 ecos_plan_joint.py（STKP v1）。本文件只枚举预设 A–F，供论文比较。

只比较四个方案，把「内核化」和「融合」拆开（方案原文）：

  A  用户态实时线程 / 不融合
  B  用户态实时线程 / 融合
  C  Linux 实时内核线程 / 不融合
  D  Linux 实时内核线程 / 融合

合法性交给独立验证器（plan_verify），本文件只构造候选并算成本。
内核没有收益时必须选用户态——这是原则 1，不是调参。

成本（离线经验模型，不是 WCET）：

  Cost = G · c_domain + E + extra_domain
  Benefit vs A = Cost(A) − Cost(p)

  c_user / c_kernel  每次释放的唤醒代价（µs，实测中位，默认 21 / 21）
  extra_kernel       进入内核的附加运行时税（µs/周期；默认 0）
  E                  计算体执行时间之和（缺省 0，不影响选型相对序若只比释放）

用法：
  python candidate_generate.py --teir task.teir.json -o candidates.json
  python candidate_generate.py --teir t.json --kernel-extra-us 80   # 反例：不内核化更优
"""
from __future__ import print_function

import argparse
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))


def load_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_verifier():
    path = os.path.join(KF, "verify", "plan_verify.py")
    spec = importlib.util.spec_from_file_location("plan_verify", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stage_order(teir):
    nodes = list(teir.get("nodes") or [])
    nodes.sort(key=lambda n: (
        n.get("phase_us") is None,
        float(n.get("phase_us") or 0),
        n["name"],
    ))
    return [n["name"] for n in nodes]


def teir_node(teir, name):
    for n in teir.get("nodes") or []:
        if n["name"] == name:
            return n
    return {}


def can_kernel(teir, name):
    return "linux_kernel" in (teir_node(teir, name).get("allowed_domains") or [])


def make_plan(order, fused, platform):
    if fused:
        groups = [{"id": "g0", "members": list(order)}]
    else:
        groups = [{"id": "g%d" % i, "members": [n]}
                  for i, n in enumerate(order)]
    return {
        "version": 2,
        "meta": {
            "platform": platform,
            "fusion": bool(fused),
            "generator": "candidate_generate A/B/C/D",
        },
        "order": list(order),
        "groups": groups,
    }


LABELS = {
    ("linux_userspace", False): "A",
    ("linux_userspace", True): "B",
    ("linux_kernel", False): "C",
    ("linux_kernel", True): "D",
}


def make_mixed_plan(order, teir, fuse_within=True):
    """MMIO 等不能进内核的阶段留用户态；可内核化的进 linux_kernel。组不跨域。"""
    domains = {}
    groups = []
    cur, cur_dom = [], None

    def flush():
        nonlocal cur, cur_dom
        if cur:
            groups.append({
                "id": "g%d" % len(groups),
                "members": list(cur),
                "domain": cur_dom,
            })
            cur, cur_dom = [], None

    for name in order:
        d = "linux_kernel" if can_kernel(teir, name) else "linux_userspace"
        domains[name] = d
        if not fuse_within:
            groups.append({
                "id": "g%d" % len(groups),
                "members": [name],
                "domain": d,
            })
            continue
        if cur_dom is None or d == cur_dom:
            cur.append(name)
            cur_dom = d
        else:
            flush()
            cur, cur_dom = [name], d
    if fuse_within:
        flush()
    return {
        "version": 2,
        "meta": {
            "platform": "mixed",
            "fusion": bool(fuse_within),
            "generator": "candidate_generate E/F partial kernelize",
        },
        "order": list(order),
        "groups": groups,
        "domains": domains,
    }


def cost_of(n_stages, fused, domain, c_user, c_kernel, extra_kernel, exec_us):
    g = 1 if fused else n_stages
    if domain == "linux_kernel":
        c = c_kernel
        extra = extra_kernel
    else:
        c = c_user
        extra = 0.0
    release = g * c
    total = release + exec_us + extra
    return {
        "groups": g,
        "c_us": c,
        "release_us": round(release, 3),
        "exec_us": round(exec_us, 3),
        "extra_us": round(extra, 3),
        "total_us": round(total, 3),
        "saved_release_vs_A_us": round(n_stages * c_user - release, 3),
        "added_runtime_us": round(extra, 3),
        "saved_boundary_us": 0.0,
        "benefit_vs_A_us": None,  # filled after Cost(A) known
    }


def cost_mixed(plan, teir, c_user, c_kernel, extra_kernel, exec_us, boundary_us):
    release = 0.0
    has_k = False
    for g in plan.get("groups") or []:
        d = g.get("domain") or (plan.get("domains") or {}).get(
            (g.get("members") or [None])[0])
        if d == "linux_kernel":
            release += c_kernel
            has_k = True
        else:
            release += c_user
    n_b = 0
    doms = plan.get("domains") or {}
    for e in teir.get("edges") or []:
        if e.get("kind") not in ("data", "call"):
            continue
        a, b = doms.get(e.get("from")), doms.get(e.get("to"))
        if a and b and a != b:
            n_b += 1
    extra = extra_kernel if has_k else 0.0
    bound = n_b * boundary_us
    total = release + exec_us + extra + bound
    return {
        "groups": len(plan.get("groups") or []),
        "c_us": None,
        "release_us": round(release, 3),
        "exec_us": round(exec_us, 3),
        "extra_us": round(extra, 3),
        "boundary_us": round(bound, 3),
        "cross_domain_edges": n_b,
        "total_us": round(total, 3),
        "saved_release_vs_A_us": None,
        "added_runtime_us": round(extra + bound, 3),
        "saved_boundary_us": 0.0,
        "benefit_vs_A_us": None,
    }


def generate(teir, c_user, c_kernel, extra_kernel, exec_us, verifier,
             boundary_us=50.0):
    order = stage_order(teir)
    n = len(order)
    if n == 0:
        raise SystemExit("TEIR 没有阶段")
    specs = [
        ("A", "linux_userspace", False),
        ("B", "linux_userspace", True),
        ("C", "linux_kernel", False),
        ("D", "linux_kernel", True),
    ]
    cands = []
    for label, domain, fused in specs:
        plan = make_plan(order, fused, domain)
        ok, diags = verifier.verify(teir, plan)
        cost = cost_of(n, fused, domain, c_user, c_kernel, extra_kernel, exec_us)
        rec = {
            "id": label,
            "domain": domain,
            "fused": fused,
            "legal": bool(ok),
            "diagnostics": diags,
            "plan": plan,
            "cost": cost,
            "why": None,
        }
        if not ok:
            rec["why"] = "验证器拒绝: " + "; ".join(
                d["code"] for d in diags[:4])
        cands.append(rec)

    need_partial = any(not can_kernel(teir, n) for n in order) and any(
        can_kernel(teir, n) for n in order)
    if need_partial:
        for label, fuse in (("E", True), ("F", False)):
            plan = make_mixed_plan(order, teir, fuse_within=fuse)
            ok, diags = verifier.verify(teir, plan)
            cost = cost_mixed(plan, teir, c_user, c_kernel, extra_kernel,
                              exec_us, boundary_us)
            rec = {
                "id": label,
                "domain": "mixed",
                "fused": fuse,
                "legal": bool(ok),
                "diagnostics": diags,
                "plan": plan,
                "cost": cost,
                "why": None if ok else ("验证器拒绝: " + "; ".join(
                    d["code"] for d in diags[:4])),
            }
            cands.append(rec)

    cost_a = None
    for rec in cands:
        if rec["id"] == "A" and rec["legal"]:
            cost_a = rec["cost"]["total_us"]
    if cost_a is None:
        # A 非法极少见；仍用公式 N*c_user+E 作对照
        cost_a = n * c_user + exec_us
    for rec in cands:
        rec["cost"]["benefit_vs_A_us"] = round(
            cost_a - rec["cost"]["total_us"], 3)

    legal = [c for c in cands if c["legal"]]
    # 同分选用户态（不强制内核化）：linux_userspace 优先，再不融合
    def rank_key(c):
        # 同分：用户态 > 部分内核化 > 全内核；不强制迁移
        dom = c["domain"]
        dom_penalty = {"linux_userspace": 0, "mixed": 1, "linux_kernel": 2}.get(dom, 3)
        fused_bit = 1 if c["fused"] else 0
        return (c["cost"]["total_us"], dom_penalty, fused_bit, c["id"])

    chosen = min(legal, key=rank_key) if legal else None
    if chosen:
        parts = [
            "Cost=G·c + E + extra = %s" % chosen["cost"]["total_us"],
            "G=%d c=%s extra=%s" % (
                chosen["cost"]["groups"],
                chosen["cost"]["c_us"],
                chosen["cost"]["extra_us"],
            ),
        ]
        if chosen["domain"] == "linux_userspace":
            kern = [c for c in legal if c["domain"] == "linux_kernel"]
            mixed = [c for c in legal if c["domain"] == "mixed"]
            if kern:
                best_k = min(kern, key=lambda x: x["cost"]["total_us"])
                parts.append("内核最优 %s 的 Cost=%s ≥ 用户态，故不迁移"
                             % (best_k["id"], best_k["cost"]["total_us"]))
            elif mixed:
                best_m = min(mixed, key=lambda x: x["cost"]["total_us"])
                parts.append("全内核非法；部分内核化 %s Cost=%s ≥ 用户态，故不迁移"
                             % (best_m["id"], best_m["cost"]["total_us"]))
            else:
                parts.append("内核候选全部非法（域/版本），只能留用户态")
        elif chosen["domain"] == "mixed":
            parts.append("部分内核化：不能进内核的阶段留用户态")
        else:
            parts.append("内核 Cost 低于用户态，选择迁移")
        chosen["why"] = "; ".join(parts)

    return {
        "version": 1,
        "kind": "selective_kernelize",
        "task": teir.get("task") or {},
        "model": {
            "formula": "Cost = G*c_domain + E + extra_domain; Benefit = Cost(A)-Cost(p)",
            "c_user_us": c_user,
            "c_kernel_us": c_kernel,
            "extra_kernel_us": extra_kernel,
            "exec_us": exec_us,
            "note": "经验成本，不是 WCET；同分不强制内核化",
        },
        "candidates": cands,
        "chosen": None if chosen is None else chosen["id"],
        "chosen_record": chosen,
        "n_legal": len(legal),
    }


def main():
    ap = argparse.ArgumentParser(description="A/B/C/D 选择性内核化候选")
    ap.add_argument("--teir", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--chosen-plan", default=None,
                    help="把入选方案写成独立 plan.json，供验证器与代码生成使用")
    ap.add_argument("--c-user-us", type=float, default=21.0)
    ap.add_argument("--c-kernel-us", type=float, default=21.0)
    ap.add_argument("--kernel-extra-us", type=float, default=0.0,
                    help="进内核的附加税；> 融合所省释放时，应选用户态")
    ap.add_argument("--exec-us", type=float, default=0.0)
    ap.add_argument("--boundary-us", type=float, default=50.0,
                    help="跨域边通信税（µs/边）；与数据版本分离")
    args = ap.parse_args()

    teir = load_json(args.teir)
    v = load_verifier()
    doc = generate(teir, args.c_user_us, args.c_kernel_us,
                   args.kernel_extra_us, args.exec_us, v,
                   boundary_us=args.boundary_us)
    dump_json(args.out, doc)
    rec = doc.get("chosen_record")
    if args.chosen_plan:
        if not rec or not rec.get("plan"):
            print("无入选 plan，不写 %s" % args.chosen_plan, file=sys.stderr)
            return 3
        dump_json(args.chosen_plan, rec["plan"])
        print("chosen plan → %s  (%s %s)" % (
            args.chosen_plan, doc.get("chosen"), rec.get("domain")))
    print("A/B/C/D/E/F legal=%d/%d  chosen=%s"
          % (doc["n_legal"], len(doc["candidates"]), doc["chosen"]))
    for c in doc["candidates"]:
        flag = "→" if c["id"] == doc["chosen"] else " "
        st = "LEGAL " if c["legal"] else "REJECT"
        print("  %s %s %s  fused=%s  Cost=%s  %s"
              % (flag, c["id"], st, c["fused"],
                 c["cost"]["total_us"] if c["legal"] else "-",
                 c["why"] or ""))
    print("wrote %s" % args.out)
    return 0 if doc["chosen"] else 3


if __name__ == "__main__":
    sys.exit(main())
