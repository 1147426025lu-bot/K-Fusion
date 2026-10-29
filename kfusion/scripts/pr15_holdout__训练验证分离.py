#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr15_holdout__训练验证分离.py — PR-15：别用同一批轮次既选又证。

现在的做法是把 10 轮全用来建画像，再用同一个画像宣布「选对了」——这是自证。
本脚本按轮次切分：

  折 A：第 0–4 轮建画像（训练），第 5–9 轮检验（验证）
  折 B：反向（5–9 训练，0–4 验证）

每折做三件事：
  1. 用**训练轮**算 J，选出 (部署, 调度类)；
  2. 用**验证轮**重算每个候选的 J，得到 oracle；
  3. 报三件事：选择是否一致、遗憾（regret = J_val(选中) − J_val(oracle)）、
     以及训练/验证两边的期限结论是否翻转。

为什么要双向两折：单折的「一致/不一致」只有一个样本，无法区分「方法稳」
与「恰好这一折稳」。两折只够看方向，不足以给置信度——这点写进结论，不夸大。

用法：
  python3 kfusion/scripts/pr15_holdout__训练验证分离.py \
    --shapes kfusion/docs/pr11_shapes/shapes.json \
    --baseline kfusion/docs/pr10e_baseline/baseline.json \
    --stkp kfusion/docs/pr11_deploy_objective/candidates_pr10e_stkp.json \
    --profile-full kfusion/docs/pr11_deploy_objective/sched_profile_v2.json \
    --env kfusion/docs/pr11_deploy_objective/env_evidence.json \
    --deadline 1000 --horizon-s 3600 --betas 0 1e5 -o holdout.json
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

import deploy_objective as do  # noqa: E402
import sched_aware_cost as sac  # noqa: E402


def load_json(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def dump_json(p, obj):
    d = os.path.dirname(os.path.abspath(p))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _ingest_shapes(out, shapes_path, load=0, source="shapes"):
    sh = load_json(shapes_path)
    for c in sh.get("cells") or []:
        if c.get("status") != "ok" or int(c.get("load") or 0) != load:
            continue
        s = c.get("shape") or {}
        doms = s.get("domains") or []
        out[c["scenario"]] = {
            "scenario": c["scenario"],
            "domain": "mixed" if len(set(doms)) > 1 else (doms[0] if doms else None),
            "shape": {"n_groups": s.get("n_groups"), "fused": s.get("fused"),
                      "n_releases": s.get("n_releases"),
                      "members": s.get("members"),
                      "phases_us": s.get("phases_us"),
                      "exec_mode": s.get("exec_mode")},
            "sched": {cond["sched"]: cond.get("runs") or []
                      for cond in c.get("conditions") or []},
            "source": source,
            "load": load,
        }


def collect_cells(shapes_path, baseline_path, load=0, extra_shapes=None):
    """把数据源汇成 (scenario → 元信息 + 每调度类的逐轮记录)。

    extra_shapes 后写入：mixed_plan 等新格子并进来，不得覆盖同名旧格子
    （同名视为冲突，直接报错）。
    """
    out = {}
    _ingest_shapes(out, shapes_path, load=load, source="shapes")
    if load == 0:  # PR-10E 基线只有静默条件
        b = load_json(baseline_path)
        for c in b.get("cells") or []:
            scen = c.get("scenario")
            if scen not in do.DEPLOYMENT_DOMAIN:
                continue
            shp = dict(do.DEPLOYMENT_SHAPE[scen])
            dom = do.DEPLOYMENT_DOMAIN[scen]
            out[scen] = {
                "scenario": scen, "domain": dom, "shape": shp,
                "sched": {cond["sched"]: cond.get("runs") or []
                          for cond in c.get("conditions") or []},
                "source": "baseline", "load": 0,
            }
    for extra in extra_shapes or []:
        before = set(out)
        tmp = {}
        _ingest_shapes(tmp, extra, load=load, source="extra:" + extra)
        clash = set(tmp) & before
        if clash:
            raise SystemExit("extra-shapes 与已有 scenario 重名：%s" % sorted(clash))
        out.update(tmp)
    return out


def profile_from(cells, rounds, env, boundary, meta):
    """只用指定轮次建画像。rounds 是允许的 repeat 编号集合。"""
    prof = {
        "kind": do.PROFILE_KIND, "version": 2,
        "deployments": {}, "unmeasured": [],
        "boundary": boundary, "env_evidence": env,
        "measure_env": dict(meta, note="holdout 子集画像"),
    }
    for name, c in cells.items():
        scheds = {}
        for sched, runs in c["sched"].items():
            sub = [r for r in runs if int(r.get("repeat") or 0) in rounds
                   and r.get("n") and r.get("valid") is not False]
            if not sub:
                continue
            try:
                scheds[sched] = do._cell_profile(sub)
            except do.TermMissing as exc:
                scheds[sched] = {"evidence": "missing", "reason": str(exc)}
        prof["deployments"][name] = {
            "maps_to_domain": c["domain"],
            "shape": c["shape"],
            "sched": scheds,
        }
    return prof


def stats_on(cells, scenario, sched, rounds):
    """在指定轮次上算 Q99（各轮 p99 的中位数）与超期数。"""
    runs = [r for r in (cells[scenario]["sched"].get(sched) or [])
            if int(r.get("repeat") or 0) in rounds and r.get("n")
            and r.get("valid") is not False]
    if not runs:
        return None
    p99 = [r["response"]["p99_us"] for r in runs]
    q99 = do._median(p99)
    n = sum(int(r.get("n") or 0) for r in runs)
    miss = sum(int(r.get("miss") or 0) for r in runs)
    mx = max(r["response"]["max_us"] for r in runs)
    return {"q99_us": q99, "n": n, "miss": miss, "max_us": mx,
            "rounds": len(runs), "per_round_p99": sorted(p99)}


def j_of(stats, beta, boundary_us=0.0, alpha=1.0):
    if stats is None:
        return None
    return round(alpha * stats["q99_us"] + beta * (stats["miss"] / float(stats["n"]))
                 + boundary_us, 3)


def verdict_of(stats, deadline_us, horizon_s, period_us):
    """与 sched_aware_cost 同口径的四档，用在验证轮上。"""
    if stats is None:
        return sac.VERDICT_NO_EVIDENCE, "无数据"
    if deadline_us is None:
        return sac.VERDICT_NO_EVIDENCE, "无 deadline"
    if stats["miss"] > 0:
        return (sac.VERDICT_INFEASIBLE,
                "验证轮 %d/%d 超期（最差 %.1f µs）"
                % (stats["miss"], stats["n"], stats["max_us"]))
    ci = do.wilson_interval(0, stats["n"])[1]
    if horizon_s is None:
        return sac.VERDICT_CANNOT_CERTIFY, "未给 horizon"
    cycles = float(horizon_s) * 1e6 / float(period_us)
    exp_upper = ci * cycles
    if exp_upper >= 1.0:
        return (sac.VERDICT_CANNOT_CERTIFY,
                "验证轮 0/%d 超期，但上界 %.1f 次/h" % (stats["n"], exp_upper))
    return sac.VERDICT_FEASIBLE, "验证轮 0/%d，上界 %.2f 次/h" % (stats["n"], exp_upper)


def run_fold(cells, stkp, cands, env, boundary, meta, train_rounds, val_rounds,
             deadline_us, horizon_s, betas, period_us):
    prof = profile_from(cells, train_rounds, env, boundary, meta)
    # 用「全量画像」把候选映射到 scenario（形状匹配与轮次无关）
    full = profile_from(cells, set(range(10)), env, boundary, meta)
    cand_scen = {}
    for i, c in enumerate(cands):
        shape = sac._cand_shape(c)
        hit = do.deployment_for(full, c.get("domain") or
                                ((c.get("plan") or {}).get("meta") or {}).get("platform"),
                                shape)
        cand_scen[i] = hit[0] if hit else None

    out = {"train_rounds": sorted(train_rounds),
           "val_rounds": sorted(val_rounds), "by_beta": {}}
    for beta in betas:
        dec = do.decide(cands, prof, alpha=1.0, beta=beta, gamma=0.0,
                        deadline_us=deadline_us)
        chosen = dec.get("chosen")
        rec = {
            "n_rankable_train": dec.get("n_rankable"),
            "chosen_train": None if not chosen else {
                "label": chosen.get("label"), "sched": chosen.get("sched"),
                "J_train": chosen.get("J_us"),
            },
        }
        if chosen:
            idx = chosen.get("cand_index")
            scen = cand_scen.get(idx)
            st = stats_on(cells, scen, chosen["sched"], val_rounds) if scen else None
            rec["chosen_val"] = st
            rec["chosen_J_val"] = j_of(st, beta)
            v, why = verdict_of(st, deadline_us, horizon_s, period_us)
            rec["verdict_val"] = v
            rec["verdict_val_why"] = why
            # 训练轮上同一组合的结论，用于看是否翻转
            st_tr = stats_on(cells, scen, chosen["sched"], train_rounds) if scen else None
            vt, why_t = verdict_of(st_tr, deadline_us, horizon_s, period_us)
            rec["verdict_train"] = vt
            rec["verdict_flip"] = (vt != v)
            rec["verdict_train_why"] = why_t
        # oracle：在验证轮上重算所有可排名候选
        rank = []
        for i, c in enumerate(cands):
            scen = cand_scen.get(i)
            if not scen:
                continue
            for sched in (do.SCHED_CLASSES):
                st = stats_on(cells, scen, sched, val_rounds)
                jv = j_of(st, beta)
                if jv is None:
                    continue
                rank.append({"label": "%s@%s" % (c.get("id"), sched),
                             "cand_index": i, "sched": sched,
                             "J_val": jv, "stats": st,
                             "deployment_ready": bool(c.get("deployment_ready"))})
        rank = [r for r in rank if r["deployment_ready"]]
        rank.sort(key=lambda r: r["J_val"])
        rec["oracle_val"] = rank[0] if rank else None
        if chosen and rank:
            rec["agree"] = (rank[0]["cand_index"] == chosen.get("cand_index")
                            and rank[0]["sched"] == chosen.get("sched"))
            rec["regret_us"] = round(rec["chosen_J_val"] - rank[0]["J_val"], 3)
        rec["val_table"] = rank[:5]
        out["by_beta"]["%g" % beta] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--stkp", required=True)
    ap.add_argument("--env", required=True)
    ap.add_argument("--deadline", type=float, default=1000.0)
    ap.add_argument("--period", type=float, default=1000.0)
    ap.add_argument("--horizon-s", dest="horizon_s", type=float, default=3600.0)
    ap.add_argument("--betas", nargs="+", default=["0", "1e5"])
    ap.add_argument("--load", type=int, default=0)
    ap.add_argument("--extra-shapes", action="append", default=[],
                    help="追加形状 json（如 pr17 mixed_plan）；scenario 不得与主表重名")
    ap.add_argument("--boundary",
                    default=os.path.join(KF, "docs", "pr10c_boundary",
                                         "boundary.json"),
                    help="PR-10C 边界实测；禁止在脚本里写死 t_ioctl")
    ap.add_argument("--protocol", default="loo", choices=("half", "loo"),
                    help="half=对半两折；loo=留一轮（10 折，样本量更大）")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    cells = collect_cells(a.shapes, a.baseline, load=a.load,
                          extra_shapes=a.extra_shapes)
    if not cells:
        print("没有可用 cell", file=sys.stderr)
        return 2
    stkp = load_json(a.stkp)
    cands = [c for c in (stkp.get("candidates") or []) if c.get("plan")]
    env = load_json(a.env)
    try:
        boundary = do.boundary_from_file(a.boundary)
    except do.TermMissing as exc:
        print("边界文件不可用：%s" % exc, file=sys.stderr)
        return 2
    meta = {"period_us": a.period, "deadline_us": a.deadline,
            "pin_cpu": 3, "cycles_per_round": 5000, "repeat": 5,
            "scheds": list(do.SCHED_CLASSES), "load": a.load}
    allr = set(range(10))
    if a.protocol == "half":
        folds = {
            "A": (set(range(0, 5)), set(range(5, 10))),
            "B": (set(range(5, 10)), set(range(0, 5))),
        }
        proto = "每折 5 轮建画像 / 另 5 轮检验；双向两折（A：0-4→5-9，B：反向）"
    else:
        folds = {"LOO-r%02d" % i: (allr - {i}, {i}) for i in range(10)}
        proto = "留一轮：9 轮建画像、被留的那一轮检验，共 10 折"
    betas = [float(b) for b in a.betas]
    doc = {
        "kind": "pr15_holdout", "version": 1,
        "load": a.load, "deadline_us": a.deadline, "horizon_s": a.horizon_s,
        "betas": betas,
        "protocol": proto,
        "claim": "只回答「换一批轮次，选择是否还成立」；两折不足以给置信度",
        "scenarios": sorted(cells),
        "extra_shapes": a.extra_shapes,
        "boundary": {
            "source_log": boundary["t_ioctl_us"].get("source_log"),
            "source_sha256": boundary["t_ioctl_us"].get("source_sha256"),
            "value_us": boundary["t_ioctl_us"].get("value"),
        },
        "folds": {},
    }
    for name, (tr, va) in folds.items():
        doc["folds"][name] = run_fold(cells, stkp, cands, env, boundary, meta,
                                      tr, va, a.deadline, a.horizon_s, betas,
                                      a.period)
    # 汇总：多折下的一致性、最大遗憾、结论翻转次数
    for bkey in ["%g" % b for b in betas]:
        recs = [doc["folds"][k]["by_beta"][bkey] for k in doc["folds"]]
        ag = [r for r in recs if r.get("agree") is not None]
        reg = [r["regret_us"] for r in ag]
        flips = [r for r in recs if r.get("verdict_flip")]
        doc.setdefault("summary", {})[bkey] = {
            "n_folds": len(recs), "n_with_choice": len(ag),
            "n_agree": sum(1 for r in ag if r["agree"]),
            "max_regret_us": max(reg) if reg else None,
            "regrets": reg,
            "n_verdict_flip": len(flips),
            "chosen_labels": sorted({str((r.get("chosen_train") or {}).get("label"))
                                     + "@" + str((r.get("chosen_train") or {}).get("sched"))
                                     for r in recs if r.get("chosen_train")}),
        }
    # ① 分辨率：最优与次优的差距 vs 被选组合的轮间波动。
    #    margin/noise ≫ 1 时，任何留出法都会「通过」——那种通过没有区分力。
    full = profile_from(cells, allr, env, boundary, meta)
    res = {"by_beta": {}}
    for beta in betas:
        dec = do.decide(cands, full, alpha=1.0, beta=beta, gamma=0.0,
                        deadline_us=a.deadline)
        rk = dec.get("ranked") or []
        row = {"n_rankable": len(rk)}
        if len(rk) >= 2:
            ch = rk[0]
            scen = None
            for i, c in enumerate(cands):
                if i == ch.get("cand_index"):
                    scen = None
                    break
            # 用候选索引追回 scenario
            for i, c in enumerate(cands):
                if i == ch.get("cand_index"):
                    hit = do.deployment_for(full, c.get("domain") or
                                            ((c.get("plan") or {}).get("meta") or {}).get("platform"),
                                            sac._cand_shape(c))
                    scen = hit[0] if hit else None
            noise = None
            if scen:
                pr = sorted(r["response"]["p99_us"] for r in
                            (cells[scen]["sched"].get(ch["sched"]) or [])
                            if r.get("n") and r.get("valid") is not False)
                if pr:
                    noise = {"min": pr[0], "max": pr[-1], "spread": pr[-1] - pr[0]}
            margin = round(rk[1]["J_us"] - rk[0]["J_us"], 3)
            row.update({
                "best": "%s@%s" % (rk[0].get("candidate_id"), rk[0].get("sched")),
                "second": "%s@%s" % (rk[1].get("candidate_id"), rk[1].get("sched")),
                "margin_us": margin,
                "noise_p99_us": noise,
                "margin_over_noise": (round(margin / noise["spread"], 2)
                                      if noise and noise["spread"] else None),
                "note": "margin/noise 越大，留出法越必然通过；"
                        "≈1 才真正在检验选择",
            })
        res["by_beta"]["%g" % beta] = row
    doc["resolution"] = res

    # ② 单轮判定稳定性：只用一轮做期限结论，10 轮里会翻转几次？
    #    这是「多轮重复」是否必要的直接证据。
    stab = {}
    for name, c in sorted(cells.items()):
        for sched in c["sched"]:
            vs = []
            for i in sorted(allr):
                st = stats_on(cells, name, sched, {i})
                v, _ = verdict_of(st, a.deadline, a.horizon_s, a.period)
                vs.append(v)
            tally = {}
            for v in vs:
                tally[v] = tally.get(v, 0) + 1
            stab["%s@%s" % (name, sched)] = {
                "per_round_verdict": vs, "tally": tally,
                "stable": len(tally) == 1,
            }
    doc["single_round_stability"] = stab
    dump_json(a.out, doc)

    print("协议：%s（load=%d, deadline=%g µs）" % (doc["protocol"], a.load, a.deadline))
    for name in sorted(doc["folds"]):
        f = doc["folds"][name]
        print("\n折 %s：训练 %s / 验证 %s" % (name, f["train_rounds"], f["val_rounds"]))
        for bkey, rec in sorted(f["by_beta"].items()):
            ch = rec.get("chosen_train") or {}
            orc = rec.get("oracle_val") or {}
            print("  β=%-5s 训练选 %-28s @%-7s J_tr=%s" % (
                bkey, str(ch.get("label"))[:28], ch.get("sched"), ch.get("J_train")))
            print("         验证 oracle %-24s @%-7s J_val=%s  一致=%s  regret=%s µs" % (
                str(orc.get("label"))[:24], orc.get("sched"), orc.get("J_val"),
                rec.get("agree"), rec.get("regret_us")))
            if a.protocol == "half":
                print("         期限结论  训练=%s → 验证=%s%s" % (
                    rec.get("verdict_train"), rec.get("verdict_val"),
                    "  ⚠ 翻转" if rec.get("verdict_flip") else ""))
    print("\n汇总（%d 折）：" % len(doc["folds"]))
    for bkey, r in sorted((doc.get("resolution") or {}).get("by_beta", {}).items()):
        print("  分辨率 β=%-6s 最优 %s vs 次优 %s：margin=%s µs，"
              "轮间 p99 极差=%s µs，margin/noise=%s" % (
                  bkey, r.get("best"), r.get("second"), r.get("margin_us"),
                  (r.get("noise_p99_us") or {}).get("spread"),
                  r.get("margin_over_noise")))
    print("\n单轮判定稳定性（只拿一轮出期限结论）：")
    for k, v in sorted((doc.get("single_round_stability") or {}).items()):
        print("  %-28s %s%s" % (k, v["tally"],
                                "" if v["stable"] else "  ⚠ 逐轮不一致"))
    for bkey, sm in sorted((doc.get("summary") or {}).items()):
        print("  β=%-6s 有选择 %d/%d 折，选对 %d 折，最大遗憾 %s µs，结论翻转 %d 次" % (
            bkey, sm["n_with_choice"], sm["n_folds"], sm["n_agree"],
            sm["max_regret_us"], sm["n_verdict_flip"]))
        print("           选中组合：%s" % ", ".join(sm["chosen_labels"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
