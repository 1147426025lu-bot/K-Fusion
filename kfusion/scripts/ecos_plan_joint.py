#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ECoS 小图联合规划器：融合边界 + 执行域的 exhaustive oracle。

这是 CP-SAT 之前的可审计基线。它只适合小图（默认最多 8 个阶段），但会
枚举所有连续分组和每组的平台分配，复用 fusion_plan 的 fail-closed 规则。
候选的代价是：每组一次释放 + 每条跨域边一次 handoff + 阶段执行时间。
"""
import argparse
import importlib.util
import io
import itertools
import json
import os
import sys


def load_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def load_fusion_planner():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "fusion_plan__融合规划.py")
    spec = importlib.util.spec_from_file_location("fusion_plan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Planner


def partitions(n):
    """Yield every contiguous partition of n ordered items."""
    for cuts in itertools.product((0, 1), repeat=max(0, n - 1)):
        groups, start = [], 0
        for i, cut in enumerate(cuts, 1):
            if cut:
                groups.append(list(range(start, i)))
                start = i
        groups.append(list(range(start, n)))
        yield groups


def edge_handoffs(order, groups, domains):
    group_of = {}
    for gi, group in enumerate(groups):
        for idx in group:
            group_of[order[idx]] = gi
    transitions = []
    for a, b in zip(order, order[1:]):
        ga, gb = group_of[a], group_of[b]
        if ga != gb and domains[ga] != domains[gb]:
            transitions.append({"from": a, "to": b,
                                "from_domain": domains[ga],
                                "to_domain": domains[gb]})
    return transitions


def main():
    ap = argparse.ArgumentParser(description="ECoS 小图联合规划 exhaustive oracle")
    ap.add_argument("dag")
    ap.add_argument("--platforms", required=True)
    ap.add_argument("--exec-us", default=None,
                    help="JSON 文件或内联对象：{阶段名: 执行时间 µs}")
    ap.add_argument("--release-cost-us", default=None,
                    help="JSON 文件或内联对象：{平台名: 每次释放 µs}，默认按 20")
    ap.add_argument("--handoff-cost-us", type=float, default=0.0)
    ap.add_argument("--max-stages", type=int, default=8)
    ap.add_argument("--oracle", action="store_true", help="输出所有合法候选")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    dag = load_json(args.dag)
    caps = load_json(args.platforms)
    platforms = caps.get("platforms") or []
    if not platforms:
        print("!! 没有平台能力，拒绝规划", file=sys.stderr)
        return 2
    order = dag.get("topo_order") or dag.get("topo_order_delayed") or []
    nodes = [n["name"] for n in dag.get("nodes") or []]
    if not order or set(order) != set(nodes):
        print("!! DAG 没有完整拓扑序，拒绝规划", file=sys.stderr)
        return 2
    if dag.get("algebraic_loops"):
        print("!! 检出代数环，拒绝规划", file=sys.stderr)
        return 2
    if len(order) > args.max_stages:
        print("!! 阶段数 %d 超过 exhaustive 上限 %d；请使用 CP-SAT/搜索器"
              % (len(order), args.max_stages), file=sys.stderr)
        return 2

    exec_us = {}
    if args.exec_us:
        exec_us = (json.loads(args.exec_us) if args.exec_us.lstrip().startswith("{")
                   else load_json(args.exec_us))
    release = {p["name"]: 20.0 for p in platforms}
    if args.release_cost_us:
        release.update(json.loads(args.release_cost_us)
                       if args.release_cost_us.lstrip().startswith("{")
                       else load_json(args.release_cost_us))

    Planner = load_fusion_planner()
    valid, rejected = [], []
    for groups in partitions(len(order)):
        for domains in itertools.product([p["name"] for p in platforms],
                                          repeat=len(groups)):
            named = [[order[i] for i in g] for g in groups]
            reasons = []
            for members, domain in zip(named, domains):
                platform = next(p for p in platforms if p["name"] == domain)
                planner = Planner(dag, platform, release.get(domain, 20.0),
                                  exec_us)
                ok, why = planner.group_legal(members)
                if not ok:
                    reasons.append({"group": members, "domain": domain,
                                    "reason": why})
            if reasons:
                rejected.append({"groups": named, "domains": list(domains),
                                 "reasons": reasons})
                continue
            transitions = edge_handoffs(order, groups, domains)
            exec_total = sum(float(exec_us.get(n, 0.0)) for n in order)
            release_total = sum(release.get(d, 20.0) for d in domains)
            handoff_total = len(transitions) * args.handoff_cost_us
            valid.append({
                "groups": [{"id": "g%d" % i, "members": g,
                            "domain": domains[i]}
                           for i, g in enumerate(named)],
                "domains": list(domains),
                "handoffs": transitions,
                "cost": {"exec_us": round(exec_total, 3),
                         "release_us": round(release_total, 3),
                         "handoff_us": round(handoff_total, 3),
                         "predicted_R_us": round(exec_total + release_total +
                                                  handoff_total, 3)},
            })

    valid.sort(key=lambda x: (x["cost"]["predicted_R_us"],
                              len(x["groups"]), len(x["handoffs"])))
    result = {"version": 1, "mode": "exhaustive_oracle",
              "order": order, "platforms": [p["name"] for p in platforms],
              "assumptions": {"handoff_cost_us": args.handoff_cost_us,
                              "release_cost_us": release},
              "candidate_count": len(valid), "rejected_count": len(rejected),
              "best": valid[0] if valid else None,
              "candidates": valid if args.oracle else valid[:10],
              "rejected": rejected[:50]}
    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write("\n")
    if not valid:
        print("!! 没有合法联合方案（候选 %d，拒绝 %d）" %
              (len(valid), len(rejected)), file=sys.stderr)
        return 3
    b = valid[0]
    print("== exhaustive oracle：合法 %d / 拒绝 %d ==" %
          (len(valid), len(rejected)))
    print("== 最优 R̂ = %.3f µs ==" % b["cost"]["predicted_R_us"])
    for g in b["groups"]:
        print("  %-3s %-28s @ %s" % (g["id"], " + ".join(g["members"]),
                                      g["domain"]))
    print("  handoffs=%d cost=%.3f µs" %
          (len(b["handoffs"]), b["cost"]["handoff_us"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
