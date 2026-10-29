# -*- coding: utf-8 -*-
"""PR-18A：反馈画像。三层对象并列，后一层不得覆盖前一层。

    static_prediction  —— STKP 编译期 R̂（可含 scenario 钳定）
    measurement        —— 该 (形状, 调度类, 负载) 的实测 Q99 / R_miss
    calibration        —— 本模块不填写；PR-18B 另写，禁止回写前两层

匹配键：源码哈希 + plan 哈希 + 域 + 成员 + 相位 + 释放次数 + 调度类 + 负载 + 平台。
load=0 与 load=4 是不同条件，残差不得混池。缺测 → insufficient_evidence，
不得把别的形状/调度类/负载的数借过来。Δ = Q99 − R̂ 是经验误差，不是 WCET。
"""
from __future__ import print_function

import copy

from deploy_objective import (
    SCHED_CLASSES, _evidence_ok, _member_key, _n_releases,
    deployment_for, sha256_file,
)

KIND = "feedback_profile"
VERSION = 1
CLAIM = ("经验预测误差，不是 WCET；校准层为空直到 PR-18B；"
         "不得用静默残差预测加压")


def platform_id(env):
    env = env or {}
    host = env.get("host") or {}
    kern = env.get("kernel") or {}
    return "|".join([
        str(host.get("model") or host.get("hostname") or "?"),
        str(kern.get("release") or "?"),
        str(kern.get("preempt_class") or "?"),
    ])


def _phases(groups):
    out = []
    for g in groups or []:
        ph = g.get("release_phase_us")
        if ph is None:
            ph = g.get("phase_us")
        out.append(round(float(ph or 0.0), 3))
    return out


def cand_shape(cand):
    groups = (cand.get("plan") or {}).get("groups") or []
    fused = cand.get("fused")
    order = (cand.get("plan") or {}).get("order") or []
    if fused is None:
        fused = len(groups) < len(order) if order else False
    return {
        "n_groups": len(groups),
        "fused": bool(fused),
        "n_releases": _n_releases(groups),
        "members": [g.get("members") or [] for g in groups],
        "phases_us": _phases(groups),
        "n_crossings": int((cand.get("cost") or {}).get("handoff_channels") or 0),
    }


def match_key(cand, sched, load, env, src_sha=None, plan_sha=None):
    sh = cand_shape(cand)
    domain = cand.get("domain") or ((cand.get("plan") or {}).get("meta") or {}
                                    ).get("platform")
    return {
        "src_sha256": src_sha,
        "plan_sha256": plan_sha,
        "domain": domain,
        "members": [list(m) for m in (_member_key(sh["members"]) or ())],
        "phases_us": sh["phases_us"],
        "n_releases": sh["n_releases"],
        "n_groups": sh["n_groups"],
        "fused": sh["fused"],
        "sched": sched,
        "load": int(load or 0),
        "platform": platform_id(env),
    }


def static_from_cand(cand):
    cost = cand.get("cost") or {}
    rhat = cost.get("predicted_R_us")
    if rhat is None:
        rhat = (cand.get("plan") or {}).get("cost", {}).get("predicted_R_us")
    return {
        "predicted_R_us": None if rhat is None else float(rhat),
        "compute_us": cost.get("compute_us"),
        "release_us": cost.get("release_us"),
        "handoff_us": cost.get("handoff_us"),
        "handoff_channels": cost.get("handoff_channels"),
        "grade": cost.get("grade"),
        "evidence": list(cost.get("evidence") or []),
        "ioctl_model": cost.get("ioctl_model"),
        "note": "scenario/cli 钳定可出现在 evidence 里；那种 R̂ 不是实测",
    }


def measurement_from_profile(profile, cand, sched):
    """精确形状匹配。匹配不到或格子缺测 → insufficient_evidence。"""
    domain = cand.get("domain") or ((cand.get("plan") or {}).get("meta") or {}
                                    ).get("platform")
    hit = deployment_for(profile, domain, cand_shape(cand))
    if not hit:
        return {
            "status": "insufficient_evidence",
            "reason": "profile:该形状未测",
            "deployment": None,
        }
    name, blk = hit
    cell = (blk.get("sched") or {}).get(sched)
    if not cell or cell.get("evidence") == "missing":
        return {
            "status": "insufficient_evidence",
            "reason": "profile:调度类未测(%s/%s)" % (name, sched),
            "deployment": name,
        }
    q = cell.get("q99_resp_us")
    m = cell.get("miss_rate")
    if not _evidence_ok(q) or not _evidence_ok(m, need=("value",)):
        return {
            "status": "insufficient_evidence",
            "reason": "Q99 或 R_miss 无实测",
            "deployment": name,
        }
    tail = cell.get("tail") or {}
    return {
        "status": "measured",
        "deployment": name,
        "q99_resp_us": float(q["value"]),
        "q99_rounds": q.get("rounds"),
        "q99_statistic": q.get("statistic"),
        "q99_across_rounds": q.get("across_rounds"),
        "miss_rate": float(m["value"]),
        "n_miss": cell.get("n_miss"),
        "n_cycles": cell.get("n_cycles"),
        "rounds": cell.get("rounds"),
        "resp_max_worst_us": tail.get("resp_max_worst_us"),
        "exec_mode": (blk.get("shape") or {}).get("exec_mode"),
    }


def error_from(static, meas):
    """Δ = Q99 − R̂。分位数不可加，禁止把阶段 p99 相加冒充这一项。"""
    if (meas or {}).get("status") != "measured":
        return None
    rhat = (static or {}).get("predicted_R_us")
    q99 = meas.get("q99_resp_us")
    if rhat is None or q99 is None:
        return None
    return {
        "delta_q99_minus_Rhat_us": round(float(q99) - float(rhat), 3),
        "ratio_q99_over_Rhat": (None if float(rhat) == 0
                                else round(float(q99) / float(rhat), 4)),
        "note": "经验残差；不得当作 WCET 裕量，也不得跨负载套用",
    }


def empty_calibration():
    return {
        "status": "not_computed",
        "model": None,
        "note": "PR-18A 只建误差表；校准见 PR-18B，且不得覆盖 static/measurement",
    }


def attach_layers(rec, static, meas, calib=None):
    """写三层。传入 calib 也只放 calibration，绝不改 static/measurement。"""
    out = copy.deepcopy(rec)
    out["static_prediction"] = copy.deepcopy(static)
    out["measurement"] = copy.deepcopy(meas)
    out["calibration"] = copy.deepcopy(calib) if calib is not None else empty_calibration()
    return out


def build_report(stkp, profiles_by_load, env, hashes=None, scheds=None):
    """profiles_by_load: {load_int: sched_profile}。不同 load 分表。"""
    hashes = hashes or {}
    scheds = tuple(scheds) if scheds else SCHED_CLASSES
    cands = [c for c in (stkp.get("candidates") or []) if c.get("plan")]
    rows = []
    for load, prof in sorted((profiles_by_load or {}).items()):
        load = int(load)
        for i, cand in enumerate(cands):
            static = static_from_cand(cand)
            for sched in scheds:
                key = match_key(cand, sched, load, env,
                                src_sha=hashes.get("src_sha256"),
                                plan_sha=hashes.get("plan_sha256"))
                meas = measurement_from_profile(prof, cand, sched)
                rec = {
                    "cand_index": i,
                    "candidate_id": cand.get("id"),
                    "deployment_ready": bool(cand.get("deployment_ready")),
                    "match_key": key,
                }
                rec = attach_layers(rec, static, meas)
                rec["error"] = error_from(rec["static_prediction"],
                                          rec["measurement"])
                rows.append(rec)
    return {
        "kind": KIND,
        "version": VERSION,
        "claim": CLAIM,
        "hashes": hashes,
        "platform": platform_id(env),
        "loads": sorted(int(x) for x in (profiles_by_load or {})),
        "n_rows": len(rows),
        "n_measured": sum(1 for r in rows
                          if (r.get("measurement") or {}).get("status") == "measured"),
        "n_insufficient": sum(1 for r in rows
                              if (r.get("measurement") or {}).get("status")
                              == "insufficient_evidence"),
        "rows": rows,
        "summary": summarize(rows),
    }


def summarize(rows):
    """按释放结构 / 调度类 / 是否跨域分组，仍按 load 切开。"""
    buckets = {}
    for r in rows:
        err = r.get("error")
        if not err:
            continue
        mk = r.get("match_key") or {}
        key = (int(mk.get("load") or 0), mk.get("sched"),
               int(mk.get("n_releases") or 0),
               bool((r.get("static_prediction") or {}).get("handoff_channels")))
        b = buckets.setdefault(key, {"n": 0, "deltas": []})
        b["n"] += 1
        b["deltas"].append(err["delta_q99_minus_Rhat_us"])
    out = []
    for (load, sched, nrel, xdom), b in sorted(buckets.items()):
        ds = sorted(b["deltas"])
        mid = ds[len(ds) // 2] if len(ds) % 2 else 0.5 * (ds[len(ds) // 2 - 1]
                                                          + ds[len(ds) // 2])
        out.append({
            "load": load, "sched": sched, "n_releases": nrel,
            "cross_domain": xdom, "n": b["n"],
            "delta_min_us": ds[0], "delta_max_us": ds[-1],
            "delta_median_us": round(mid, 3),
        })
    return out


def file_hash(path):
    try:
        return sha256_file(path)
    except OSError:
        return None
