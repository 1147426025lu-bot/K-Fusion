#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr16_ablation__消融对照.py — PR-16：去掉哪个机制会怎样。

五个方法，每个只比前一个多一样东西：

  M0 静态模型        R̂=ΣC+ΣL+ΣH 最小者；调度类固定 CFS；结论只看 R̂≤deadline
  M1 +调度类维度     部署仍按 R̂ 选；调度类固定 FIFO99（纯模型区分不了调度类，
                    所以这一维只能靠「知道它的存在」白拿，不是靠模型算出来）
  M2 +实测尾部       改成按实测 Q99 选（α=1, β=0）；结论改成 Q99≤deadline
  M3 +违约风险       J=Q99+β·R_miss（β=1e5）；结论用四档（超期数优先）
  M4 +边界成本       再加 γ·C_boundary。γ 默认 0——Q99 里已含跨界往返，
                    所以预期 M4≡M3；**这一项若真的改变了结论，说明重复计数**。

指标（在留出的那一轮上算，10 折平均）：
  · 选择正确率：选中项是否就是验证轮上的 argmin
  · 平均遗憾：J_val(选中) − J_val(oracle)
  · 验证轮实际超期率：选中的组合在验证轮上超期拍数 / 总拍数
  · **假可行率**：方法判「可行/不能背书」但验证轮实测有超期的比例 ← 关键指标
  · 期限判定四档分布

为什么不能只看选择：本任务的候选差距远大于轮间波动（PR-15 已量化
margin/noise=2.4–9.5），所有方法都会选对，消融会变成一排满分。
真正分得开的是**期限判定的可信度**。

用法：
  python3 kfusion/scripts/pr16_ablation__消融对照.py \
    --shapes .../shapes.json --baseline .../baseline.json \
    --stkp .../candidates_pr10e_stkp.json --env .../env_evidence.json \
    --load 4 --deadline 1000 --horizon-s 3600 -o pr16_ablation_load4.json
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
sys.path.insert(0, os.path.join(KF, "scripts"))

import deploy_objective as do  # noqa: E402
import sched_aware_cost as sac  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "pr15_holdout", os.path.join(HERE, "pr15_holdout__训练验证分离.py"))
pr15 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr15)

METHODS = [
    ("M0", "静态模型", "R̂ 最小，调度类固定 CFS"),
    ("M1", "+调度类维度", "部署按 R̂，调度类固定 FIFO99"),
    ("M2", "+实测尾部", "按实测 Q99 选，β=0"),
    ("M3", "+违约风险", "J=Q99+β·R_miss，β=1e5"),
    ("M4", "+边界成本", "M3 + γ·C_boundary（γ=1）"),
]


def dump_json(p, obj):
    d = os.path.dirname(os.path.abspath(p))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def method_choice(mid, cands, cells, scen_of, profile_full, train_rounds,
                  deadline_us, horizon_s, period_us):
    """每个方法在训练轮上做决策，返回 (cand_index, sched, 判定, 依据)。"""
    # --- M0/M1：静态模型，R̂ 与调度类无关
    if mid in ("M0", "M1"):
        pool = [(i, c) for i, c in enumerate(cands)
                if c.get("deployment_ready")]
        if not pool:
            return None
        i, c = min(pool, key=lambda t: ((t[1].get("cost") or {})
                                        .get("predicted_R_us") or 1e18))
        sched = "cfs" if mid == "M0" else "fifo99"
        r_hat = (c.get("cost") or {}).get("predicted_R_us")
        verdict = ("empirically_feasible" if (r_hat is not None and deadline_us
                                              and r_hat <= deadline_us)
                   else "cannot_certify")
        return {"cand_index": i, "sched": sched, "verdict": verdict,
                "basis": {"R_hat_us": r_hat, "note": "静态模型，无实测分布"}}
    # --- M2~M4：实测驱动
    beta = 0.0 if mid == "M2" else 1e5
    gamma = 0.0 if mid != "M4" else 1.0
    prof = pr15.profile_from(cells, train_rounds, profile_full["env_evidence"],
                            profile_full["boundary"], profile_full["measure_env"])
    dec = do.decide(cands, prof, alpha=1.0, beta=beta, gamma=gamma,
                    deadline_us=deadline_us)
    ch = dec.get("chosen")
    if not ch:
        return None
    i = ch["cand_index"]
    sched = ch["sched"]
    scen = scen_of.get(i)
    if mid == "M2":
        # 只用 Q99 判：Q99>deadline 才算不可行
        q = ch["terms"].get("Q99_resp_us")
        verdict = ("infeasible_observed" if (q is not None and deadline_us
                                            and q > deadline_us)
                   else "empirically_feasible")
    else:
        v, why = pr15.verdict_of(
            pr15.stats_on(cells, scen, sched, train_rounds) if scen else None,
            deadline_us, horizon_s, period_us)
        verdict = v
    return {"cand_index": i, "sched": sched, "verdict": verdict,
            "basis": {"J_us": ch["J_us"], "terms": ch["terms"],
                      "beta": beta, "gamma": gamma}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--stkp", required=True)
    ap.add_argument("--env", required=True)
    ap.add_argument("--deadline", type=float, default=1000.0)
    ap.add_argument("--period", type=float, default=1000.0)
    ap.add_argument("--horizon-s", dest="horizon_s", type=float, default=3600.0)
    ap.add_argument("--load", type=int, default=0)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    cells = pr15.collect_cells(a.shapes, a.baseline, load=a.load)
    stkp = json.load(io.open(a.stkp, encoding="utf-8"))
    cands = [c for c in (stkp.get("candidates") or []) if c.get("plan")]
    env = json.load(io.open(a.env, encoding="utf-8"))
    boundary = {"t_ioctl_us": {"value": 2.0, "evidence": "measured",
                              "samples": 15000, "rounds": 3}}
    meta = {"period_us": a.period, "deadline_us": a.deadline, "pin_cpu": 3,
            "cycles_per_round": 5000, "repeat": 9, "load": a.load}
    allr = set(range(10))
    full = pr15.profile_from(cells, allr, env, boundary, meta)

    scen_of = {}
    for i, c in enumerate(cands):
        dom = c.get("domain") or ((c.get("plan") or {}).get("meta") or {}).get("platform")
        hit = do.deployment_for(full, dom, sac._cand_shape(c))
        scen_of[i] = hit[0] if hit else None

    doc = {"kind": "pr16_ablation", "version": 1, "load": a.load,
           "deadline_us": a.deadline, "horizon_s": a.horizon_s,
           "protocol": "留一法 10 折；训练轮决策、留出轮验证",
           "methods": [{"id": m, "name": n, "def": d} for m, n, d in METHODS],
           "claim": ("消融只在本任务与本平台上成立；本任务 margin/noise=2.4–9.5，"
                     "所有方法都能选对，差别体现在期限判定的假可行率"),
           "folds": {}, "summary": {}}

    per_verdict = {m: {"n": 0, "false_feasible": 0, "miss_detected": 0,
                       "miss_total": 0, "false_alarm": 0, "ok_total": 0}
                   for m, _n, _d in METHODS}
    per_method = {m: {"n": 0, "correct": 0, "regret": [], "chosen_miss": 0,
                      "chosen_cycles": 0, "false_feasible": 0, "unverifiable": 0,
                      "verdicts": {}, "picked": set()} for m, _n, _d in METHODS}

    for held in sorted(allr):
        train = allr - {held}
        val = {held}
        fold = {"held_out_round": held, "methods": {}}
        # 验证轮 ground truth：每个候选组合各自的实际结果
        truth = {}
        for i, c in enumerate(cands):
            scen = scen_of.get(i)
            if not scen:
                continue
            for sched in do.SCHED_CLASSES:
                st = pr15.stats_on(cells, scen, sched, val)
                if st is None:
                    continue
                v, _ = pr15.verdict_of(st, a.deadline, a.horizon_s, a.period)
                truth[(i, sched)] = {"stats": st, "verdict": v,
                                     "J_val": round(st["q99_us"]
                                                    + 1e5 * st["miss"] / float(st["n"]), 3)}
        valid = [(k, v) for k, v in truth.items()
                 if cands[k[0]].get("deployment_ready")]
        oracle = min(valid, key=lambda kv: kv[1]["J_val"]) if valid else None

        # —— 逐组合判定：与方法选谁无关，检验「期限结论」本身准不准
        for (ci, sched), tv in truth.items():
            if not cands[ci].get("deployment_ready"):
                continue
            c = cands[ci]
            for mid, _n, _d in METHODS:
                if mid in ("M0", "M1"):
                    r_hat = (c.get("cost") or {}).get("predicted_R_us")
                    pred = ("empirically_feasible"
                            if (r_hat is not None and r_hat <= a.deadline)
                            else "cannot_certify")
                elif mid == "M2":
                    prof = pr15.profile_from(cells, train, env, boundary, meta)
                    st_tr = pr15.stats_on(cells, scen_of.get(ci), sched, train)
                    pred = ("infeasible_observed"
                            if (st_tr and st_tr["q99_us"] > a.deadline)
                            else "empirically_feasible")
                else:
                    prof = pr15.profile_from(cells, train, env, boundary, meta)
                    st_tr = pr15.stats_on(cells, scen_of.get(ci), sched, train)
                    pred, _why = pr15.verdict_of(st_tr, a.deadline,
                                                a.horizon_s, a.period)
                pv = per_verdict[mid]
                pv["n"] += 1
                actual_miss = tv["stats"]["miss"] > 0
                if actual_miss:
                    pv["miss_total"] += 1
                    if pred == "infeasible_observed":
                        pv["miss_detected"] += 1
                    else:
                        pv["false_feasible"] += 1
                else:
                    pv["ok_total"] += 1
                    if pred == "infeasible_observed":
                        pv["false_alarm"] += 1

        for mid, _n, _d in METHODS:
            pick = method_choice(mid, cands, cells, scen_of, full, train,
                                 a.deadline, a.horizon_s, a.period)
            rec = {"pick": pick}
            if pick is None:
                fold["methods"][mid] = rec
                continue
            key = (pick["cand_index"], pick["sched"])
            tv = truth.get(key)
            rec["val"] = tv
            rec["verdict_pred"] = pick["verdict"]
            if tv is None:
                # 选中项没有实测画像 → 这个选择无法验证。这不是缺数据，
                # 而是「模型敢选一个测都没测的方案」——单独计数，不许混进分母。
                rec["unverifiable"] = True
                per_method[mid]["unverifiable"] += 1
                per_method[mid]["verdicts"][pick["verdict"]] = \
                    per_method[mid]["verdicts"].get(pick["verdict"], 0) + 1
                per_method[mid]["picked"].add("%s@%s" % (
                    cands[pick["cand_index"]].get("id"), pick["sched"]))
            if tv and oracle:
                rec["correct"] = key == oracle[0]
                rec["regret_us"] = round(tv["J_val"] - oracle[1]["J_val"], 3)
                st = tv["stats"]
                agg = per_method[mid]
                agg["n"] += 1
                agg["correct"] += int(rec["correct"])
                agg["regret"].append(rec["regret_us"])
                agg["chosen_miss"] += st["miss"]
                agg["chosen_cycles"] += st["n"]
                agg["verdicts"][pick["verdict"]] = \
                    agg["verdicts"].get(pick["verdict"], 0) + 1
                agg["picked"].add("%s@%s" % (cands[pick["cand_index"]].get("id"),
                                             pick["sched"]))
                # 假可行：方法说行/不能背书，验证轮却真超期
                if pick["verdict"] in ("empirically_feasible",
                                       "cannot_certify") and st["miss"] > 0:
                    agg["false_feasible"] += 1
                    rec["false_feasible"] = True
            fold["methods"][mid] = rec
        doc["folds"]["r%02d" % held] = fold

    for mid, name, desc in METHODS:
        agg = per_method[mid]
        n = agg["n"] or 1
        doc["summary"][mid] = {
            "name": name, "def": desc, "n_folds": agg["n"],
            "choice_accuracy": round(agg["correct"] / float(n), 3),
            "mean_regret_us": round(sum(agg["regret"]) / float(n), 3),
            "max_regret_us": max(agg["regret"]) if agg["regret"] else None,
            "chosen_miss_rate": (round(agg["chosen_miss"] / float(agg["chosen_cycles"]), 6)
                                 if agg["chosen_cycles"] else None),
            "false_feasible_rate": round(agg["false_feasible"] / float(n), 3),
            "unverifiable_rate": round(agg["unverifiable"] / 10.0, 3),
            "verdicts": agg["verdicts"],
            "picked": sorted(agg["picked"]),
            "verdict_matrix": {
                **per_verdict[mid],
                "false_feasible_rate": (round(per_verdict[mid]["false_feasible"]
                                              / float(per_verdict[mid]["miss_total"]), 3)
                                        if per_verdict[mid]["miss_total"] else None),
                "detect_rate": (round(per_verdict[mid]["miss_detected"]
                                      / float(per_verdict[mid]["miss_total"]), 3)
                                if per_verdict[mid]["miss_total"] else None),
                "false_alarm_rate": (round(per_verdict[mid]["false_alarm"]
                                           / float(per_verdict[mid]["ok_total"]), 3)
                                     if per_verdict[mid]["ok_total"] else None),
            },
        }
    dump_json(a.out, doc)

    print("== PR-16 消融（load=%d，deadline=%g µs，留一法 10 折）=="
          % (a.load, a.deadline))
    print("%-4s %-12s %8s %10s %10s %10s %10s  %s" % (
        "", "方法", "可验证", "选择正确", "平均遗憾", "实超期率", "假可行率", "选中组合"))
    for mid, name, _d in METHODS:
        sm = doc["summary"][mid]
        print("%-4s %-12s %8s %10s %10s %10s %10s  %s" % (
            mid, name, "%d/10" % sm["n_folds"], sm["choice_accuracy"],
            sm["mean_regret_us"], sm["chosen_miss_rate"],
            sm["false_feasible_rate"], ", ".join(sm["picked"])[:44]))
    print("\n（可验证 = 选中项有实测画像的折数；不可验证的折不进正确率/遗憾的分母）")

    print("\n-- 期限判定准确性（逐 候选×调度类 组合，与选谁无关）--")
    print("%-4s %-12s %6s %10s %10s %10s" % (
        "", "方法", "组合数", "漏报(假可行)", "检出率", "误报率"))
    for mid, name, _d in METHODS:
        v = doc["summary"][mid]["verdict_matrix"]
        print("%-4s %-12s %6d %10s %10s %10s" % (
            mid, name, v["n"], "%d (%.0f%%)" % (v["false_feasible"],
                                                100 * (v["false_feasible_rate"] or 0)),
            v["detect_rate"], v["false_alarm_rate"]))
    print("\n%s" % doc["claim"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
