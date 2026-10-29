# -*- coding: utf-8 -*-
"""部署目标函数 J(d,s)：把「等调度」的实测尾部分布纳入部署决策。

审查给的形式化：
    J(d,s) = α·Q99_resp(d,s) + β·R_miss(d,s) + γ·C_boundary(d)

其中 s 是**调度类**（cfs / fifo99），不是部署 d 的属性。PR-10D 的结论是
4.7× 来自调度类而不是内核化，所以调度类必须与「分组×域」一起进决策空间，
不能先选部署再套一个固定时延。

三条不许含糊的边界：
  1. Q99 / R_miss 只能是**实测**，且必须带轮数与总拍数。缺测 = 该 (d,s)
     不可排名（fail-closed），不许用另一部署的数替代，不许当 0。
  2. R_miss 是 5 万拍里的事件计数，点估计不稳；区间用 Wilson，决策可切
     `--miss-bound upper` 取上界（保守）。
  3. Q99 已经是端到端响应，里面**已含**边界往返。γ>0 表达的是「多一次跨界
     的结构代价/故障面/审计成本」，不是再加一次时延。γ 默认 0，理由写进
     决策记录，避免重复计数被当成物理时延。

J 是加权标量化，权重是**策略输入**，量纲不齐（µs 与无量纲率）。它不是 WCET，
也不宣称硬实时可调度性——它只在「已通过 verify 的候选」之间排序。
"""
from __future__ import print_function

import io
import json
import math
import os

PROFILE_KIND = "sched_profile"
PROFILE_VERSION = 1

# 决策空间里允许的调度类；顺序仅影响并排列印，不影响选择
SCHED_CLASSES = ("cfs", "fifo99")


class TermMissing(Exception):
    """某一项没有可追溯实测值。"""


def _median(xs):
    ys = sorted(float(x) for x in xs)
    n = len(ys)
    if n == 0:
        raise TermMissing("空序列")
    m = n // 2
    return ys[m] if n % 2 else 0.5 * (ys[m - 1] + ys[m])


def _span(xs):
    ys = [float(x) for x in xs]
    return [min(ys), max(ys)]


def wilson_interval(k, n, z=1.96):
    """二项比例的 Wilson 区间。k 次超期 / n 拍。"""
    if n <= 0:
        raise TermMissing("n<=0")
    p = float(k) / float(n)
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    lo = max(0.0, centre - half)
    hi = min(1.0, centre + half)
    return [lo, hi]


def _evidence_ok(slot, need=("value",)):
    """只认 measured / test / pinned + 有轮数或拍数。其余一律不可用。"""
    if not isinstance(slot, dict):
        return False
    ev = slot.get("evidence")
    if ev not in ("measured", "test", "pinned"):
        return False
    for k in need:
        if slot.get(k) is None:
            return False
    has_n = any(slot.get(k) for k in ("rounds", "samples", "n_cycles"))
    if ev == "measured" and not has_n:
        return False
    return True


# --------------------------------------------------------------------------
# 画像生成：把 PR-10E-A 的 10 轮基线压成每 (部署, 调度类) 的一格
# --------------------------------------------------------------------------

def _cell_profile(runs):
    """一轮一格：跨轮取中位数，同时保留跨轮极差（极差本身是复现对象）。"""
    if not runs:
        raise TermMissing("无轮次")
    lat_p50 = [r["start_lat"]["p50_us"] for r in runs]
    lat_p99 = [r["start_lat"]["p99_us"] for r in runs]
    lat_max = [r["start_lat"]["max_us"] for r in runs]
    resp_p99 = [r["response"]["p99_us"] for r in runs]
    resp_max = [r["response"]["max_us"] for r in runs]
    n_cycles = sum(int(r.get("n") or 0) for r in runs)
    n_miss = sum(int(r.get("miss") or 0) for r in runs)
    if n_cycles <= 0:
        raise TermMissing("总拍数为 0")
    ci = wilson_interval(n_miss, n_cycles)
    return {
        "rounds": len(runs),
        "n_cycles": n_cycles,
        "n_miss": n_miss,
        # Q99 项：各轮 p99 的中位数＝「典型一轮」的 p99，不是 10 轮混池
        "q99_resp_us": {
            "value": round(_median(resp_p99), 3),
            "evidence": "measured",
            "rounds": len(runs),
            "n_cycles": n_cycles,
            "across_rounds": _span(resp_p99),
            "statistic": "median_of_round_p99",
            "note": "跨轮极差一并保留；混池 p99 会掩盖单轮劣化",
        },
        "miss_rate": {
            "value": n_miss / float(n_cycles),
            "evidence": "measured",
            "n_cycles": n_cycles,
            "rounds": len(runs),
            "count": n_miss,
            "ci95_wilson": [round(ci[0], 8), round(ci[1], 8)],
            "statistic": "pooled_over_rounds",
            "note": "事件数少时点估计不稳，可用 --miss-bound upper",
        },
        # 尾部风险四量分开存，不跨量下单一结论
        "tail": {
            "start_lat_p50_median_us": round(_median(lat_p50), 3),
            "start_lat_p50_range_us": [round(x, 3) for x in _span(lat_p50)],
            "start_lat_p99_median_us": round(_median(lat_p99), 3),
            "start_lat_p99_range_us": [round(x, 3) for x in _span(lat_p99)],
            "start_lat_max_worst_us": round(max(lat_max), 3),
            "resp_max_worst_us": round(max(resp_max), 3),
            "rounds_with_miss": sum(1 for r in runs if (r.get("miss") or 0) > 0),
        },
    }


# 基线里的场景名 → STKP 的域；profile 里显式写出，不靠猜
DEPLOYMENT_DOMAIN = {
    "user_only": "linux_userspace",
    "partial_kernel": "mixed",
}

# 实测时的**执行形状**。Q99 是形状相关的：融合会改执行结构，不能把
# 3 组不融合测出来的尾部借给 1 组全融合的候选。来源：PR-10E 流水线日志
# 「STKP chosen plan → L[stage_sample]|L[stage_pid]|L[stage_sink]」。
DEPLOYMENT_SHAPE = {
    "user_only": {
        "n_groups": 3, "fused": False,
        "members": [["stage_sample"], ["stage_pid"], ["stage_sink"]],
        "note": "gold：sample/pid/sink 三次顺序调用，全用户态",
    },
    "partial_kernel": {
        "n_groups": 3, "fused": False,
        "members": [["stage_sample"], ["stage_pid"], ["stage_sink"]],
        "note": "mixed：三段不融合，中间段经 ioctl 进内核",
    },
}


def aggregate_shapes(doc, env=None):
    """shapes.json → sched_profile 的 deployments（按形状索引）。

    与 aggregate_baseline 的区别：这里的形状来自构建产物回读的 plan.json，
    不是写死的假设。名字用形状名（fused_user / split_sp_k_user ...）。
    """
    cells = doc.get("cells") or []
    if not cells:
        raise TermMissing("shapes 无 cells")
    prof = {
        "kind": PROFILE_KIND,
        "version": 2,
        "source": doc.get("source"),
        "measure_env": {
            "pin_cpu": doc.get("pin_cpu"),
            "cycles_per_round": doc.get("cycles"),
            "repeat": doc.get("repeat"),
            "scheds": list(doc.get("scheds") or []),
            "deadline_us": doc.get("deadline_us"),
            "note": "形状补测用 driver 统计块（plan 模式不产逐拍 csv）；"
                    "分位数定义与 PR-10E-A 同源探针",
        },
        "deployments": {},
        "unmeasured": [],
        "env_evidence": env,
    }
    for cell in cells:
        if cell.get("status") != "ok":
            prof["unmeasured"].append({
                "scenario": cell.get("scenario"),
                "reason": "构建/采集失败：%s" % cell.get("status"),
            })
            continue
        sh = cell.get("shape") or {}
        if not sh.get("n_groups"):
            prof["unmeasured"].append({"scenario": cell.get("scenario"),
                                       "reason": "形状回读缺失"})
            continue
        scheds = {}
        for cond in cell.get("conditions") or []:
            s = cond.get("sched")
            if not s:
                continue
            runs = [r for r in (cond.get("runs") or []) if r.get("n")]
            try:
                scheds[s] = _cell_profile(runs)
            except TermMissing as exc:
                scheds[s] = {"evidence": "missing", "reason": str(exc)}
        doms = sh.get("domains") or []
        domain = "mixed" if len(set(doms)) > 1 else (
            doms[0] if doms else None)
        prof["deployments"][cell["scenario"]] = {
            "maps_to_domain": domain,
            "shape": {"n_groups": int(sh["n_groups"]),
                      "fused": bool(sh.get("fused")),
                      "members": sh.get("members"),
                      "note": cell.get("note")},
            "sched": scheds,
        }
    return prof


def merge_profiles(base, extra):
    """把两份画像的 deployments 合并；形状冲突直接报错，不静默覆盖。"""
    out = json.loads(json.dumps(base))
    out.setdefault("deployments", {})
    for name, blk in (extra.get("deployments") or {}).items():
        for other, ob in out["deployments"].items():
            if ob.get("maps_to_domain") != blk.get("maps_to_domain"):
                continue
            ka = _member_key((ob.get("shape") or {}).get("members"))
            kb = _member_key((blk.get("shape") or {}).get("members"))
            same = (ka == kb) if (ka is not None and kb is not None) else (
                (ob.get("shape") or {}).get("n_groups") ==
                (blk.get("shape") or {}).get("n_groups") and
                bool((ob.get("shape") or {}).get("fused")) ==
                bool((blk.get("shape") or {}).get("fused")))
            if same:
                raise SystemExit(
                    "画像形状冲突：%s 与 %s 同域且同组结构（%s, %s 组）"
                    % (other, name, blk.get("maps_to_domain"),
                       (blk.get("shape") or {}).get("n_groups")))
        out["deployments"][name] = blk
    out["unmeasured"] = (out.get("unmeasured") or []) + \
                        (extra.get("unmeasured") or [])
    if extra.get("env_evidence") and not out.get("env_evidence"):
        out["env_evidence"] = extra["env_evidence"]
    out["version"] = 2
    return out


def aggregate_baseline(doc, source=None, sha256=None, env=None):
    """baseline.json → sched_profile。没测过的部署不编造，写进 unmeasured。"""
    cells = doc.get("cells") or []
    if not cells:
        raise TermMissing("baseline 无 cells")
    env = doc.get("env") or {}
    profile = {
        "kind": PROFILE_KIND,
        "version": PROFILE_VERSION,
        "source": source,
        "source_sha256": sha256,
        "measure_env": {
            "pin_cpu": doc.get("pin_cpu"),
            "cycles_per_round": doc.get("cycles"),
            "repeat": doc.get("repeat"),
            "scheds": list(doc.get("scheds") or []),
            "kernel": env.get("kernel") or env.get("uname"),
            "note": "非 PREEMPT_RT；跨轮极差是复现对象，不是噪声",
        },
        "deployments": {},
        "unmeasured": [],
        "env_evidence": env,
    }
    for cell in cells:
        scen = cell.get("scenario")
        if scen not in DEPLOYMENT_DOMAIN:
            profile["unmeasured"].append({
                "scenario": scen,
                "reason": "未知场景，未映射到域，不参与 J",
            })
            continue
        scheds = {}
        for cond in cell.get("conditions") or []:
            s = cond.get("sched")
            if not s:
                continue
            try:
                scheds[s] = _cell_profile(cond.get("runs") or [])
            except TermMissing as exc:
                scheds[s] = {"evidence": "missing", "reason": str(exc)}
        profile["deployments"][scen] = {
            "maps_to_domain": DEPLOYMENT_DOMAIN[scen],
            "shape": dict(DEPLOYMENT_SHAPE.get(scen) or {}),
            "sched": scheds,
        }
    # STKP 里存在但本基线没测的部署：显式记录下来，fail-closed 时有话可说
    for dom, why in (
        ("linux_kernel", "PR-10E-A 基线网格未含内核自主部署（PR-10C2 是另一份"
                         "构建/口径，按「同一二进制只改一个变量」原则不得混用）"),
    ):
        if not any(v.get("maps_to_domain") == dom
                   for v in profile["deployments"].values()):
            profile["unmeasured"].append({"domain": dom, "reason": why})
    return profile


def load_profile(path):
    with io.open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("kind") != PROFILE_KIND:
        raise SystemExit("不是 %s：%s" % (PROFILE_KIND, path))
    return doc


def deployment_for_domain(profile, domain):
    """只按域查。多个形状同域时**不许猜**，返回 None 交给形状精确匹配。"""
    hits = [name for name, blk in (profile.get("deployments") or {}).items()
            if blk.get("maps_to_domain") == domain]
    return hits[0] if len(hits) == 1 else None


SHAPE_KEYS = ("n_groups", "fused")


def _member_key(members):
    """把组结构归一成可比较的键：组内顺序无关、组间顺序有关。"""
    if not members:
        return None
    try:
        return tuple(tuple(sorted(m)) for m in members)
    except TypeError:
        return None


def deployment_for(profile, domain, shape):
    """按 (域, 组结构) 精确匹配。返回 (name, block) 或 None。

    为什么必须精确：Q99 是执行形状相关的量。3 组不融合测出来的尾部借给
    1 组全融合的候选，等于把两个不同程序的数混在一起——文献里最容易被
    审出来的那种错。匹配不到就返回 None（不可排名），不许退而求其次。

    只用「组数」不够：`[sample+pid][sink]` 与 `[sample][pid+sink]` 都是
    2 组不融合，但相位跨度差一倍（实测 p99 462 vs 263 µs）。所以优先比
    组成员集合，成员缺失时才退回 (组数, 融合) 且要求唯一命中。
    """
    shape = shape or {}
    want_members = _member_key(shape.get("members"))
    hits = []
    for name, blk in (profile.get("deployments") or {}).items():
        if blk.get("maps_to_domain") != domain:
            continue
        sh = blk.get("shape") or {}
        if not sh:
            continue
        if want_members is not None:
            got = _member_key(sh.get("members"))
            if got is None or got != want_members:
                continue
            hits.append((name, blk))
            continue
        ok = True
        for k in SHAPE_KEYS:
            if shape.get(k) is None or sh.get(k) is None:
                ok = False
                break
            if k == "fused":
                same = bool(shape[k]) == bool(sh[k])
            else:
                same = int(shape[k]) == int(sh[k])
            if not same:
                ok = False
                break
        if ok:
            hits.append((name, blk))
    return hits[0] if len(hits) == 1 else None


def env_gate(profile, allow_incomplete=False):
    """环境证据门禁。返回 (ok, reason)。

    审查的 P0：画像必须说清是哪台机器/哪个内核/哪种抢占模式测的。
    没有这层，跨平台或 PREEMPT_RT 数据会被无标识地混进同一张表。
    """
    ev = profile.get("env_evidence")
    if not isinstance(ev, dict) or not ev.get("complete"):
        missing = (ev or {}).get("missing") or ["env_evidence 缺失"]
        if allow_incomplete:
            return True, "env_incomplete_allowed: %s" % ",".join(
                str(m) for m in missing)
        return False, "env_incomplete: %s" % ",".join(str(m) for m in missing)
    return True, "env_ok"


# --------------------------------------------------------------------------
# J 求值
# --------------------------------------------------------------------------

def t_ioctl(profile):
    """边界单价必须实测；返回 (value, evidence_slot)。"""
    slot = (profile.get("boundary") or {}).get("t_ioctl_us")
    if not _evidence_ok(slot):
        raise TermMissing("边界单价 T_ioctl 无实测")
    return float(slot["value"]), slot


def evaluate(profile, domain, sched, n_crossings,
             alpha=1.0, beta=0.0, gamma=0.0,
             miss_bound="point", deadline_us=None, cand_shape=None):
    """算一个 (部署, 调度类) 的 J。任何一项缺测 → 不可排名。"""
    dep = None
    rec = {
        "domain": domain,
        "deployment": None,
        "sched": sched,
        "n_crossings": int(n_crossings or 0),
        "alpha": alpha, "beta": beta, "gamma": gamma,
        "miss_bound": miss_bound,
        "terms": {},
        "J_us": None,
        "rankable": False,
        "missing": [],
    }
    if cand_shape is None:
        rec["missing"].append("形状未知：不能挑画像")
        return rec
    hit = deployment_for(profile, domain, cand_shape)
    if hit is None:
        rec["missing"].append(
            "profile:该形状未测（%s, 组数=%s, 融合=%s）"
            % (domain, cand_shape.get("n_groups"), cand_shape.get("fused")))
        return rec
    dep, blk = hit
    rec["deployment"] = dep
    cell = (blk.get("sched") or {}).get(sched)
    if not cell or cell.get("evidence") == "missing":
        rec["missing"].append("profile:调度类未测(%s/%s)" % (dep, sched))
        return rec

    q = cell.get("q99_resp_us")
    if not _evidence_ok(q):
        rec["missing"].append("Q99:无实测")
    else:
        rec["terms"]["Q99_resp_us"] = float(q["value"])
        rec["q99_provenance"] = {
            "rounds": q.get("rounds"),
            "across_rounds": q.get("across_rounds"),
            "statistic": q.get("statistic"),
        }

    m = cell.get("miss_rate")
    if not _evidence_ok(m, need=("value",)):
        rec["missing"].append("R_miss:无实测")
    else:
        ci = m.get("ci95_wilson") or [None, None]
        val = float(m["value"])
        if miss_bound == "upper":
            if ci[1] is None:
                rec["missing"].append("R_miss:取上界但无区间")
            else:
                val = float(ci[1])
        rec["terms"]["R_miss"] = val
        rec["miss_provenance"] = {
            "count": m.get("count"),
            "n_cycles": m.get("n_cycles"),
            "ci95_wilson": ci,
            "used": miss_bound,
        }

    cb, slot = None, None
    try:
        unit, slot = t_ioctl(profile)
    except TermMissing as exc:
        if rec["n_crossings"] > 0:
            rec["missing"].append("C_boundary:%s" % exc)
        else:
            # 跨界数为 0 时边界代价结构上就是 0，不需要单价实测
            rec["terms"]["C_boundary_us"] = 0.0
            rec["boundary_provenance"] = {
                "unit_us": None, "n_crossings": 0,
                "note": "无跨界，边界项恒 0；不需要单价实测",
            }
    else:
        cb = unit * rec["n_crossings"]
        rec["terms"]["C_boundary_us"] = round(cb, 3)
        rec["boundary_provenance"] = {
            "unit_us": unit,
            "n_crossings": rec["n_crossings"],
            "source_log": slot.get("source_log"),
            "p99_us": slot.get("p99_us"),
        }

    if rec["missing"]:
        return rec

    J = (alpha * rec["terms"]["Q99_resp_us"]
         + beta * rec["terms"]["R_miss"]
         + gamma * rec["terms"]["C_boundary_us"])
    rec["J_us"] = round(J, 3)
    rec["rankable"] = True
    rec["tail"] = cell.get("tail")
    if deadline_us is not None:
        tail = cell.get("tail") or {}
        rec["deadline_us"] = float(deadline_us)
        rec["worst_resp_over_deadline"] = bool(
            (tail.get("resp_max_worst_us") or 0) > float(deadline_us))
    return rec


def decide(candidates, profile, alpha=1.0, beta=0.0, gamma=0.0,
           miss_bound="point", deadline_us=None, scheds=SCHED_CLASSES,
           allow_incomplete_env=False):
    """对每个候选 d，在调度类上取最小 J；再全局选最小。

    返回结构里每个 (d,s) 都留痕，不只留赢家——否则「为什么没选它」
    在报告里无法复核。
    """
    ok, env_reason = env_gate(profile, allow_incomplete_env)
    env_note = {
        "ok": ok, "reason": env_reason,
        "kernel": ((profile.get("env_evidence") or {}).get("kernel") or {}
                   ).get("preempt_class"),
        "release": ((profile.get("env_evidence") or {}).get("kernel") or {}
                    ).get("release"),
        "host": ((profile.get("env_evidence") or {}).get("host") or {}
                 ).get("model"),
    }
    if not ok:
        return {
            "kind": "pr11_deploy_objective", "version": 1,
            "objective": "J(d,s)",
            "weights": {"alpha": alpha, "beta": beta, "gamma": gamma,
                        "miss_bound": miss_bound},
            "env": env_note,
            "blocked": env_reason,
            "n_rankable": 0, "n_evals": 0, "chosen": None,
            "ranked": [], "all_evals": [],
            "claim": "env_evidence_incomplete：没有平台身份的画像不得用于决策",
        }
    evals = []
    for idx, cand in enumerate(candidates):
        domain = cand.get("domain") or (cand.get("plan") or {}).get("meta", {}).get("platform")
        n_cross = (cand.get("cost") or {}).get("handoff_channels")
        if n_cross is None:
            n_cross = sum(1 for c in (cand.get("cost") or {}).get("channels") or [])
        per_sched = {}
        groups = (cand.get("plan") or {}).get("groups") or []
        cand_shape = {
            "n_groups": len(groups),
            "fused": bool(cand.get("fused")
                          if cand.get("fused") is not None
                          else len(groups) < len((cand.get("plan") or {}).get("order") or [])),
            "members": [g.get("members") or [] for g in groups],
        }
        for s in scheds:
            r = evaluate(profile, domain, s, n_cross,
                         alpha=alpha, beta=beta, gamma=gamma,
                         miss_bound=miss_bound, deadline_us=deadline_us,
                         cand_shape=cand_shape)
            r["candidate_id"] = cand.get("id")
            r["cand_index"] = idx
            r["cand_shape"] = cand_shape
            r["deployment_ready"] = bool(cand.get("deployment_ready"))
            r["label"] = "%s@%s" % (cand.get("id"), domain)
            per_sched[s] = r
            evals.append(r)
        best = None
        for s, r in per_sched.items():
            if not r["rankable"]:
                continue
            if best is None or (r["J_us"], s) < (best["J_us"], best["sched"]):
                best = r
    rankable = [e for e in evals if e["rankable"] and e.get("deployment_ready")]
    rankable.sort(key=lambda e: (e["J_us"], e["cand_index"]))
    chosen = rankable[0] if rankable else None
    return {
        "kind": "pr11_deploy_objective",
        "version": 1,
        "objective": "J(d,s) = alpha*Q99_resp(d,s) + beta*R_miss(d,s) + gamma*C_boundary(d)",
        "weights": {"alpha": alpha, "beta": beta, "gamma": gamma,
                    "miss_bound": miss_bound,
                    "note": "权重是策略输入，量纲不齐（µs 与率）；不是 WCET"},
        "deadline_us": deadline_us,
        "env": env_note,
        "n_rankable": len(rankable),
        "n_evals": len(evals),
        "chosen": None if chosen is None else {
            "candidate_id": chosen["candidate_id"],
            "cand_index": chosen["cand_index"],
            "label": chosen["label"],
            "deployment": chosen["deployment"],
            "sched": chosen["sched"],
            "J_us": chosen["J_us"],
            "terms": chosen["terms"],
        },
        "ranked": rankable,
        "all_evals": evals,
        "claim": "在已通过 verify 的候选之间按实测尾部排序；不宣称硬实时可调度性",
    }
