#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr14_sched_aware_verify__调度感知验证.py — PR-14：把三件事分成三列。

审查 P1：验证器通过 ≠ 期限满足。PR-10E 已经实测到「CFS 部署验证通过但照样超期」。
本脚本不再输出单一 R̂，而是给出三列，任何一列都不得替代另外两列：

  compile_legal   语义/结构合法（来自 verify，与时间无关）
  model.feasible  旧成本模型 R̂=ΣC+ΣL+ΣH ≤ deadline 的判断（保留作对照）
  deadline_verdict 只认实测，四档：
                  infeasible_observed / cannot_certify /
                  empirically_feasible / insufficient_evidence

**没有「可调度」这一档**。最乐观也只到「在这些观测里没看到超期，残余风险上界 X」。

用法：
  python3 kfusion/scripts/pr14_sched_aware_verify__调度感知验证.py \
      --stkp kfusion/docs/pr11_deploy_objective/candidates_pr10e_stkp.json \
      --profile kfusion/docs/pr11_deploy_objective/sched_profile_v2.json \
      --deadline 1000 --horizon-s 3600 -o report.json
"""
from __future__ import print_function

import argparse
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))

import sched_aware_cost as sac  # noqa: E402


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


SYM = {
    sac.VERDICT_INFEASIBLE: "✗ 超期",
    sac.VERDICT_CANNOT_CERTIFY: "△ 不能背书",
    sac.VERDICT_FEASIBLE: "✓ 观测内可行",
    sac.VERDICT_NO_EVIDENCE: "? 证据不足",
}


def cmd_report(args):
    stkp = load_json(args.stkp)
    prof = load_json(args.profile)
    cands = [c for c in (stkp.get("candidates") or [])
             if c.get("id") or c.get("plan")]
    if not cands:
        raise SystemExit("候选集为空：%s" % args.stkp)
    rep = sac.evaluate_all(cands, prof, deadline_us=args.deadline,
                          horizon_s=args.horizon_s)
    rep["sources"] = {"stkp": args.stkp, "profile": args.profile}
    env = prof.get("env_evidence") or {}
    rep["env"] = {
        "complete": env.get("complete"),
        "release": (env.get("kernel") or {}).get("release"),
        "preempt": (env.get("kernel") or {}).get("preempt_class"),
        "host": (env.get("host") or {}).get("model"),
        "rev": (env.get("code") or {}).get("git_rev"),
        "note": "四档结论只在这台机器/这个内核上成立",
    }
    dump_json(args.out, rep)

    print("deadline=%s µs  horizon=%s  env=%s/%s rev=%s" % (
        args.deadline, args.horizon_s, rep["env"]["host"],
        rep["env"]["preempt"], rep["env"]["rev"]))
    print("%-46s %-6s %-14s %8s %8s  %s"
          % ("候选", "组", "部署", "R̂_model", "Q99", "deadline 结论"))
    for r in rep["rows"]:
        sh = r["shape"]
        model = (r.get("model") or {}).get("R_hat_us")
        m0 = (r.get("measured") or [{}])[0].get("measured") or {}
        q99 = m0.get("q99_resp_us")
        per = []
        for m in r.get("measured") or []:
            per.append("%s:%s" % (m["sched"], SYM.get(m.get("deadline_verdict"), "?")))
        print("%-46s %-6s %-14s %8s %8s  %s"
              % (str(r.get("candidate_id"))[:46], sh.get("n_groups"),
                 r.get("deployment") or "-", model, q99,
                 " ".join(per) if per else SYM.get(r["deadline_verdict"], "?")))
    print("\n统计：%s" % json.dumps(rep["counts"], ensure_ascii=False))
    print("声明：%s" % rep["claim"])
    bad = rep["counts"].get(sac.VERDICT_NO_EVIDENCE, 0)
    return 0 if bad == 0 else 3


def main():
    ap = argparse.ArgumentParser(description="调度感知的可行性验证 v2")
    ap.add_argument("--stkp", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--deadline", type=float, default=None)
    ap.add_argument("--horizon-s", dest="horizon_s", type=float, default=3600.0,
                    help="把超期率上界折成次数的任务时长；不给就只能 cannot_certify")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    return cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
