#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-18：反馈校准 CLI。errors=18A 误差表；calibrate=18B 训练轮残差。

  python3 kfusion/scripts/pr18_feedback__反馈校准.py errors \
      --stkp kfusion/docs/pr17_uku_plan/stkp.json \
      --plan kfusion/docs/pr17_uku_plan/plan.json \
      --src kfusion/examples/ecos/mixed_pid_stateful__积分混合.c \
      --env kfusion/docs/pr11_deploy_objective/env_evidence.json \
      --profile-load 0 kfusion/docs/pr11_deploy_objective/sched_profile_v3.json \
      --profile-load 4 kfusion/docs/pr11_deploy_objective/sched_profile_load4.json \
      --profile-load 4 kfusion/docs/pr17_uku_plan/sched_profile_mixed_plan_load4.json \
      -o kfusion/docs/pr18_feedback/error_report.json

同一 --profile-load 可给多份，按 load 合并；形状冲突直接失败。
静默残差不得拿去解释加压。不重跑板上矩阵。
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))
sys.path.insert(0, os.path.join(KF, "scripts"))

import calibrate_cost as cc  # noqa: E402
import deploy_objective as do  # noqa: E402
import feedback_loop as fl  # noqa: E402
import feedback_profile as fb  # noqa: E402
import sched_aware_cost as sac  # noqa: E402
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "pr15_holdout", os.path.join(HERE, "pr15_holdout__训练验证分离.py"))
pr15 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr15)


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


def cmd_errors(args):
    env = load_json(args.env)
    stkp = load_json(args.stkp)
    by_load = {}
    for load, path in args.profile_load or []:
        extra = do.load_profile(path)
        load = int(load)
        if load in by_load:
            by_load[load] = do.merge_profiles(by_load[load], extra)
        else:
            by_load[load] = extra
    if not by_load:
        print("至少给一份 --profile-load LOAD PATH", file=sys.stderr)
        return 2
    hashes = {
        "stkp": args.stkp,
        "stkp_sha256": do.sha256_file(args.stkp),
        "plan": args.plan,
        "plan_sha256": do.sha256_file(args.plan) if args.plan and os.path.isfile(args.plan) else None,
        "src": args.src,
        "src_sha256": do.sha256_file(args.src) if args.src and os.path.isfile(args.src) else None,
        "env": args.env,
        "env_sha256": do.sha256_file(args.env),
        "profiles": [
            {"load": int(load), "path": path, "sha256": do.sha256_file(path)}
            for load, path in (args.profile_load or [])
        ],
    }
    report = fb.build_report(stkp, by_load, env, hashes=hashes)
    dump_json(args.out, report)
    print("wrote %s  rows=%d measured=%d insufficient=%d"
          % (args.out, report["n_rows"], report["n_measured"],
             report["n_insufficient"]))
    print("%-6s %-8s %-8s %-6s %8s %8s %8s"
          % ("load", "sched", "n_rel", "xdom", "n", "Δmed", "Δspan"))
    for s in report.get("summary") or []:
        print("%-6s %-8s %-8s %-6s %8d %8.1f %8.1f"
              % (s["load"], s["sched"], s["n_releases"],
                 "yes" if s["cross_domain"] else "no",
                 s["n"], s["delta_median_us"],
                 s["delta_max_us"] - s["delta_min_us"]))
    # 抽样打印 mixed 三释放，便于核对 427 vs 426
    for r in report["rows"]:
        mk = r["match_key"]
        if mk.get("n_releases") == 3 and mk.get("domain") == "mixed":
            meas = r["measurement"]
            err = r.get("error") or {}
            print("  mixed n_rel=3 load=%s %s  R̂=%s  Q99=%s  Δ=%s  %s"
                  % (mk["load"], mk["sched"],
                     (r["static_prediction"] or {}).get("predicted_R_us"),
                     meas.get("q99_resp_us"),
                     err.get("delta_q99_minus_Rhat_us"),
                     meas.get("status")))
    return 0


def parse_rounds(text):
    text = (text or "").strip()
    if not text:
        return set()
    if "-" in text and "," not in text:
        a, b = text.split("-", 1)
        return set(range(int(a), int(b) + 1))
    return set(int(x) for x in text.split(",") if x.strip() != "")


def _hashes(args):
    return {
        "stkp": args.stkp,
        "stkp_sha256": do.sha256_file(args.stkp),
        "plan": getattr(args, "plan", None),
        "plan_sha256": (do.sha256_file(args.plan)
                        if getattr(args, "plan", None)
                        and os.path.isfile(args.plan) else None),
        "src": getattr(args, "src", None),
        "src_sha256": (do.sha256_file(args.src)
                       if getattr(args, "src", None)
                       and os.path.isfile(args.src) else None),
        "env": args.env,
        "env_sha256": do.sha256_file(args.env),
    }


def cmd_calibrate(args):
    train = parse_rounds(args.train_rounds)
    hold = parse_rounds(args.holdout_rounds)
    if not train:
        print("训练轮为空", file=sys.stderr)
        return 2
    if train & hold:
        print("训练轮与留出轮重叠：%s" % sorted(train & hold), file=sys.stderr)
        return 2
    env = load_json(args.env)
    stkp = load_json(args.stkp)
    try:
        boundary = do.boundary_from_file(args.boundary)
    except do.TermMissing as exc:
        print("边界文件不可用：%s" % exc, file=sys.stderr)
        return 2
    hashes = _hashes(args)
    hashes["train_rounds"] = sorted(train)
    hashes["holdout_rounds"] = sorted(hold)
    loads = [int(x) for x in (args.loads or [0])]
    extra = args.extra_shapes or []
    train_prof, hold_prof = {}, {}
    meta = {"pin_cpu": 3, "note": "pr18b train/holdout 子集"}
    for load in loads:
        cells = pr15.collect_cells(args.shapes, args.baseline, load=load,
                                   extra_shapes=extra)
        train_prof[load] = pr15.profile_from(cells, train, env, boundary, meta)
        if hold:
            hold_prof[load] = pr15.profile_from(cells, hold, env, boundary, meta)
    train_rep = fb.build_report(stkp, train_prof, env, hashes=hashes)
    table = cc.fit_table(train_rep["rows"], sorted(train), hashes=hashes)
    target = fb.build_report(stkp, hold_prof if hold else train_prof, env,
                             hashes=hashes)
    applied = cc.apply_to_report(target, table)
    doc = {
        "kind": "pr18_calibration",
        "version": 1,
        "model": cc.MODEL,
        "calibration_id": cc.CALIBRATION_ID,
        "claim": ("Δ̂ 只用训练轮；应用到留出轮的 measurement，不覆盖 static。"
                  "经验预测，不是 WCET。"),
        "hashes": hashes,
        "n_table": len(table),
        "n_fitted": sum(1 for v in table.values() if v.get("status") == "fitted"),
        "table": [{"key_id": k, "calibration": table[k]} for k in sorted(table)],
        "applied": applied,
        "holdout_summary": cc.holdout_summary(applied.get("rows") or []),
    }
    dump_json(args.out, doc)
    hs = doc["holdout_summary"]
    print("wrote %s  fitted=%d/%d  holdout n=%s MAE=%s max|ε|=%s"
          % (args.out, doc["n_fitted"], doc["n_table"], hs.get("n"),
             hs.get("mae_us"), hs.get("max_abs_us")))
    for r in applied.get("rows") or []:
        mk = r.get("match_key") or {}
        if mk.get("n_releases") == 3 and mk.get("domain") == "mixed":
            cal = r.get("calibration") or {}
            c2 = r.get("calibrated") or {}
            print("  mixed load=%s %s  R̂=%s  Q99_hold=%s  Qcal=%s  ε=%s  %s"
                  % (mk.get("load"), mk.get("sched"),
                     (r.get("static_prediction") or {}).get("predicted_R_us"),
                     (r.get("measurement") or {}).get("q99_resp_us"),
                     c2.get("q99_cal_us"), c2.get("holdout_minus_cal_us"),
                     cal.get("status")))
    return 0


def cmd_shadow(args):
    stkp = load_json(args.stkp)
    prof = do.load_profile(args.profile)
    table = fl.table_from_doc(load_json(args.calibration))
    env = prof.get("env_evidence") or load_json(args.env)
    rec, _ = fl.shadow_reselect(
        stkp.get("candidates") or [], prof, table, env,
        int(args.load or 0), _hashes(args),
        alpha=args.alpha, beta=args.beta, gamma=args.gamma,
        fallback=(fl.M3_FALLBACK if args.fallback else fl.M2_DROP),
        apply=bool(args.apply))
    dump_json(args.out, rec)
    print("shadow before=%s@%s after=%s@%s status=%s"
          % ((rec.get("chosen_before") or {}).get("candidate_id"),
             (rec.get("chosen_before") or {}).get("sched"),
             (rec.get("chosen_after") or {}).get("candidate_id"),
             (rec.get("chosen_after") or {}).get("sched"),
             rec.get("apply_status")))
    return 0


def _scen_of(cands, prof):
    out = {}
    for i, c in enumerate(cands):
        hit = do.deployment_for(
            prof, c.get("domain"), sac._cand_shape(c))
        out[i] = hit[0] if hit else None
    return out


def cmd_ablate(args):
    """M0 静态 / M1 实测画像 / M2 校准重选 / M3 校准+回退+β。留出轮评价。"""
    train = parse_rounds(args.train_rounds)
    hold = parse_rounds(args.holdout_rounds)
    if train & hold:
        print("训练/留出重叠", file=sys.stderr)
        return 2
    env = load_json(args.env)
    stkp = load_json(args.stkp)
    cands = [c for c in (stkp.get("candidates") or []) if c.get("plan")]
    load = int(args.load or 0)
    cells = pr15.collect_cells(args.shapes, args.baseline, load=load,
                               extra_shapes=args.extra_shapes or [])
    boundary = do.boundary_from_file(args.boundary)
    meta = {"note": "pr18d"}
    train_prof = pr15.profile_from(cells, train, env, boundary, meta)
    hashes = _hashes(args)
    train_rep = fb.build_report(stkp, {load: train_prof}, env, hashes=hashes)
    table = cc.fit_table(train_rep["rows"], sorted(train), hashes=hashes)
    scen = _scen_of(cands, train_prof)

    def hold_stats(idx, sched):
        name = scen.get(idx)
        return pr15.stats_on(cells, name, sched, hold) if name else None

    methods = {}
    # M0
    pool = [(i, c) for i, c in enumerate(cands) if c.get("deployment_ready")]
    i0, c0 = min(pool, key=lambda t: ((t[1].get("cost") or {})
                                      .get("predicted_R_us") or 1e18))
    methods["M0"] = {"cand_index": i0, "sched": "cfs",
                     "basis": "min predicted_R, sched=cfs"}
    # M1
    d1 = do.decide(cands, train_prof, alpha=1.0, beta=0.0, gamma=0.0)
    ch1 = d1.get("chosen") or {}
    methods["M1"] = {"cand_index": ch1.get("cand_index"),
                     "sched": ch1.get("sched"), "basis": "J=Q99 measured train"}
    # M2
    d2 = fl.overlay_decide(cands, train_prof, table, env, load, hashes,
                           fallback=fl.M2_DROP)
    ch2 = d2.get("chosen") or {}
    methods["M2"] = {"cand_index": ch2.get("cand_index"),
                     "sched": ch2.get("sched"), "basis": "J=Q99_cal drop-missing"}
    # M3
    d3 = fl.overlay_decide(cands, train_prof, table, env, load, hashes,
                           alpha=1.0, beta=1e5, gamma=0.0,
                           fallback=fl.M3_FALLBACK)
    ch3 = d3.get("chosen") or {}
    methods["M3"] = {"cand_index": ch3.get("cand_index"),
                     "sched": ch3.get("sched"),
                     "basis": "J=Q99_cal|measured + β R_miss"}

    oracle = []
    for i, c in enumerate(cands):
        if not c.get("deployment_ready"):
            continue
        for s in do.SCHED_CLASSES:
            st = hold_stats(i, s)
            if not st:
                continue
            oracle.append((st["q99_us"] + 1e5 * st["miss"] / float(st["n"]),
                           i, s, st))
    oracle.sort()
    rows = []
    for mid, m in methods.items():
        idx, sched = m.get("cand_index"), m.get("sched")
        st = hold_stats(idx, sched) if idx is not None else None
        jv = None if st is None else round(
            st["q99_us"] + 1e5 * st["miss"] / float(st["n"]), 3)
        orc = oracle[0] if oracle else None
        rows.append({
            "id": mid, "basis": m["basis"],
            "chosen": None if idx is None else {
                "cand_index": idx, "id": cands[idx].get("id"), "sched": sched},
            "holdout": st, "J_hold_us": jv,
            "agree_oracle": bool(orc and idx == orc[1] and sched == orc[2]),
            "regret_us": (None if (jv is None or not orc)
                          else round(jv - orc[0], 3)),
            "holdout_miss": None if st is None else st["miss"],
        })
    doc = {
        "kind": "pr18d_ablation",
        "claim": ("训练轮决策、留出轮评价；本任务 margin 大时 M1≡M2 不证明校准无用，"
                  "只证明当前负载分不开。非 WCET。"),
        "load": load, "train_rounds": sorted(train),
        "holdout_rounds": sorted(hold),
        "n_fitted": sum(1 for v in table.values() if v.get("status") == "fitted"),
        "methods": rows,
    }
    dump_json(args.out, doc)
    print("%-4s %-28s %-8s %10s %8s %s" % (
        "id", "chosen", "sched", "J_hold", "regret", "agree"))
    for r in rows:
        ch = r.get("chosen") or {}
        print("%-4s %-28s %-8s %10s %8s %s" % (
            r["id"], ch.get("id"), ch.get("sched"), r.get("J_hold_us"),
            r.get("regret_us"), r.get("agree_oracle")))
    return 0


def main():
    ap = argparse.ArgumentParser(description="PR-18 反馈校准")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("errors", help="静态 R̂ vs 实测 Q99（按 load 分表）")
    p.add_argument("--stkp", required=True)
    p.add_argument("--plan", default=None)
    p.add_argument("--src", default=None)
    p.add_argument("--env", required=True)
    p.add_argument("--profile-load", nargs=2, action="append",
                   metavar=("LOAD", "PATH"), dest="profile_load",
                   help="可重复；同 load 多份则 merge_profiles")
    p.add_argument("-o", "--out", required=True)
    c = sub.add_parser("calibrate", help="训练轮估 Δ̂，在留出轮上评价")
    c.add_argument("--stkp", required=True)
    c.add_argument("--plan", default=None)
    c.add_argument("--src", default=None)
    c.add_argument("--env", required=True)
    c.add_argument("--shapes", required=True)
    c.add_argument("--baseline", required=True)
    c.add_argument("--extra-shapes", action="append", default=[])
    c.add_argument("--boundary",
                   default=os.path.join(KF, "docs", "pr10c_boundary",
                                        "boundary.json"))
    c.add_argument("--loads", nargs="+", default=["0"],
                   help="要校准的负载，必须分开拟合")
    c.add_argument("--train-rounds", default="0-8")
    c.add_argument("--holdout-rounds", default="9")
    c.add_argument("-o", "--out", required=True)
    s = sub.add_parser("shadow", help="PR-18C：校准重选记账，默认不改 chosen")
    s.add_argument("--stkp", required=True)
    s.add_argument("--profile", required=True)
    s.add_argument("--calibration", required=True)
    s.add_argument("--env", required=True)
    s.add_argument("--plan", default=None)
    s.add_argument("--src", default=None)
    s.add_argument("--load", type=int, default=0)
    s.add_argument("--alpha", type=float, default=1.0)
    s.add_argument("--beta", type=float, default=0.0)
    s.add_argument("--gamma", type=float, default=0.0)
    s.add_argument("--fallback", action="store_true")
    s.add_argument("--apply", action="store_true")
    s.add_argument("-o", "--out", required=True)
    d = sub.add_parser("ablate", help="PR-18D：M0–M3 留出评价")
    d.add_argument("--stkp", required=True)
    d.add_argument("--env", required=True)
    d.add_argument("--shapes", required=True)
    d.add_argument("--baseline", required=True)
    d.add_argument("--extra-shapes", action="append", default=[])
    d.add_argument("--boundary",
                   default=os.path.join(KF, "docs", "pr10c_boundary",
                                        "boundary.json"))
    d.add_argument("--plan", default=None)
    d.add_argument("--src", default=None)
    d.add_argument("--load", type=int, default=0)
    d.add_argument("--train-rounds", default="0-8")
    d.add_argument("--holdout-rounds", default="9")
    d.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    if args.cmd == "errors":
        return cmd_errors(args)
    if args.cmd == "calibrate":
        return cmd_calibrate(args)
    if args.cmd == "shadow":
        return cmd_shadow(args)
    if args.cmd == "ablate":
        return cmd_ablate(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
