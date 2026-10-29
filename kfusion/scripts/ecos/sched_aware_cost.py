# -*- coding: utf-8 -*-
"""调度感知的成本与可行性 v2：把「编译合法 / 模型可行 / 实测期限」三件事分开。

审查指出的脱节：验证器通过（legal）、模型预测可行（R̂<D）都会被读成「满足硬实时」，
而 PR-10E 实测显示 CFS 下照样超期。根因是原来的 R̂ 只有一个标量，且不含调度等待。

v2 的做法：

    R̂ = L̂_release(s) + Ĉ_exec        ← 两项都可来自**实测分解**，不是再加一遍
    C_boundary 不重复加                ← Q99 里已含跨界往返（PR-11 的 γ=0 规则）

三种结论，不许混：

  1. `compile_legal`      —— 语义/结构合法（来自 verify，不是时间判断）
  2. `model_feasible`     —— R̂_model ≤ deadline（旧成本模型的判断，保留作对照）
  3. `deadline_verdict`   —— 只认实测：观测到超期 / 未观测到但不能背书 / 证据不足

`deadline_verdict` 的四档：
  · `infeasible_observed`  —— 实测到 n>0 次超期，直接判不可行（不必谈概率）
  · `cannot_certify`       —— 0 次超期，但 Wilson 上界 × 任务时长内的拍数 ≥ 1
  · `empirically_feasible` —— 0 次超期且上界足够小（给 horizon 与上界，不写「证明」）
  · `insufficient_evidence`—— 形状/调度类/边界单价缺测，任何结论都不给

**本模块永远不返回「可调度」**。它最多说「在这些观测下没看到超期，残余风险上界是 X」。
"""
from __future__ import print_function

import io
import json

try:
    from deploy_objective import (
        deployment_for, t_ioctl as do_t_ioctl, TermMissing, _n_releases,
    )
except ImportError:  # 作为包内模块被导入时
    from .deploy_objective import (
        deployment_for, t_ioctl as do_t_ioctl, TermMissing, _n_releases,
    )

VERDICT_INFEASIBLE = "infeasible_observed"
VERDICT_CANNOT_CERTIFY = "cannot_certify"
VERDICT_FEASIBLE = "empirically_feasible"
VERDICT_NO_EVIDENCE = "insufficient_evidence"

CLAIM = ("基于实测画像的可行性筛查：只回答「在已观测的 N 拍里有没有超期、"
         "残余风险上界多大」，不宣称硬实时可调度性，也不是 WCET")


def _cand_shape(cand):
    groups = (cand.get("plan") or {}).get("groups") or []
    order = (cand.get("plan") or {}).get("order") or []
    fused = cand.get("fused")
    if fused is None:
        fused = len(groups) < len(order)
    return {
        "n_groups": len(groups),
        "fused": bool(fused),
        "n_releases": _n_releases(groups),
        "members": [g.get("members") or [] for g in groups],
    }


def _meas_block(cell, sched):
    return {
        "q99_resp_us": (cell.get("q99_resp_us") or {}).get("value"),
        "start_lat_p99_us": ((cell.get("tail") or {})
                             .get("start_lat_p99_median_us")),
        "start_lat_p50_us": ((cell.get("tail") or {})
                             .get("start_lat_p50_median_us")),
        "resp_max_us": ((cell.get("tail") or {}).get("resp_max_worst_us")),
        "misses": (cell.get("miss_rate") or {}).get("count"),
        "n_cycles": (cell.get("miss_rate") or {}).get("n_cycles"),
        "rounds": cell.get("rounds"),
        "miss_rate_upper": ((cell.get("miss_rate") or {}).get("ci95_wilson")
                            or [None, None])[1],
        "sched": sched,
    }


def estimate(cand, profile, deadline_us=None, horizon_s=None):
    """对一个 (候选, 调度类) 出三列结论。缺证据时只给 insufficient_evidence。"""
    shape = _cand_shape(cand)
    domain = cand.get("domain") or ((cand.get("plan") or {}).get("meta") or {}
                                    ).get("platform")
    out = {
        "candidate_id": cand.get("id"),
        "domain": domain,
        "shape": shape,
        "compile_legal": bool(cand.get("legal")),
        "deadline_us": deadline_us,
        "horizon_s": horizon_s,
        "model": None,
        "measured": [],
        "missing": [],
        "claim": CLAIM,
    }
    cost = cand.get("cost") or {}
    r_hat_model = cost.get("predicted_R_us")
    out["model"] = {
        "R_hat_us": r_hat_model,
        "compute_us": cost.get("compute_us"),
        "release_us": cost.get("release_us"),
        "boundary_us": cost.get("handoff_us"),
        "evidence": cost.get("evidence"),
        "feasible": (None if r_hat_model is None or deadline_us is None
                     else bool(float(r_hat_model) <= float(deadline_us))),
        "note": "旧成本模型：R̂=ΣC+ΣL+ΣH，单标量，不含调度等待的实测分布",
    }

    if not shape["n_groups"]:
        out["missing"].append("候选无组结构")
        out["deadline_verdict"] = VERDICT_NO_EVIDENCE
        return out
    hit = deployment_for(profile, domain, shape)
    if hit is None:
        out["missing"].append("该形状未测（%s, %d 组, 融合=%s）"
                              % (domain, shape["n_groups"], shape["fused"]))
        out["deadline_verdict"] = VERDICT_NO_EVIDENCE
        return out
    dep, blk = hit
    out["deployment"] = dep
    cells = blk.get("sched") or {}
    if not cells:
        out["missing"].append("画像无调度类")
        out["deadline_verdict"] = VERDICT_NO_EVIDENCE
        return out

    # 边界单价只在“用模型 exec”时才需要；实测分解模式不重复加
    unit = None
    try:
        unit, _slot = do_t_ioctl(profile)
    except TermMissing:
        unit = None

    worst = None
    for sched, cell in sorted(cells.items()):
        m = _meas_block(cell, sched)
        rec = {"sched": sched, "measured": m}
        if m["q99_resp_us"] is None or m["n_cycles"] is None:
            rec["deadline_verdict"] = VERDICT_NO_EVIDENCE
            rec["why"] = "画像缺 Q99 或拍数"
            out["measured"].append(rec)
            continue
        # R̂ 的实测分解：L̂(p99) + 测得 work(p99)。work 只在形状补测里存了；
        # 没有就用 Q99 反推会变成自证，所以宁可标 insufficient。
        l99 = m["start_lat_p99_us"]
        exec99 = (cell.get("work_p99_us") or {}).get("value")
        if l99 is not None and exec99 is not None:
            rec["reconstruction"] = {
                "R_hat_us": round(float(l99) + float(exec99), 3),
                "start_lat_p99_us": l99, "exec_p99_us": exec99,
                "gap_vs_q99_us": round(float(l99) + float(exec99)
                                       - float(m["q99_resp_us"]), 3),
                "note": "分解自同一批实测；差值应≈0，明显非 0 说明两项不同源",
            }
        else:
            rec["reconstruction"] = {
                "R_hat_us": None,
                "note": "缺 work p99（plan 模式只留响应分位数）→ 不做分解，"
                        "避免用 Q99 反推 Q99 自证",
            }

        if deadline_us is None:
            rec["deadline_verdict"] = VERDICT_NO_EVIDENCE
            rec["why"] = "没有 deadline"
        elif m["misses"] and m["misses"] > 0:
            rec["deadline_verdict"] = VERDICT_INFEASIBLE
            rec["why"] = ("实测 %d/%d 拍超期 %s µs（最差 %.1f µs）"
                          % (m["misses"], m["n_cycles"], deadline_us,
                             m["resp_max_us"] or -1))
            rec["over_deadline_max_us"] = m["resp_max_us"]
        else:
            up = m["miss_rate_upper"]
            if up is None:
                rec["deadline_verdict"] = VERDICT_NO_EVIDENCE
                rec["why"] = "缺超期率的区间上界"
            elif horizon_s is not None:
                period = _period_us(profile, deadline_us)
                cycles = (float(horizon_s) * 1e6 / float(period)
                          if period else None)
                exp_upper = (up * cycles) if cycles else None
                rec["horizon"] = {
                    "period_us": period, "cycles": cycles,
                    "expected_misses_upper": exp_upper,
                }
                if exp_upper is not None and exp_upper >= 1.0:
                    rec["deadline_verdict"] = VERDICT_CANNOT_CERTIFY
                    rec["why"] = (
                        "已观测 0 超期，但 %s 内上界 %.2f 次超期（率上界 %.2e）"
                        % (_fmt_h(horizon_s), exp_upper, up))
                else:
                    rec["deadline_verdict"] = VERDICT_FEASIBLE
                    rec["why"] = ("已观测 0/%d 超期，%s 内上界 %.2f 次"
                                  % (m["n_cycles"], _fmt_h(horizon_s),
                                     exp_upper if exp_upper is not None else -1))
            else:
                rec["deadline_verdict"] = VERDICT_CANNOT_CERTIFY
                rec["why"] = ("已观测 0/%d 超期；未给 horizon，无法把"
                              "率上界折成次数" % m["n_cycles"])
        if unit is not None:
            rec["boundary_unit_us"] = unit
        out["measured"].append(rec)
        if worst is None or _rank(rec) < _rank(worst):
            worst = rec
    if worst is None:
        out["deadline_verdict"] = VERDICT_NO_EVIDENCE
        out["missing"].append("没有任何调度类可用")
    else:
        out["deadline_verdict"] = worst["deadline_verdict"]
        out["worst_sched"] = worst["sched"]
        out["why"] = worst.get("why")
    return out


def _fmt_h(s):
    return "%.0fs" % float(s) if float(s) < 3600 else "%.1fh" % (float(s) / 3600)


def _period_us(profile, deadline_us):
    p = (profile.get("measure_env") or {}).get("period_us")
    if p:
        return float(p)
    d = (profile.get("measure_env") or {}).get("deadline_us")
    return float(d) if d else (float(deadline_us) if deadline_us else None)


_RANK = {VERDICT_INFEASIBLE: 0, VERDICT_CANNOT_CERTIFY: 1,
         VERDICT_FEASIBLE: 2, VERDICT_NO_EVIDENCE: 3}


def _rank(rec):
    return _RANK.get(rec.get("deadline_verdict"), 9)


def evaluate_all(cands, profile, deadline_us=None, horizon_s=None):
    rows = [estimate(c, profile, deadline_us, horizon_s) for c in cands]
    counts = {}
    for r in rows:
        counts[r["deadline_verdict"]] = counts.get(r["deadline_verdict"], 0) + 1
    return {
        "kind": "pr14_sched_aware_verify",
        "version": 2,
        "claim": CLAIM,
        "deadline_us": deadline_us,
        "horizon_s": horizon_s,
        "counts": counts,
        "rows": rows,
        "columns_note": ("compile_legal=语义合法；model.feasible=旧成本模型判断；"
                         "deadline_verdict=只认实测，四档见模块 docstring"),
    }
