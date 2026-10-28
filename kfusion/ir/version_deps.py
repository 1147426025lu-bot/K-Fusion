# -*- coding: utf-8 -*-
"""version_deps — Gate 2 后期 + Gate 3 准备：数据版本依赖的独立推导与复核。

为什么需要它
------------
Gate 2 v1 的 TEIR 把所有数据边都标成 same_period（同拍可见），把"读但无同周期生产者"
lump 成 cross_period_inputs。这在**单域同周期**下成立；但一旦出现：
  · 跨域边（生产者与消费者分属不同 cell / 调度域）
  · 多速率（生产者周期 ≠ 消费者周期，PR11-D）
就**不能**默认同拍可见。这正是 PlanVerifier 那条诚实边界要修的东西（见 MEMORY.md）：

  "跨域边不能一律'下一拍可见'，应把通信代价与所需数据版本分离，
   只有同拍消息确能在消费者读取前可用才允许同拍跨域。"

本模块做两件事（与规划器解耦，可被独立复核——满足 Gate 3「与规划器分离的验证」）：
  1. compute_lag(...) / analyze(dag)：从 (域归属, 各域周期, 各阶段相位) 重新推导
     每条数据边的**必要最小版本延迟** lag_min（单位：消费者周期拍），
     并和 ast_to_plan 声明的 version 比对。
  2. verify(dag)：把"声明比必要更乐观"（例如声称 same_period 但必要是 prev_period）
     判为错误（fail-closed）——杜绝 over-claim。

版本语义
--------
对一条数据边 A→B（B 读变量 g，A 是同调用序里 B 之前最后一个写 g 的阶段）：
  · same_period  : B(k) 读的是 A(k) 在本周期写的版本。要求 A 的写在本周期 B 读之前可用。
  · prev_period  : B(k) 读的是 A(k-lag) 的版本（lag≥1），即从更早的周期继承（z⁻¹ 类）。

必要最小延迟 lag_min 由 (P_A, P_B, 是否跨域, 相位) 决定：
  · 同域且相位 A≤B（A 在 B 之前跑完）：lag_min=0 → same_period。
  · 同域多速率：令 R = P_A / P_B（生产者周期 / 消费者周期）。
        R≤1（生产者不慢于消费者）：每消费者周期生产者至少跑一次，且最慢一次在
            该消费者周期起点前完成 ⇒ lag_min=0（same_period，前提相位有序）。
        R>1（生产者更慢）：生产者每 R 个消费者周期才跑一次，两次之间 B 最多等
            R-1 个消费者周期 ⇒ lag_min = R-1（prev_period）。
  · 跨域：默认 lag_min = max(1, R-1)（"下一拍才可见"，多速率时更保守），除非调用方用
        same_period_xdom_proof 提供相位证明（证明跨域消息在消费者读取前可达，且 R≤1）。
        诚实起见：证明不了就按 prev_period，绝不默认 same_period。
"""

import math


def node_period(dag, name):
    """取节点周期（µs）：节点 period_us > 域周期 > 任务周期。"""
    node = None
    for n in (dag.get("nodes") or []):
        if n.get("name") == name:
            node = n
            break
    if node is None:
        return float((dag.get("meta") or {}).get("task", {}).get("period_us") or 0.0)
    if node.get("period_us"):
        return float(node["period_us"])
    dom = node.get("domain") or "default"
    dpus = (dag.get("meta") or {}).get("domain_period_us") or {}
    if dpus.get(dom):
        return float(dpus[dom])
    return float((dag.get("meta") or {}).get("task", {}).get("period_us") or 0.0)


def node_domain(dag, name):
    for n in (dag.get("nodes") or []):
        if n.get("name") == name:
            return n.get("domain") or "default"
    return "default"


def node_phase(dag, name):
    phase = (dag.get("phase") or {}).get("offsets") or {}
    return float(phase.get(name, 0.0))


def compute_lag(Pa, Pb, xdom, phase_a, phase_b, same_period_xdom_proof=False):
    """纯函数：从 (生产者周期, 消费者周期, 是否跨域, 相位, 证明) 推必要最小延迟。

    返回 (lag_min:int, kind:str)。lag_min 单位 = 消费者周期拍。
    """
    if Pb <= 0:
        Pb = max(Pa, 1e-9)
    R = Pa / Pb  # 生产者 / 消费者 周期比

    if xdom:
        # 跨域：默认下一拍（或更多，多速率时）才可见
        if same_period_xdom_proof and R <= 1.0 + 1e-9 and phase_a <= phase_b + 1e-9:
            # 提供了相位证明（生产者在该消费者周期内先于消费者完成）才放行同拍
            return 0, "xdom_proven_same_period"
        return max(1, int(math.ceil(R) - 1)), "xdom_default_prev_period"

    # 同域
    if R <= 1.0 + 1e-9:
        if phase_a <= phase_b + 1e-9:
            return 0, "same_domain_same_period"
        # 同周期内生产者相位反而晚于消费者 ⇒ 该读不可能同拍，退为上一拍
        return 1, "same_domain_phase_conflict_prev"
    # 同域多速率、生产者更慢
    return int(math.ceil(R) - 1), "same_domain_multirate_prev"


def _claimed_lag(edge):
    """声明延迟：same_period→0；prev_period→edge.lag_periods（默认1）。"""
    if edge.get("version") == "same_period":
        return 0
    return int(edge.get("lag_periods") or 1)


def analyze(dag):
    """逐边重新推导必要最小延迟，并和声明比对。

    返回 edges:[{from,to,var,claimed,required,kind,xdom,consistent,over_safe}],
    cross_period:[{stage,var}]（本就是 prev_period，无声明可查）。
    """
    edges = [e for e in (dag.get("edges") or []) if e.get("kind") == "data"]
    reports = []
    for e in edges:
        a, b = e.get("from"), e.get("to")
        Pa, Pb = node_period(dag, a), node_period(dag, b)
        xa, xb = node_domain(dag, a), node_domain(dag, b)
        xdom = (xa != xb)
        la = node_phase(dag, a)
        lb = node_phase(dag, b)
        proof = bool(e.get("same_period_xdom_proof"))
        req, kind = compute_lag(Pa, Pb, xdom, la, lb, proof)
        claimed = _claimed_lag(e)
        consistent = claimed >= req  # 声明不能比必要更乐观
        over_safe = claimed > req    # 声明比必要保守（允许，记 warning）
        reports.append({
            "from": a, "to": b, "var": e.get("var") or e.get("proven"),
            "xdom": xdom, "claimed": claimed, "required": req,
            "kind": kind, "consistent": consistent, "over_safe": over_safe,
            "version": e.get("version"), "lag_periods": e.get("lag_periods"),
        })
    cross = (dag.get("meta") or {}).get("auto", {}).get("cross_period_inputs") or \
            dag.get("cross_period_inputs") or []
    return {"edges": reports, "cross_period": cross}


def verify(dag):
    """独立复核：发现 over-claim（声明比必要更乐观）即 fail-closed 报错。

    返回 {ok, errors:[...], warnings:[...], edges, cross_period}。
    """
    res = analyze(dag)
    errors = []
    warnings = []
    for r in res["edges"]:
        if not r["consistent"]:
            errors.append(
                "OVERCLAIM %s->%s(%s): 声明 version=%s lag=%s，但 (域/周期/相位) "
                "推导必要延迟=%d（%s）。跨域/多速率边不能默认同拍可见——"
                "要么补 same_period_xdom_proof（且仅当 R≤1 且相位允许），"
                "要么把版本降级为 prev_period 并设 lag_periods≥%d。"
                % (r["from"], r["to"], r["var"], r["version"],
                   r["lag_periods"], r["required"], r["kind"], r["required"]))
        elif r["over_safe"]:
            warnings.append(
                "CONSERVATIVE %s->%s(%s): 声明 lag=%d 大于必要 %d（%s）——保守，允许。"
                % (r["from"], r["to"], r["var"], r["claimed"],
                   r["required"], r["kind"]))
    # 跨周期输入本就是 prev_period；若某处声称它为 same_period 属于逻辑错误，
    # 但这里没有 producer 边，仅作记录，不单独判错。
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "edges": res["edges"],
        "cross_period": res["cross_period"],
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: version_deps.py <dag.json>")
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
    rep = verify(d)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0 if rep["ok"] else 3)
