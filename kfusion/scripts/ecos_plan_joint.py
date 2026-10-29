#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STKP v1：语义与时间约束驱动的选择性内核化规划。

范围（必须写进论文，不得扩大声称）：
  固定阶段顺序、连续融合、两个 Linux 执行域（用户态 / 内核态）。
  原始组合数 N(n)=2·3^{n-1}（5 阶段 162）。最优仅针对该空间与既定成本模型。

层次：
  1. 静态合法性（TEIR allowed_domains、K-C0、版本边）
  2. 部署×融合联合枚举 + 安全剪枝
  3. 独立验证；混合域默认可部署仅限 UKU ioctl 适配器（用户态—内核—用户态）

A–F（candidate_generate）保留为论文对照基线，不驱动代码生成。
"""
from __future__ import print_function

import argparse
import importlib.util
import io
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
DOMAINS = ("linux_userspace", "linux_kernel")
sys.path.insert(0, os.path.join(HERE, "ecos"))
import stage_cost as stage_cost_mod  # noqa: E402
import uku_interface as uku_mod  # noqa: E402
import deploy_objective as do_mod  # noqa: E402
EV_FINAL = stage_cost_mod.EV_FINAL
EV_EXPLORE = stage_cost_mod.EV_EXPLORE
EV_WEAK = stage_cost_mod.EV_WEAK
EV_MISSING = stage_cost_mod.EV_MISSING


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


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_verifier():
    return load_module(os.path.join(KF, "verify", "plan_verify.py"),
                       "plan_verify")


def load_baseline():
    return load_module(
        os.path.join(HERE, "ecos", "candidate_generate.py"),
        "candidate_generate")


def load_fusion_planner():
    path = os.path.join(HERE, "fusion_plan__融合规划.py")
    return load_module(path, "fusion_plan").Planner


def partitions(n):
    """连续分段：相邻是否切一刀。"""
    for cuts in itertools.product((0, 1), repeat=max(0, n - 1)):
        groups, start = [], 0
        for i, cut in enumerate(cuts, 1):
            if cut:
                groups.append(list(range(start, i)))
                start = i
        groups.append(list(range(start, n)))
        yield groups


def raw_space_size(n):
    """两域、连续融合：2·3^{n-1}。"""
    if n <= 0:
        return 0
    return 2 * (3 ** (n - 1))


def stage_order(teir):
    nodes = list(teir.get("nodes") or [])
    nodes.sort(key=lambda n: (
        n.get("phase_us") is None,
        float(n.get("phase_us") or 0),
        n["name"],
    ))
    return [n["name"] for n in nodes]


def node_map(teir):
    return {n["name"]: n for n in (teir.get("nodes") or [])}


def slot_value(slot, from_override=False):
    return stage_cost_mod.parse_cost_entry(slot, from_override=from_override)


def stage_compute_cost(node, domain, override):
    """缺测不得当 0。裸 CLI 覆盖是 cli_override，不是 measured。"""
    if override and domain in override and node["name"] in override[domain]:
        return slot_value(override[domain][node["name"]], from_override=True)
    cbd = node.get("cost_us_by_domain") or {}
    if domain in cbd:
        return slot_value(cbd[domain], from_override=False)
    if node.get("exec_us") is not None:
        return float(node["exec_us"]), "shared_exec"
    return None, "missing"


def _finite_nonneg(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return v == v and v >= 0.0


def is_uku_ioctl(plan):
    return (plan.get("meta") or {}).get("xdom_adapter") == "uku_ioctl"


def candidate_schedule(plan, teir, override, c_user, c_kernel):
    """同一份分域 exec / 释放代价，供 Ĉ 与 DAG 窗口。"""
    nodes = node_map(teir)
    exec_us = {}
    evidence = []
    missing = []
    release = []
    # PR-9E：这些阶段的 exec_us 是 0（组总额记在组尾），只是为了让 Ĉ 的把总成本
    # 加回来，**不是**它们的执行时间。任何窗口/顺序证明都不得用这些 0。
    timing_bound = []
    uku = is_uku_ioctl(plan)
    for g in plan.get("groups") or []:
        d = g.get("domain")
        if uku:
            # ioctl 路径是单用户态线程，没有内核 hrtimer 周期释放。
            L = 0.0 if d == "linux_kernel" else float(c_user)
        else:
            L = c_kernel if d == "linux_kernel" else c_user
        release.append({
            "group": g.get("id"),
            "domain": d,
            "L_us": float(L),
            "phase_us": g.get("release_phase_us"),
        })
        members = g.get("members") or []
        gslot = stage_cost_mod.lookup_group_cost(override, members, d)
        if gslot is not None:
            val, ev = slot_value(gslot, from_override=True)
            evidence.append("C(%s,%s):%s" % ("+".join(members), d, ev))
            if val is None or not _finite_nonneg(val):
                for m in members:
                    missing.append("%s@%s" % (m, d))
            else:
                # 总额记在组尾；组内 0 标 group_bound，不得当作独立阶段实测。
                # PR-9E：0 值只用于「把组总成本加回去」，不能当组内阶段的时间证据，
                # 因此单独记进 timing_bound，禁止流入窗口/顺序校验。
                for i, m in enumerate(members):
                    if i == len(members) - 1:
                        exec_us[m] = float(val)
                    else:
                        exec_us[m] = 0.0
                        timing_bound.append(m)
                    evidence.append("%s@%s:group_bound" % (m, d))
            continue
        for m in members:
            val, ev = stage_compute_cost(nodes.get(m) or {"name": m}, d,
                                         override)
            evidence.append("%s@%s:%s" % (m, d, ev))
            if val is None or not _finite_nonneg(val):
                missing.append("%s@%s" % (m, d))
            else:
                exec_us[m] = float(val)
    if uku:
        # 只计一次用户态唤醒；第二段 U 已在同一线程，不再加 L_U。
        for i, r in enumerate(release):
            if r["domain"] == "linux_userspace" and i > 0:
                r["L_us"] = 0.0
    return {
        "exec_us": exec_us,
        "release": release,
        "evidence": evidence,
        "missing": missing,
        "timing_bound": timing_bound,
        "complete": not missing,
        "model": "uku_ioctl_serial" if uku else "per_group_release",
    }


def uku_serial_events(plan, sched, t_ioctl):
    """一次唤醒后连续执行。采样用开始时刻，输出用提交（完成）时刻。"""
    exec_us = (sched or {}).get("exec_us") or {}
    L_by = {r.get("group"): float(r.get("L_us") or 0.0)
            for r in (sched or {}).get("release") or []}
    t = 0.0
    events = {}
    for g in plan.get("groups") or []:
        t += L_by.get(g.get("id"), 0.0)
        if g.get("domain") == "linux_kernel":
            t += float(t_ioctl or 0.0)
        for m in g.get("members") or []:
            start = t
            t += float(exec_us.get(m) or 0.0)
            events[m] = {
                "start_us": start,
                "read_us": start,
                "finish_us": t,
                "commit_us": t,
            }
    return t, events


def uku_serial_finish(plan, sched, t_ioctl):
    end, events = uku_serial_events(plan, sched, t_ioctl)
    return end, {k: v["finish_us"] for k, v in events.items()}


def _event_instant(events, stage, kind):
    e = (events or {}).get(stage)
    if e is None:
        return None
    if isinstance(e, dict) and "start_us" in e:
        if kind == "sample":
            return e.get("read_us", e.get("start_us"))
        return e.get("commit_us", e.get("finish_us"))
    try:
        return float(e)
    except (TypeError, ValueError):
        return None


def uku_window_errors(contract, events):
    if not contract:
        return []
    ctr = contract.get("timing_constraints") or {}
    bad = []
    for key in ("sample", "actuator", "output"):
        w = ctr.get(key)
        if not w:
            continue
        items = w if isinstance(w, list) else [w]
        for it in items:
            if not isinstance(it, dict):
                continue
            st = it.get("stage")
            t = _event_instant(events, st, key)
            if t is None:
                continue
            lo = it.get("earliest_us")
            hi = it.get("latest_us")
            label = "采样开始" if key == "sample" else "提交"
            if lo is not None and t < float(lo) - 1e-9:
                bad.append({"code": "E_UKU_WINDOW",
                            "msg": "%s %s %.3f < earliest %s" % (
                                st, label, t, lo)})
            if hi is not None and t > float(hi) + 1e-9:
                bad.append({"code": "E_UKU_WINDOW",
                            "msg": "%s %s %.3f > latest %s" % (
                                st, label, t, hi)})
    return bad


def parse_ioctl_cost(ioctl_cost, handoff_us):
    """T_ioctl：裸 --handoff-cost-us 是 cli_override，不是 measured。"""
    if ioctl_cost is not None:
        return stage_cost_mod.parse_cost_entry(ioctl_cost, from_override=True)
    if handoff_us is None:
        return 0.0, "missing"
    return float(handoff_us), "cli_override"


def batched_handoffs(order, named_groups, domains, teir):
    """跨域交接按相邻组计一批，不按依赖边数乘固定税。"""
    channels = []
    for i in range(len(named_groups) - 1):
        if domains[i] == domains[i + 1]:
            continue
        left, right = named_groups[i], named_groups[i + 1]
        regions = []
        for e in teir.get("edges") or []:
            if e.get("kind") not in ("data", "call"):
                continue
            if e.get("from") in left and e.get("to") in right:
                regions.extend(e.get("regions") or [])
        channels.append({
            "from_group": i,
            "to_group": i + 1,
            "from_domain": domains[i],
            "to_domain": domains[i + 1],
            "from_stage": left[-1],
            "to_stage": right[0],
            "regions": sorted(set(regions)),
        })
    return channels


def plan_platform(domains):
    uniq = list(dict.fromkeys(domains))
    if len(uniq) == 1:
        return uniq[0]
    return "mixed"


def collapsed_domains(groups):
    seq = []
    for g in groups or []:
        d = g.get("domain")
        if d and (not seq or seq[-1] != d):
            seq.append(d)
    return seq


def mixed_adapter_kind(groups):
    """PR-5B.1：可部署混合仅恰好三组 U|K|U（一个内核组）。

    折叠后的 UKU（例如 U|K|K|U）会把两组内核阶段合成一次 ioctl 再各跑一遍，
    改变语义。未通过接口门禁前不得 deployment_ready。
    """
    if len(groups or []) != 3:
        return None
    doms = [g.get("domain") for g in groups]
    if doms != ["linux_userspace", "linux_kernel", "linux_userspace"]:
        return None
    return "uku_ioctl"


def group_release_phase(members, nodes):
    phs = []
    for m in members:
        p = (nodes.get(m) or {}).get("phase_us")
        if p is not None:
            phs.append(float(p))
    return min(phs) if phs else 0.0


def build_plan(order, named_groups, domains, nodes=None):
    stage_dom = {}
    groups = []
    nodes = nodes or {}
    for i, (members, d) in enumerate(zip(named_groups, domains)):
        groups.append({
            "id": "g%d" % i,
            "members": list(members),
            "domain": d,
            "release_phase_us": group_release_phase(members, nodes),
        })
        for m in members:
            stage_dom[m] = d
    plat = plan_platform(domains)
    fused = len(groups) == 1
    adapter = mixed_adapter_kind(groups) if plat == "mixed" else None
    ready = plat in ("linux_userspace", "linux_kernel") or adapter == "uku_ioctl"
    return {
        "version": 2,
        "meta": {
            "platform": plat,
            "fusion": fused,
            "generator": "stkp_v1",
            "model_feasible": True,
            "deployment_ready": ready,
            "xdom_adapter": adapter,
        },
        "order": list(order),
        "groups": groups,
        "domains": stage_dom,
    }


def af_label(plan):
    plat = (plan.get("meta") or {}).get("platform")
    groups = plan.get("groups") or []
    n = len(plan.get("order") or [])
    fused = len(groups) == 1
    unfused = n > 0 and len(groups) == n
    if plat == "linux_userspace" and unfused:
        return "A"
    if plat == "linux_userspace" and fused:
        return "B"
    if plat == "linux_kernel" and unfused:
        return "C"
    if plat == "linux_kernel" and fused:
        return "D"
    if plat == "mixed" and fused is False:
        # E = 按可内核化切段并组内融合；无法在此精确对齐时用 STKP 标签
        return None
    return None


def evaluate_cost(plan, teir, c_user, c_kernel, extra_kernel, handoff_us,
                  override, deadline_us, contract=None, ioctl_cost=None):
    sched = candidate_schedule(plan, teir, override, c_user, c_kernel)
    evidence = list(sched["evidence"])
    missing = list(sched["missing"])
    compute = sum(sched["exec_us"].values()) if sched["complete"] else None
    release = sum(r["L_us"] for r in sched["release"])
    has_k = any(r["domain"] == "linux_kernel" for r in sched["release"])
    named = [g["members"] for g in (plan.get("groups") or [])]
    domains = [g["domain"] for g in (plan.get("groups") or [])]
    channels = batched_handoffs(plan.get("order") or [], named, domains, teir)
    uku = is_uku_ioctl(plan)
    h_val, h_ev = parse_ioctl_cost(ioctl_cost, handoff_us)
    if h_val is None or not _finite_nonneg(h_val):
        h_val, h_ev = 0.0, "missing"
    if uku:
        h_total = float(h_val)
        sync = 0.0
        sched["T_ioctl_us"] = h_total
        evidence.append("T_ioctl:%s" % h_ev)
    else:
        h_total = len(channels) * float(h_val)
        sync = extra_kernel if has_k else 0.0
        sched["T_ioctl_us"] = 0.0
    if extra_kernel and not uku and not _finite_nonneg(extra_kernel):
        missing.append("extra_kernel")
    kinds = [item.rsplit(":", 1)[-1] for item in evidence]
    stage_kinds = [k for k in kinds if not k.startswith("T_ioctl")]
    # T_ioctl 缺测不让整个 UKU 不可比，只禁止结题。
    has_missing = bool(missing) or any(
        k in EV_MISSING for k in stage_kinds)
    has_ph = any(k in EV_WEAK for k in kinds)
    comparable = not has_missing
    ioctl_final = (not uku) or (h_ev in EV_FINAL)
    final_rankable = comparable and (not has_ph) and ioctl_final and all(
        k in EV_FINAL for k in stage_kinds)
    explore_rankable = comparable and (not has_ph)
    pred = None if not comparable else round(
        (compute or 0.0) + release + h_total + sync, 3)
    if pred is not None and not _finite_nonneg(pred):
        comparable = False
        pred = None
        missing.append("predicted_R")
        has_missing = True
    temporal_ok = True
    temporal_why = None
    if comparable and deadline_us is not None and pred is not None:
        if pred > float(deadline_us):
            temporal_ok = False
            temporal_why = "R̂=%.3f > deadline %s" % (pred, deadline_us)
    if comparable and uku:
        _end, events = uku_serial_events(plan, sched, h_total)
        werr = uku_window_errors(contract, events)
        sched["events_us"] = events
        sched["finish_us"] = {k: v["finish_us"] for k, v in events.items()}
        if werr:
            temporal_ok = False
            temporal_why = "; ".join(e["msg"] for e in werr[:4])
    if has_missing:
        grade = "missing"
    elif has_ph:
        grade = "placeholder_no_optimum"
    elif final_rankable:
        grade = "empirical_model_not_wcet"
    else:
        grade = "explore_uncertain"
    return {
        "comparable": comparable,
        "final_rankable": final_rankable,
        "explore_rankable": explore_rankable,
        "missing_costs": missing,
        "evidence": evidence,
        "compute_us": None if not comparable else round(compute, 3),
        "release_us": round(release, 3),
        "handoff_us": round(h_total, 3),
        "handoff_channels": 1 if uku else len(channels),
        "channels": channels,
        "sync_us": round(sync, 3),
        "ioctl_model": ({
            "L_user_us": round(release, 3),
            "C_us": None if compute is None else round(compute, 3),
            "T_ioctl_us": round(h_total, 3),
            "T_ioctl_evidence": h_ev,
            "note": "Ĉ=L_U+ΣC+T_ioctl；T_ioctl 须实测才可结题；非 WCET",
        } if uku else None),
        "predicted_R_us": pred,
        "total_us": pred,
        "grade": grade,
        "temporal_ok": temporal_ok,
        "temporal_why": temporal_why,
        "schedule": sched,
    }, channels


def load_dag_verifier():
    return load_module(os.path.join(HERE, "ecos", "plan_verify.py"),
                       "ecos_plan_verify")


def dag_temporal_ok(plan, dag, contract, platforms, c_user, c_kernel, sched):
    """窗口检查必须用与 Ĉ 同一份 CandidateSchedule.exec_us。"""
    if not dag:
        return True, []
    plat_name = (plan.get("meta") or {}).get("platform")
    if plat_name == "mixed":
        if not is_uku_ioctl(plan):
            return True, []
        t_ioctl = (sched or {}).get("T_ioctl_us") or 0.0
        _end, events = uku_serial_events(plan, sched, t_ioctl)
        werr = uku_window_errors(contract, events)
        return (not werr), werr
    if sched is not None and not sched.get("complete"):
        ctr = (contract or {}).get("timing_constraints") or {}
        if any(ctr.get(k) for k in ("sample", "actuator", "output")):
            return False, [{"code": "E_WINDOW_NO_COST",
                            "msg": "有相位窗口但缺测，不得用 0 过窗"}]
        return True, []
    # PR-9E：组级实测成本只用于比较整组代价；组内成员被记成 0 的那些，
    # 没有任何执行时间证据。有相位窗口时不能拿它当时间证明。
    bound = (sched or {}).get("timing_bound") or []
    if bound:
        ctr = (contract or {}).get("timing_constraints") or {}
        if any(ctr.get(k) for k in ("sample", "actuator", "output")):
            return False, [{
                "code": "E_WINDOW_GROUP_BOUND",
                "msg": ("组级实测成本不得当组内阶段执行时间：%s 无逐阶段测量，"
                        "存在相位窗口时不得宣称时间合法" % ",".join(bound)),
            }]
    exec_us = (sched or {}).get("exec_us") or {}
    rel = (sched or {}).get("release") or []
    if rel:
        c = float(rel[0]["L_us"])
    else:
        c = c_kernel if plat_name == "linux_kernel" else c_user
    mod = load_dag_verifier()
    plats = []
    if platforms:
        plats = [p for p in (platforms.get("platforms") or [])
                 if p.get("name") == plat_name]
    if not plats:
        plats = [{
            "name": plat_name or "linux_userspace",
            "contexts": ["thread", "softirq"],
            "allow_mmio": plat_name != "linux_kernel",
            "allow_sleep": True,
        }]
    try:
        rep = mod.verify_plan(dag, plan, plats[0],
                              release_cost_us=c, exec_us=exec_us,
                              contract=contract)
    except Exception as exc:
        return False, [{"code": "DAG_VERIFY", "msg": str(exc)}]
    return bool(rep.get("ok")), list(rep.get("errors") or [])


def pick_chosen(codegen):
    """占位成本不得宣称最优；缺测不得进入排序。"""
    if not codegen:
        return None, None
    final = [r for r in codegen if (r.get("cost") or {}).get("final_rankable")]
    if final:
        return min(final, key=rank_key), "empirical_model_not_wcet"
    explore = [r for r in codegen if (r.get("cost") or {}).get("explore_rankable")]
    if explore:
        return min(explore, key=rank_key), "explore_uncertain"
    user = [r for r in codegen if r.get("domain") == "linux_userspace"]
    pool = user or codegen
    return min(pool, key=rank_key), "placeholder_no_optimum"


def rank_key(rec):
    cost = rec.get("cost") or {}
    pred = cost.get("predicted_R_us")
    # 不可比的不得赢；然后更小 R̂、更少组、更少交接、用户态优先
    incomp = 0 if rec.get("comparable") and pred is not None else 1
    pred_v = pred if pred is not None else 1e18
    plat = rec.get("domain") or ""
    pen = {"linux_userspace": 0, "linux_kernel": 1, "mixed": 2}.get(plat, 3)
    ng = len((rec.get("plan") or {}).get("groups") or [])
    nh = (cost.get("handoff_channels") or 0)
    return (incomp, pred_v, ng, nh, pen)


def solve(teir, verifier, c_user=21.0, c_kernel=21.0, extra_kernel=0.0,
          handoff_us=0.0, override=None, max_stages=8, keep_rejected=40,
          dag=None, contract=None, platforms=None, ioctl_cost=None):
    order = stage_order(teir)
    n = len(order)
    if n == 0:
        raise SystemExit("TEIR 没有阶段")
    if n > max_stages:
        raise SystemExit("阶段数 %d 超过 STKP v1 穷举上限 %d" % (n, max_stages))
    nodes = node_map(teir)
    deadline = (teir.get("task") or {}).get("deadline_us")
    prune = {"domain": 0, "semantic": 0, "temporal": 0, "runtime": 0,
             "incomparable": 0}
    feasible, rejected = [], []
    raw = 0
    for parts in partitions(n):
        named = [[order[i] for i in g] for g in parts]
        for domains in itertools.product(DOMAINS, repeat=len(named)):
            raw += 1
            blocked = False
            why_dom = []
            for members, d in zip(named, domains):
                for m in members:
                    allowed = (nodes.get(m) or {}).get("allowed_domains")
                    if not allowed or d not in allowed:
                        blocked = True
                        why_dom.append("%s∉%s" % (m, d or "?"))
            if blocked:
                prune["domain"] += 1
                rejected.append({"reason": "domain", "detail": why_dom[:6]})
                continue
            plan = build_plan(order, named, domains, nodes)
            if plan["meta"].get("xdom_adapter") == "uku_ioctl":
                chk = uku_mod.check(plan, teir, contract)
                plan["meta"]["uku_check"] = {
                    "ok": chk["ok"],
                    "codes": uku_mod.codes(chk),
                    "errors": chk.get("errors") or [],
                }
                if not chk["ok"]:
                    plan["meta"]["deployment_ready"] = False
                    plan["meta"]["xdom_adapter"] = None
                    plan["meta"]["uku_reject"] = uku_mod.codes(chk)
                else:
                    sess = (chk.get("interface") or {}).get("session")
                    if sess:
                        plan["meta"]["uku_session"] = sess
            ok, diags = verifier.verify(teir, plan)
            if not ok:
                prune["semantic"] += 1
                rejected.append({
                    "reason": "semantic",
                    "codes": [d.get("code") for d in diags[:6]],
                    "groups": named,
                    "domains": list(domains),
                })
                continue
            cost, channels = evaluate_cost(
                plan, teir, c_user, c_kernel, extra_kernel, handoff_us,
                override, deadline, contract=contract, ioctl_cost=ioctl_cost)
            plan["cost"] = {
                "predicted_R_us": cost["predicted_R_us"],
                "grade": cost["grade"],
            }
            rec = {
                "id": None,
                "domain": plan["meta"]["platform"],
                "fused": plan["meta"]["fusion"],
                "legal": True,
                "model_feasible": True,
                "deployment_ready": bool(plan["meta"]["deployment_ready"]),
                "comparable": cost["comparable"],
                "diagnostics": [],
                "plan": plan,
                "cost": cost,
                "schedule": cost.get("schedule"),
                "why": None,
            }
            if cost.get("schedule"):
                plan["schedule"] = {
                    "exec_us": cost["schedule"].get("exec_us") or {},
                    "release": cost["schedule"].get("release") or [],
                    "complete": cost["schedule"].get("complete"),
                }
            rec["id"] = af_label(plan) or ("STKP:%s" % "|".join(
                "%s[%s]" % (d[0].upper(), "+".join(g))
                for g, d in zip(named, domains)))
            if not cost["temporal_ok"]:
                prune["temporal"] += 1
                rec["legal"] = False
                rec["model_feasible"] = False
                rec["why"] = cost["temporal_why"]
                rejected.append({"reason": "temporal", "id": rec["id"],
                                 "detail": cost["temporal_why"]})
                continue
            if rec["deployment_ready"] and dag:
                ok_d, derrs = dag_temporal_ok(
                    plan, dag, contract, platforms, c_user, c_kernel,
                    cost.get("schedule"))
                if not ok_d:
                    prune["temporal"] += 1
                    rec["legal"] = False
                    rec["model_feasible"] = False
                    rec["why"] = "DAG 时间契约: " + "; ".join(
                        (e.get("code") or "") for e in derrs[:4])
                    rejected.append({
                        "reason": "temporal", "id": rec["id"],
                        "codes": [e.get("code") for e in derrs[:6]],
                    })
                    continue
            if not rec["deployment_ready"]:
                prune["runtime"] += 1
                rec["why"] = (
                    "混合域仅 UKU ioctl（用户态—内核—用户态）可部署，"
                    "当前形态无适配器")
            if not rec["comparable"]:
                prune["incomparable"] += 1
                rec["why"] = (rec["why"] or "") + (
                    "; 缺测不得当 0: " + ",".join(cost["missing_costs"]))
            feasible.append(rec)

    codegen = [r for r in feasible if r["deployment_ready"] and r["legal"]]
    deployable = [r for r in codegen if r["comparable"]]
    model_ok = [r for r in feasible if r["model_feasible"] and r["legal"]]
    best_deploy, claim = pick_chosen(codegen)
    best_model = min(
        [r for r in model_ok if r.get("comparable")],
        key=rank_key) if any(r.get("comparable") for r in model_ok) else None
    if best_deploy:
        c = best_deploy["cost"]
        parts = [
            "STKP Ĉ=ΣC_i,d + ΣL_g + ΣH_q + C_sync = %s" % c.get("total_us"),
            "groups=%d channels=%d" % (
                len(best_deploy["plan"]["groups"]),
                c.get("handoff_channels") or 0),
            "deployment_ready",
        ]
        if best_model and best_model is not best_deploy:
            if (best_model.get("cost") or {}).get("total_us") is not None:
                if best_model["cost"]["total_us"] < (c.get("total_us") or 0):
                    parts.append(
                        "模型更优 %s Ĉ=%s 但不可部署" % (
                            best_model["id"], best_model["cost"]["total_us"]))
        if best_deploy["domain"] == "linux_userspace":
            parts.append("同分或不优于内核时保留用户态")
        if claim:
            parts.append("claim=%s" % claim)
        best_deploy["why"] = "; ".join(parts)

    return {
        "version": 1,
        "kind": "stkp",
        "algorithm": "STKP_v1",
        "scope": ("fixed-order contiguous fusion; domains="
                  "linux_userspace|linux_kernel; not arbitrary DAG"),
        "order": order,
        "raw_space": raw_space_size(n),
        "enumerated": raw,
        "pruned": prune,
        "n_model_feasible": len(model_ok),
        "n_deployment_ready": len(codegen),
        "claim": claim,
        "candidates": codegen,
        "model_only": [r for r in model_ok if not r["deployment_ready"]][:20],
        "rejected_sample": rejected[:keep_rejected],
        "best_model": best_model,
        "best_deploy": best_deploy,
        "chosen": None if best_deploy is None else best_deploy["id"],
        "chosen_record": best_deploy,
        "note": "经验成本，不是 WCET；不可部署的混合方案可出现在报告中",
    }


def attach_baseline(doc, teir, c_user, c_kernel, extra_kernel, verifier):
    try:
        base = load_baseline()
        af = base.generate(teir, c_user, c_kernel, extra_kernel, 0.0, verifier)
    except Exception as exc:
        doc["baseline_af"] = {"error": str(exc)}
        return doc
    doc["baseline_af"] = {
        "chosen": af.get("chosen"),
        "n_legal": af.get("n_legal"),
        "candidates": [
            {"id": c["id"], "legal": c["legal"], "domain": c["domain"],
             "fused": c["fused"],
             "total_us": (c.get("cost") or {}).get("total_us")}
            for c in af.get("candidates") or []
        ],
    }
    stkp_c = (doc.get("chosen_record") or {}).get("cost") or {}
    by = {}
    for c in doc.get("candidates") or []:
        i = c.get("id")
        if i in ("A", "B", "C", "D", "E", "F"):
            by[i] = (c.get("cost") or {}).get("total_us")
    doc["vs_baseline"] = {
        "cost_model": "stkp_unified",
        "note": "A–F 旧公式不用于论文优劣证据；此处用同一套 STKP 成本",
        "af_chosen": af.get("chosen"),
        "stkp_chosen": doc.get("chosen"),
        "stkp_cost_of_af_ids": by,
        "stkp_total_us": stkp_c.get("total_us"),
        "stkp_missed_by_af": (
            doc.get("chosen") not in (None, "A", "B", "C", "D", "E", "F")
        ),
        "claim": doc.get("claim"),
    }
    return doc


def parse_override(text):
    """{domain: {stage: us}} 或空。"""
    if not text:
        return None
    obj = json.loads(text) if text.lstrip().startswith("{") else load_json(text)
    return obj


def _slim_objective(res):
    """decision 里 all_evals 很长；stkp.json 只留可排名的 + 每候选一条拒因。"""
    seen, unrank = set(), []
    for e in res.get("all_evals") or []:
        if e.get("rankable"):
            continue
        cid = e.get("candidate_id")
        if cid in seen:
            continue
        seen.add(cid)
        unrank.append({"candidate_id": cid, "deployment": e.get("deployment"),
                       "sched": e.get("sched"),
                       "missing": e.get("missing")})
    out = {k: res.get(k) for k in
           ("kind", "version", "objective", "weights", "deadline_us",
            "n_rankable", "n_evals", "chosen", "claim")}
    out["ranked"] = res.get("ranked") or []
    out["not_rankable"] = unrank
    return out


def apply_deploy_objective(doc, profile, alpha=1.0, beta=0.0, gamma=0.0,
                           miss_bound="point", scheds=None, deadline=None):
    """用实测尾部选部署，调度类一起选。返回 (doc, ok)。

    ok=False 表示没有任何 (部署, 调度类) 同时具备实测 Q99 与 R_miss。
    此时**必须**把 chosen 清空，让上层 fail-closed 停止生成——
    退回模型排序等于把「测不出来」悄悄当成「模型说得对」。
    """
    cands = doc.get("candidates") or []
    if deadline is None:
        deadline = (doc.get("task") or {}).get("deadline_us")
    res = do_mod.decide(cands, profile, alpha=alpha, beta=beta, gamma=gamma,
                        miss_bound=miss_bound, deadline_us=deadline,
                        scheds=tuple(scheds) if scheds else do_mod.SCHED_CLASSES)
    prev_id = doc.get("chosen")
    prev_cost = ((doc.get("chosen_record") or {}).get("cost") or {}).get("total_us")
    doc["model_best_before_objective"] = {"chosen": prev_id,
                                          "total_us": prev_cost}
    slim = _slim_objective(res)
    doc["deploy_objective"] = slim

    # 覆盖度：有多少个 deployment_ready 候选真的进了排名。
    # 不足 100% 时入选结论只覆盖已测形状——必须显式说出来，
    # 否则「A 入选」会被误读成「A 最优」，其实是内核候选没画像。
    # 按**索引**统计：候选 id 会重名（同名分属不同域），按 id 会串。
    ranked_idx = set()
    for e in res.get("all_evals") or []:
        if e.get("rankable"):
            ranked_idx.add(e.get("cand_index"))
    all_idx = set(range(len(cands)))
    slim["coverage"] = {
        "ranked_candidates": len(ranked_idx),
        "deployment_ready_candidates": len(cands),
        "unranked_candidates": [
            "%s@%s" % (cands[i].get("id"), cands[i].get("domain"))
            for i in sorted(all_idx - ranked_idx)],
        "complete": not (all_idx - ranked_idx),
    }

    if res.get("chosen") is None:
        doc["chosen"] = None
        doc["chosen_record"] = None
        doc["sched_class"] = None
        doc["claim"] = "objective_fail_closed"
        return doc, False

    idx = res["chosen"].get("cand_index")
    if idx is None or not (0 <= idx < len(cands)):
        doc["chosen"] = None
        doc["chosen_record"] = None
        doc["sched_class"] = None
        doc["claim"] = "objective_candidate_missing"
        return doc, False
    rec = cands[idx]

    doc["chosen_record"] = rec
    doc["chosen"] = rec.get("id")
    doc["sched_class"] = res["chosen"]["sched"]
    doc["claim"] = "objective_ranked_measured_tail"
    rec["why"] = ("%s; J=%s µs（α=%s β=%s γ=%s, miss_bound=%s）；"
                  "模型原选 %s" % (rec.get("why") or "",
                                  res["chosen"]["J_us"], alpha, beta, gamma,
                                  miss_bound, prev_id))
    return doc, True


def run_stkp(args):
    teir = load_json(args.teir)
    v = load_verifier()
    override = parse_override(args.stage_cost) if args.stage_cost else None
    ioctl_cost = None
    if args.ioctl_cost:
        ioctl_cost = (json.loads(args.ioctl_cost)
                      if str(args.ioctl_cost).lstrip().startswith("{")
                      else load_json(args.ioctl_cost))
    dag = load_json(args.dag) if args.dag else None
    contract = load_json(args.contract) if args.contract else None
    platforms = load_json(args.platforms) if args.platforms else None
    doc = solve(
        teir, v,
        c_user=args.c_user_us,
        c_kernel=args.c_kernel_us,
        extra_kernel=args.kernel_extra_us,
        handoff_us=args.handoff_cost_us,
        override=override,
        max_stages=args.max_stages,
        dag=dag, contract=contract, platforms=platforms,
        ioctl_cost=ioctl_cost,
    )
    if args.deploy_objective:
        prof = do_mod.load_profile(args.deploy_objective)
        scheds = [s.strip() for s in str(args.objective_scheds).split(",")
                  if s.strip()]
        doc, ok = apply_deploy_objective(
            doc, prof, alpha=args.objective_alpha, beta=args.objective_beta,
            gamma=args.objective_gamma, miss_bound=args.objective_miss_bound,
            scheds=scheds,
            deadline=((teir.get("task") or {}).get("deadline_us")))
        if args.objective_out:
            dump_json(args.objective_out, doc["deploy_objective"])
        if not ok:
            print("!! 部署目标 fail-closed：没有同时具备实测 Q99 与 R_miss 的"
                  " (部署, 调度类) 组合，拒绝生成（%d 个候选，%d 条拒因）" % (
                      doc["deploy_objective"].get("n_evals") or 0,
                      len(doc["deploy_objective"].get("not_rankable") or [])),
                  file=sys.stderr)
    if not args.no_baseline:
        doc = attach_baseline(doc, teir, args.c_user_us, args.c_kernel_us,
                              args.kernel_extra_us, v)
    if args.out:
        dump_json(args.out, doc)
    rec = doc.get("chosen_record")
    if args.chosen_plan:
        if not rec or not rec.get("plan"):
            print("无 deployment_ready 方案，不写 %s" % args.chosen_plan,
                  file=sys.stderr)
            return 3
        dump_json(args.chosen_plan, rec["plan"])
        print("STKP chosen plan → %s  (%s %s Ĉ=%s)" % (
            args.chosen_plan, doc.get("chosen"), rec.get("domain"),
            (rec.get("cost") or {}).get("total_us")))
    print("STKP v1 raw=%d enum=%d deployable=%d model=%d chosen=%s claim=%s" % (
        doc["raw_space"], doc["enumerated"], doc["n_deployment_ready"],
        doc["n_model_feasible"], doc["chosen"], doc.get("claim")))
    pr = doc["pruned"]
    print("  prune domain=%d semantic=%d temporal=%d mixed=%d missing=%d" % (
        pr["domain"], pr["semantic"], pr["temporal"], pr["runtime"],
        pr["incomparable"]))
    if rec:
        for g in rec["plan"]["groups"]:
            print("  %-3s %-28s @ %s" % (
                g["id"], " + ".join(g["members"]), g["domain"]))
    obj = doc.get("deploy_objective")
    if obj:
        ch = obj.get("chosen")
        before = (doc.get("model_best_before_objective") or {}).get("chosen")
        if ch:
            cov = obj.get("coverage") or {}
            print("  deploy objective: %s @ %s J=%s µs（模型原选 %s；"
                  "有画像 %d/%d）" % (
                      ch.get("label") or ch.get("candidate_id"), ch.get("sched"),
                      ch.get("J_us"), before,
                      cov.get("ranked_candidates"),
                      cov.get("deployment_ready_candidates")))
            if not cov.get("complete"):
                print("  ⚠ 覆盖不全：%s 无实测画像，未参与排名——"
                      "结论只覆盖已测形状" % ", ".join(
                          cov.get("unranked_candidates") or []))
        else:
            print("  deploy objective: fail-closed（模型原选 %s 未被证实）" % before)
    vs = doc.get("vs_baseline") or {}
    if vs:
        print("  vs A–F: baseline=%s STKP=%s missed_by_af=%s" % (
            vs.get("af_chosen"), vs.get("stkp_chosen"),
            vs.get("stkp_missed_by_af")))
    if args.out:
        print("wrote %s" % args.out)
    return 0 if rec else 3


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


def run_dag_oracle(args):
    """旧接口：DAG + fusion_plan.group_legal。STKP 请走 --teir。"""
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
        print("!! 阶段数 %d 超过 exhaustive 上限 %d" %
              (len(order), args.max_stages), file=sys.stderr)
        return 2
    exec_us = {}
    if args.exec_us:
        exec_us = (json.loads(args.exec_us) if args.exec_us.lstrip().startswith("{")
                   else load_json(args.exec_us))
    release = {p["name"]: 20.0 for p in platforms}
    if args.release_cost_us:
        release.update(json.loads(args.release_cost_us)
                       if str(args.release_cost_us).lstrip().startswith("{")
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
        dump_json(args.out, result)
    if not valid:
        print("!! 没有合法联合方案", file=sys.stderr)
        return 3
    b = valid[0]
    print("== exhaustive oracle：合法 %d / 拒绝 %d ==" %
          (len(valid), len(rejected)))
    print("== 最优 R̂ = %.3f µs ==" % b["cost"]["predicted_R_us"])
    return 0


def main():
    ap = argparse.ArgumentParser(description="STKP v1 / DAG exhaustive oracle")
    ap.add_argument("dag", nargs="?", default=None,
                    help="旧接口：DAG JSON（无 --teir 时）")
    ap.add_argument("--teir", default=None, help="STKP 入口：TEIR JSON")
    ap.add_argument("--dag", dest="dag_opt", default=None)
    ap.add_argument("--platforms", default=None)
    ap.add_argument("--exec-us", default=None)
    ap.add_argument("--release-cost-us", default=None)
    ap.add_argument("--handoff-cost-us", type=float, default=0.0)
    ap.add_argument("--c-user-us", type=float, default=21.0)
    ap.add_argument("--c-kernel-us", type=float, default=21.0)
    ap.add_argument("--kernel-extra-us", type=float, default=0.0)
    ap.add_argument("--stage-cost", default=None,
                    help="分域成本 {domain:{stage: number|{value,evidence,source_log}}}")
    ap.add_argument("--ioctl-cost", default=None,
                    help="UKU T_ioctl {value,evidence,source_log,samples}")
    ap.add_argument("--contract", default=None, help="时间契约 JSON（窗口/期限）")
    ap.add_argument("--max-stages", type=int, default=8)
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--chosen-plan", default=None)
    ap.add_argument("--deploy-objective", default=None,
                    help="实测调度画像 sched_profile.json；给了就按 J(d,s) 选，"
                         "不做任何组合可排名时 fail-closed 不生成")
    ap.add_argument("--objective-alpha", type=float, default=1.0)
    ap.add_argument("--objective-beta", type=float, default=0.0,
                    help="µs / 单位超期率（1e5 表示 1%% 超期折 1000µs）")
    ap.add_argument("--objective-gamma", type=float, default=0.0,
                    help="跨界结构代价权重；Q99 已含往返，>0 是策略选择不是时延")
    ap.add_argument("--objective-miss-bound", dest="objective_miss_bound",
                    default="point", choices=("point", "upper"))
    ap.add_argument("--objective-scheds", default="cfs,fifo99")
    ap.add_argument("--objective-out", default=None,
                    help="把决策明细单独写出一份 json")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()
    args.dag = args.dag_opt or args.dag
    if args.teir:
        return run_stkp(args)
    if not args.dag or not args.platforms:
        print("用法: ecos_plan_joint.py --teir t.json -o stkp.json [--chosen-plan p.json]",
              file=sys.stderr)
        print("  或: ecos_plan_joint.py dag.json --platforms caps.json",
              file=sys.stderr)
        return 2
    return run_dag_oracle(args)


if __name__ == "__main__":
    sys.exit(main())
