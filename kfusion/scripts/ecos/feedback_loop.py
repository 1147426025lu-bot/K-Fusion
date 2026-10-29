# -*- coding: utf-8 -*-
"""PR-18C：校准结果 shadow 进部署决策，默认不改正在用的 chosen。

    chosen_before  —— 实测 J(d,s)（PR-11）
    calibration_id —— 残差表版本
    chosen_after   —— 用 Q̂99,cal 重排（缺校准格子按 mode 丢掉或回退实测）
    apply_status   —— accepted / rejected / unchanged

accepted 只表示「校准排序通过门禁且与 before 不同」。shadow 模式只记账，
不改 STKP chosen。--apply-calibration 才改 chosen，且仍要求 deployment_ready。
校准不能绕过 env / Q99 缺测 / 非 ready 的 fail-closed。
"""
from __future__ import print_function

import copy

import calibrate_cost as cc
import deploy_objective as do
import feedback_profile as fb

M2_DROP = "drop"
M3_FALLBACK = "measured"


def table_from_doc(doc):
    if not doc:
        return {}
    tab = doc.get("table")
    if isinstance(tab, dict):
        return copy.deepcopy(tab)
    out = {}
    for e in tab or []:
        kid = e.get("key_id")
        cal = e.get("calibration")
        if kid and cal:
            out[kid] = cal
    return out


def _choice(ch):
    if not ch:
        return None
    return {
        "candidate_id": ch.get("candidate_id") or ch.get("id"),
        "cand_index": ch.get("cand_index"),
        "sched": ch.get("sched"),
        "label": ch.get("label"),
        "J_us": ch.get("J_us"),
        "deployment": ch.get("deployment"),
    }


def _same(a, b):
    if not a or not b:
        return False
    return (a.get("cand_index") == b.get("cand_index")
            and a.get("sched") == b.get("sched"))


def overlay_decide(cands, profile, table, env, load, hashes,
                   alpha=1.0, beta=0.0, gamma=0.0, deadline=None,
                   fallback=M2_DROP, scheds=None):
    """在 decide() 的实测格子上叠校准 Q99，重算 J。不改 profile。"""
    base = do.decide(cands, profile, alpha=alpha, beta=beta, gamma=gamma,
                     deadline_us=deadline,
                     scheds=tuple(scheds) if scheds else do.SCHED_CLASSES)
    hashes = hashes or {}
    evals = []
    for e in copy.deepcopy(base.get("all_evals") or []):
        idx = e.get("cand_index")
        cand = cands[idx] if idx is not None and 0 <= idx < len(cands) else {}
        mk = fb.match_key(cand, e.get("sched"), load, env,
                          src_sha=hashes.get("src_sha256"),
                          plan_sha=hashes.get("plan_sha256"))
        calib = cc.lookup(table, mk)
        e["calibration_status"] = calib.get("status")
        e["calibration_id"] = calib.get("calibration_id")
        if not e.get("rankable"):
            evals.append(e)
            continue
        if calib.get("status") == "fitted":
            qcal = calib.get("q99_cal_us")
            static = {"predicted_R_us": (cand.get("cost") or {}).get("predicted_R_us")}
            via = cc.apply_q99(static, calib)
            if via is not None:
                qcal = via
            if qcal is None:
                e["rankable"] = False
                e.setdefault("missing", []).append("calibration:q99_cal 空")
            else:
                terms = e.setdefault("terms", {})
                e["q99_source"] = "calibrated"
                terms["Q99_resp_us"] = float(qcal)
                e["J_us"] = round(
                    alpha * float(qcal)
                    + beta * float(terms.get("R_miss") or 0.0)
                    + gamma * float(terms.get("C_boundary_us") or 0.0), 3)
        elif fallback == M3_FALLBACK and e.get("rankable"):
            e["q99_source"] = "measured_fallback"
        else:
            e["rankable"] = False
            e.setdefault("missing", []).append(
                "calibration:%s" % (calib.get("reason") or "insufficient"))
        evals.append(e)
    rankable = [e for e in evals if e.get("rankable") and e.get("deployment_ready")]
    rankable.sort(key=lambda x: (x["J_us"], x.get("cand_index") or 0))
    out = copy.deepcopy(base)
    out["all_evals"] = evals
    out["ranked"] = rankable
    out["n_rankable"] = len(rankable)
    out["chosen"] = None if not rankable else _choice(rankable[0])
    if out["chosen"] and rankable:
        out["chosen"]["deployment"] = rankable[0].get("deployment")
        out["chosen"]["J_us"] = rankable[0].get("J_us")
        out["chosen"]["q99_source"] = rankable[0].get("q99_source")
    out["fallback"] = fallback
    return out


def apply_status(before, after, after_ok):
    if not after_ok or after is None:
        return "rejected"
    if _same(before, after):
        return "unchanged"
    return "accepted"


def shadow_reselect(cands, profile, table, env, load, hashes,
                    alpha=1.0, beta=0.0, gamma=0.0, deadline=None,
                    fallback=M2_DROP, scheds=None, apply=False):
    """返回记账结构。apply=True 才允许调用方改 chosen。"""
    before_doc = do.decide(cands, profile, alpha=1.0, beta=0.0, gamma=0.0,
                           deadline_us=deadline,
                           scheds=tuple(scheds) if scheds else do.SCHED_CLASSES)
    before = _choice(before_doc.get("chosen"))
    after_doc = overlay_decide(
        cands, profile, table, env, load, hashes,
        alpha=alpha, beta=beta, gamma=gamma, deadline=deadline,
        fallback=fallback, scheds=scheds)
    after = after_doc.get("chosen")
    after_ok = after is not None
    if after_ok:
        idx = after.get("cand_index")
        rec = cands[idx] if idx is not None and 0 <= idx < len(cands) else {}
        if not rec.get("deployment_ready"):
            after_ok = False
            after = None
    status = apply_status(before, after, after_ok)
    cid = None
    for v in (table or {}).values():
        if v.get("calibration_id"):
            cid = v.get("calibration_id")
            break
    rec = {
        "kind": "pr18_shadow",
        "calibration_id": cid or cc.CALIBRATION_ID,
        "load": int(load or 0),
        "fallback": fallback,
        "chosen_before": before,
        "chosen_after": after,
        "apply_status": status,
        "apply": bool(apply),
        "applied": bool(apply) and status == "accepted",
        "n_rankable_before": before_doc.get("n_rankable"),
        "n_rankable_after": after_doc.get("n_rankable"),
        "claim": ("shadow 默认不替换 chosen；apply 仅在 accepted 且 ready 时改。"
                  "非 WCET。"),
    }
    return rec, after_doc
