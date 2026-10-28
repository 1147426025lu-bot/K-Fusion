#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""temporal_effect_ir.py — Gate 2：Temporal Effect IR（时间效果中间表示）

TEIR 把三件事绑在一张图上，缺一不可（方案 §3）：

  1. 计算事实  每个阶段读/写了哪些区域；哪些写是跨拍存活的状态
  2. 时间事实  每条数据边消费的是 same-job 还是 previous-job 版本
  3. 执行事实  该阶段允许的执行域（用户态 / linux_kernel / nuttx）

它不是「DAG 再加几个时间字段」：同一条写边可以同时是「本拍下游要用」
和「积分状态下一拍还要用」。因此：

    输出这拍没人读  ≠  这一拍的计算可以删

用法：
  python temporal_effect_ir.py --dag task.dag.json --summary effect_summary.json \\
      --stages stages.json --platforms platform_caps.json -o teir.json
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys

KERNEL_UNSAFE = frozenset(["mmio", "unknown", "external", "blocking_io"])


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


def write_teir(teir, path):
    """ast_to_plan / temporal 分支调用名。"""
    dump_json(path, teir)
    return path


def describe_migration(allowed, blocked, persistent=None, effects=None):
    """编译器必须能说明「为什么能/不能进内核」，不能只给一个布尔。"""
    allowed = list(allowed or [])
    blocked = list(blocked or [])
    persistent = list(persistent or [])
    effects = set(effects or [])
    if "linux_kernel" in allowed:
        parts = ["状态访问可分析"]
        unsafe = effects & KERNEL_UNSAFE
        if not unsafe:
            parts.append("计算不依赖用户态 API")
        if persistent:
            parts.append("持久状态=" + ",".join(persistent))
        return "，".join(parts)
    for b in blocked:
        if str(b).startswith("linux_kernel"):
            return "禁止进内核：" + str(b).split(":", 1)[-1]
    return "仅允许用户态"


def cost_slot(value, evidence, note=None):
    rec = {"value": None if value is None else float(value),
           "evidence": evidence}
    if note:
        rec["note"] = note
    return rec


def build_teir_from_extract(extract, dag, ex, order, stage_rw, data_edges,
                            cross_inputs, kernelizable):
    """feat/ecos-temporal-semantics 的 TEIR 构造（抽阶段 JSON + 调用序依赖）。"""
    ex = ex or {}
    nodes = []
    for n in order:
        rw = stage_rw.get(n) or {}
        allowed = (["linux_userspace", "linux_kernel", "nuttx"]
                   if kernelizable else ["linux_userspace"])
        blocked = ([] if kernelizable
                   else ["linux_kernel:K-C0 失败闭合"])
        reads = list(rw.get("reads") or [])
        writes = list(rw.get("writes") or [])
        persist = sorted(set(reads) & set(writes))
        ev = None if ex.get(n) is None else float(ex.get(n))
        note = "两域共用占位，不是分域实测"
        cbd = {"linux_userspace": cost_slot(
            ev, "placeholder" if ev is not None else "missing", note)}
        if kernelizable:
            cbd["linux_kernel"] = cost_slot(
                ev, "placeholder" if ev is not None else "missing", note)
        nodes.append({
            "name": n,
            "fns": [n],
            "reads": reads,
            "writes": writes,
            "persistent": persist,
            "exec_us": ex.get(n),
            "phase_us": (dag.get("phase") or {}).get("offsets", {}).get(n),
            "kernelizable": bool(kernelizable),
            "must_preserve": True,
            "may_delete": False,
            "allowed_domains": allowed,
            "blocked_domains": blocked,
            "migration_reason": describe_migration(
                allowed, blocked, persist, rw.get("effects")),
            "cost_us_by_domain": cbd,
        })
    state_versions = {}
    for gl in extract.get("globals") or []:
        g = gl.get("name") if isinstance(gl, dict) else gl
        if g:
            state_versions[g] = {"written_by": [], "read_by": []}
    for n in order:
        rw = stage_rw.get(n) or {}
        for g in rw.get("writes") or []:
            if g in state_versions:
                state_versions[g]["written_by"].append(n)
        for g in rw.get("reads") or []:
            if g in state_versions:
                state_versions[g]["read_by"].append(n)
    task = (dag.get("meta") or {}).get("task") or extract.get("task") or {}
    edges = []
    for e in data_edges or []:
        ver = e.get("version") or "same_period"
        edges.append({
            "from": e.get("from"),
            "to": e.get("to"),
            "kind": "data",
            "version": ("same-job" if ver in ("same_period", "same-job")
                        else "previous-job"),
            "pinned": True,
            "proven": True,
            "regions": [e["var"]] if e.get("var") else [],
            "lag_periods": e.get("lag_periods"),
            "xdom": e.get("xdom"),
        })
    return {
        "version": 1,
        "kind": "teir",
        "ir": "TEIR",
        "source": extract.get("source"),
        "task": {
            "name": task.get("name"),
            "entry": task.get("entry") or extract.get("entry"),
            "period_us": task.get("period_us"),
            "deadline_us": task.get("deadline_us"),
        },
        "globals": [{"name": gl.get("name"), "type": gl.get("type")}
                    if isinstance(gl, dict) else {"name": gl}
                    for gl in (extract.get("globals") or [])],
        "nodes": nodes,
        "edges": edges,
        "data_edges": data_edges,
        "state_versions": state_versions,
        "cross_period_inputs": cross_inputs,
        "kernelizable": bool(kernelizable),
        "events": [],
        "persistent_state": [],
        "transform": {
            "closed": not bool(kernelizable),
            "alias_uncertain": False,
            "reasons": [] if kernelizable else ["K-C0 失败闭合"],
            "policy": "ok" if kernelizable else "refuse",
        },
        "meta": {
            "note": "extract 路径 TEIR：与 DAG+EffectSummary 路径并存，字段向 Gate 2 解释器对齐",
            "placeholder_exec": (dag.get("meta") or {}).get("auto", {}).get(
                "placeholder_exec", []),
            "version_model": {
                "same_period": "B(k) 读 A(k)",
                "prev_period": "B(k) 读 A(k-lag)",
                "lag_periods": "由 version_deps.compute_lag 推导",
                "overclaim_check": "version_deps.verify 独立复核",
            },
        },
    }


def norm_region(r):
    if r is None:
        return None
    s = str(r)
    return s if s.startswith("@") or ":" in s else ("@" + s)


def closing_pairs(dag):
    pairs = set()
    evidence = {}
    for rec in dag.get("recurrences") or []:
        for e in rec.get("closing_edges") or []:
            p = (e.get("from"), e.get("to"))
            pairs.add(p)
            evidence.setdefault(p, [])
            for x in e.get("evidence") or []:
                evidence[p].append(norm_region(x))
    return pairs, evidence


def node_map(dag):
    out = {}
    for n in dag.get("nodes") or []:
        out[n["name"]] = n
    return out


def phase_of(nodes, name, dag):
    n = nodes.get(name) or {}
    if n.get("phase_us") is not None:
        return float(n["phase_us"])
    off = ((dag.get("phase") or {}).get("offsets") or {}).get(name)
    return None if off is None else float(off)


def edge_version(e, delay_pairs, nodes, dag):
    frm, to = e.get("from"), e.get("to")
    if (frm, to) in delay_pairs:
        return "previous-job"
    pf, pt = phase_of(nodes, frm, dag), phase_of(nodes, to, dag)
    if pf is not None and pt is not None and pf > pt:
        return "previous-job"
    return "same-job"


def fn_rw(summary, fnames):
    funcs = (summary or {}).get("functions") or {}
    reads, writes, effects, unknown = set(), set(), set(), set()
    ctx = None
    for f in fnames:
        info = funcs.get(f) or {}
        reads |= set(norm_region(x) for x in (info.get("reads") or []))
        writes |= set(norm_region(x) for x in (info.get("writes") or []))
        effects |= set(info.get("effects") or [])
        unknown |= set(info.get("unknown_calls") or [])
        allowed = set(info.get("allowed_contexts") or []) or {"thread"}
        ctx = allowed if ctx is None else (ctx & allowed)
    return {
        "reads": {x for x in reads if x},
        "writes": {x for x in writes if x},
        "effects": effects,
        "unknown_calls": unknown,
        "contexts": sorted(ctx or {"thread"}),
    }


def stage_fns(node):
    fns = node.get("fns")
    if fns:
        return list(fns)
    fn = node.get("fn")
    return [fn] if fn else [node["name"]]


def platforms_from_caps(caps):
    plats = []
    if caps:
        plats = list(caps.get("platforms") or [])
    names = [p.get("name") for p in plats if p.get("name")]
    # 用户态永远是候选（原则 1：不强制内核化）
    if "linux_userspace" not in names:
        plats = [{
            "name": "linux_userspace",
            "contexts": ["thread"],
            "allow_mmio": True,
            "allow_sleep": True,
            "notes": "默认用户态；TEIR 补的域，不在 caps 文件里也要出现",
        }] + plats
    return plats


def domain_ok(plat, effects, contexts, kc0_ok):
    name = plat.get("name")
    if name in ("linux_kernel", "nuttx") and not kc0_ok:
        return False, "K-C0 失败闭合"
    if "unknown" in effects or "external" in effects:
        if name == "linux_kernel":
            return False, "unknown/external 效果不得进内核"
    if "mmio" in effects and not plat.get("allow_mmio", False):
        return False, "mmio 不被该域允许"
    if "sleep" in effects and not plat.get("allow_sleep", True):
        return False, "sleep 不被该域允许"
    if "blocking_io" in effects and name == "linux_kernel":
        return False, "blocking_io 不得进内核"
    allowed_ctx = set(plat.get("contexts") or ["thread"])
    if contexts and not (set(contexts) & allowed_ctx):
        return False, "上下文交集为空"
    return True, None


def build_teir(*args, **kwargs):
    """两套入口：
      · 本机流水线：build_teir(dag, summary, stages, caps)
      · temporal ast_to_plan：8 个位置参数（extract, dag, ex, order, ...）
    """
    if len(args) >= 6:
        return build_teir_from_extract(*args)
    return _build_teir_from_dag(*args, **kwargs)


def _build_teir_from_dag(dag, summary=None, stages=None, caps=None, kc0=None):
    summary = summary or {}
    stages = stages or {}
    nodes = node_map(dag)
    delay_pairs, delay_ev = closing_pairs(dag)
    kc0_ok = True
    kc0_info = kc0 if kc0 is not None else (stages.get("kc0") or {})
    if kc0_info:
        kc0_ok = bool(kc0_info.get("ok", True))

    # 候选边 / 未解析接口 = 别名不确定 → 保守保留或拒绝变换
    candidates = list(dag.get("candidates") or [])
    unresolved = []
    for n in dag.get("nodes") or []:
        u = n.get("interface_unresolved") or []
        if u:
            unresolved.append((n["name"], u))

    alias_uncertain = bool(candidates or unresolved or
                           (dag.get("stats") or {}).get("unresolved"))
    reasons = []
    if not kc0_ok:
        reasons.append("K-C0 失败闭合")
    if alias_uncertain:
        reasons.append("别名/接口未闭合：保留依赖，拒绝删除或重排")
    if dag.get("algebraic_loops"):
        reasons.append("代数环：无合法拍内顺序")

    plats = platforms_from_caps(caps)

    # 每个区域：谁写、谁读（按边）
    teir_edges = []
    same_job_readers = {}   # region -> [(from, to)]
    prev_job_readers = {}
    for e in dag.get("edges") or []:
        if e.get("kind") not in ("data", "call", None):
            # anti/conflict 不携带版本；仍记录但 version=unordered
            ver = "unordered"
        else:
            ver = edge_version(e, delay_pairs, nodes, dag)
        regions = [norm_region(x) for x in (e.get("evidence") or [])]
        rec = {
            "from": e.get("from"),
            "to": e.get("to"),
            "kind": e.get("kind") or "data",
            "version": ver,
            "pinned": True,
            "proven": bool(e.get("proven", True)),
            "regions": [r for r in regions if r],
        }
        if not rec["proven"] or rec["kind"] == "candidate":
            rec["version"] = rec["version"]
            rec["proven"] = False
            alias_uncertain = True
            if "别名/接口未闭合：保留依赖，拒绝删除或重排" not in reasons:
                reasons.append("别名/接口未闭合：保留依赖，拒绝删除或重排")
        teir_edges.append(rec)
        bucket = prev_job_readers if ver == "previous-job" else same_job_readers
        for r in rec["regions"]:
            bucket.setdefault(r, []).append((rec["from"], rec["to"]))

    for c in candidates:
        teir_edges.append({
            "from": c.get("from"),
            "to": c.get("to"),
            "kind": "candidate",
            "version": "unknown",
            "proven": False,
            "regions": [norm_region(x) for x in (c.get("evidence") or []) if x],
        })
        alias_uncertain = True
        if "别名/接口未闭合：保留依赖，拒绝删除或重排" not in reasons:
            reasons.append("别名/接口未闭合：保留依赖，拒绝删除或重排")

    for p, evs in delay_ev.items():
        for r in evs:
            prev_job_readers.setdefault(r, [])
            if p not in prev_job_readers[r]:
                prev_job_readers[r].append(p)

    observations = set()
    for ob in stages.get("observations") or []:
        if isinstance(ob, dict) and ob.get("name"):
            observations.add(norm_region(ob["name"]))
        elif isinstance(ob, str):
            observations.add(norm_region(ob))

    teir_nodes = []
    events = []
    persistent = []

    order = dag.get("topo_order_delayed") or dag.get("topo_order") or [
        n["name"] for n in dag.get("nodes") or []
    ]

    for name in order:
        node = nodes.get(name)
        if not node:
            continue
        rw = fn_rw(summary, stage_fns(node))
        # 摘要缺失时退回节点自带 effects
        if not rw["effects"]:
            rw["effects"] = set(node.get("effects") or [])

        why = []
        unread = []
        persist_here = []
        live_same = []

        for r in sorted(rw["writes"]):
            sj = same_job_readers.get(r) or []
            pj = prev_job_readers.get(r) or []
            self_carry = r in rw["reads"]
            sj_other = [(a, b) for a, b in sj if a == name and b != name]
            pj_from_me = [(a, b) for a, b in pj if a == name]
            if self_carry:
                persist_here.append(r)
                why.append("self_carry:%s" % r)
            if pj_from_me:
                persist_here.append(r)
                why.append("previous_job:%s" % r)
            if sj_other:
                live_same.append(r)
                why.append("same_job_live:%s" % r)
            if r in observations:
                why.append("observed:%s" % r)
            if not sj_other and not pj_from_me and not self_carry:
                unread.append(r)
                if r in observations:
                    why.append("output_commit:%s" % r)
                else:
                    # 无人读的写默认仍是可观察输出，不得当死代码删
                    why.append("unobserved_write:%s" % r)

        # 副作用本身也禁止删除
        if rw["effects"] & KERNEL_UNSAFE or "mmio" in rw["effects"]:
            why.append("effects:%s" % ",".join(sorted(rw["effects"])))

        must_preserve = True
        # 只有：无写、无副作用、无持久、无观察 —— 才可能是空阶段
        may_delete = (
            not rw["writes"]
            and not (rw["effects"] - {"pure"})
            and not persist_here
        )
        if persist_here or live_same or unread or (rw["effects"] - {"pure"}):
            must_preserve = True
            may_delete = False
        if not rw["writes"] and not rw["reads"] and not (rw["effects"] - {"pure"}):
            must_preserve = False
            may_delete = True
            why = ["empty_pure"]

        allowed = []
        blocked = []
        for plat in plats:
            ok, why_d = domain_ok(plat, rw["effects"], rw["contexts"], kc0_ok)
            if ok:
                allowed.append(plat["name"])
            else:
                blocked.append("%s:%s" % (plat["name"], why_d))

        persist = sorted(set(persist_here))
        exec_u = node.get("exec_us")
        if exec_u is None:
            exec_u = (dag.get("exec_us") or {}).get(name)
        note = "节点未分域实测时两域同值，不得当作 WCET"
        cbd = {}
        if "linux_userspace" in allowed:
            cbd["linux_userspace"] = cost_slot(
                exec_u, "dag_exec" if exec_u is not None else "missing", note)
        if "linux_kernel" in allowed:
            cbd["linux_kernel"] = cost_slot(
                exec_u, "dag_exec" if exec_u is not None else "missing", note)
        rec = {
            "name": name,
            "fns": stage_fns(node),
            "phase_us": phase_of(nodes, name, dag),
            "reads": sorted(rw["reads"]),
            "writes": sorted(rw["writes"]),
            "persistent": persist,
            "unread_this_job": unread,
            "live_same_job": live_same,
            "effects": sorted(rw["effects"]),
            "contexts": rw["contexts"],
            "unknown_calls": sorted(rw["unknown_calls"]),
            "must_preserve": must_preserve,
            "may_delete": may_delete,
            "why_preserve": why,
            "allowed_domains": allowed,
            "blocked_domains": blocked,
            "migration_reason": describe_migration(
                allowed, blocked, persist, rw["effects"]),
            "cost_us_by_domain": cbd,
            "exec_us": exec_u,
        }
        teir_nodes.append(rec)
        for r in sorted(set(persist_here)):
            persistent.append({"region": r, "writer": name, "kind": "carry"})
            events.append({
                "kind": "state_update",
                "stage": name,
                "region": r,
                "version": "previous-job",
            })
        for r in unread:
            events.append({
                "kind": "output_commit",
                "stage": name,
                "region": r,
                "version": "this-job",
            })
        for r in rw["reads"]:
            if r in prev_job_readers and any(b == name for _, b in prev_job_readers[r]):
                events.append({
                    "kind": "input_commit",
                    "stage": name,
                    "region": r,
                    "version": "previous-job",
                })

    # 边级事件：same-job 数据流
    for e in teir_edges:
        if e["version"] == "same-job" and e.get("kind") == "data":
            for r in e["regions"]:
                events.append({
                    "kind": "same_job_flow",
                    "from": e["from"],
                    "to": e["to"],
                    "region": r,
                    "version": "same-job",
                })
        elif e["version"] == "previous-job":
            for r in e["regions"]:
                events.append({
                    "kind": "previous_job_flow",
                    "from": e["from"],
                    "to": e["to"],
                    "region": r,
                    "version": "previous-job",
                })

    task = dict((dag.get("meta") or {}).get("task") or stages.get("task") or {})
    closed = bool(reasons) and (
        (not kc0_ok) or bool(dag.get("algebraic_loops"))
    )
    # 别名不确定：不关闭整个编译，但禁止删除/重排（keep）
    transform = {
        "closed": closed,
        "alias_uncertain": alias_uncertain,
        "delete_forbidden": True,  # 带状态的默认；空纯函数除外，见节点 may_delete
        "reorder_forbidden_if_alias": alias_uncertain,
        "reasons": reasons,
        "policy": (
            "refuse" if closed else
            ("keep_deps" if alias_uncertain else "ok")
        ),
    }

    return {
        "version": 1,
        "kind": "teir",
        "task": task,
        "nodes": teir_nodes,
        "edges": teir_edges,
        "events": events,
        "persistent_state": persistent,
        "transform": transform,
        "meta": {
            "note": "输出本拍未读 ≠ 可删除；别名未证则保留依赖或拒绝变换",
            "delay_edges": [
                {"from": a, "to": b} for a, b in sorted(delay_pairs)
            ],
        },
    }


def may_delete_stage(teir, name):
    for n in teir.get("nodes") or []:
        if n["name"] == name:
            return bool(n.get("may_delete"))
    raise KeyError(name)


def node(teir, name):
    for n in teir.get("nodes") or []:
        if n["name"] == name:
            return n
    raise KeyError(name)


def main():
    ap = argparse.ArgumentParser(description="DAG + EffectSummary → TEIR")
    ap.add_argument("--dag", required=True)
    ap.add_argument("--summary", default=None)
    ap.add_argument("--stages", default=None)
    ap.add_argument("--platforms", default=None)
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    dag = load_json(args.dag)
    summary = load_json(args.summary) if args.summary else {}
    stages = load_json(args.stages) if args.stages else {}
    caps = load_json(args.platforms) if args.platforms else {}
    teir = build_teir(dag, summary, stages, caps)
    dump_json(args.out, teir)

    n_keep = sum(1 for n in teir["nodes"] if n["must_preserve"])
    n_del = sum(1 for n in teir["nodes"] if n["may_delete"])
    n_prev = sum(1 for e in teir["edges"] if e["version"] == "previous-job")
    print("TEIR %d nodes (must_preserve=%d may_delete=%d)  edges previous-job=%d  policy=%s"
          % (len(teir["nodes"]), n_keep, n_del, n_prev, teir["transform"]["policy"]))
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
