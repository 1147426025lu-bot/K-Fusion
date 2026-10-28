#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan_verify.py — Gate 3：与规划器分离的 TEIR 事件解释器

规划器（fusion_plan）负责**搜索**合法分段；本文件**不 import 它**，
只用 TEIR 上的数据版本跑一遍参考解释：

  same-job     本拍内生产者必须排在消费者之前
  previous-job 本拍内必须先读后写（否则读到的是本拍新值，年龄变成 0）
  age          跨拍边的逻辑年龄必须是 1，不是 k-2
  domain       阶段只能放到 TEIR.allowed_domains
  cover        must_preserve 的阶段不能从方案里消失

这才能做变异测试：改年龄 / 调换顺序 / 丢掉积分阶段后，验证器必须独立报错。
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys

# 禁止从规划器抄判定——Gate 3 的分离点
_FORBIDDEN_IMPORTS = ("fusion_plan", "fusion_plan__融合规划")


def load_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def teir_nodes(teir):
    return {n["name"]: n for n in (teir.get("nodes") or [])}


def flatten_plan(plan):
    seq = []
    groups = plan.get("groups") or []
    if not groups:
        # 无 groups 时允许用 order 当作「每阶段一组」
        for n in plan.get("order") or []:
            seq.append(n)
        return seq
    for g in groups:
        for m in g.get("members") or []:
            seq.append(m)
    return seq


def assigned_domain(plan, stage):
    doms = plan.get("domains") or {}
    if stage in doms:
        return doms[stage]
    for g in plan.get("groups") or []:
        if stage in (g.get("members") or []) and g.get("domain"):
            return g.get("domain")
    meta = plan.get("meta") or {}
    plat = meta.get("platform") or meta.get("domain")
    if plat and plat != "mixed":
        return plat
    return None


def edge_key(frm, to):
    return "%s->%s" % (frm, to)


def diagnose(code, where, msg):
    return {"code": code, "where": where, "msg": msg}


def verify(teir, plan, claims=None):
    """参考解释器。返回 (ok, diagnostics)。"""
    claims = claims or {}
    diags = []
    nodes = teir_nodes(teir)
    seq = flatten_plan(plan)
    idx = {}
    for i, name in enumerate(seq):
        if name in idx:
            diags.append(diagnose("E_DUP", name, "方案里出现两次"))
        idx[name] = i

    # 覆盖：must_preserve 必须在
    for name, n in nodes.items():
        if n.get("must_preserve") and name not in idx:
            diags.append(diagnose(
                "E_DROP_STATE", name,
                "must_preserve 阶段从方案中消失（漏掉状态依赖）"))
        if n.get("may_delete") and name not in idx:
            continue
        if name not in idx and not n.get("may_delete"):
            diags.append(diagnose("E_COVER", name, "阶段未被方案覆盖"))

    # 方案里出现 TEIR 没有的名字
    for name in seq:
        if name not in nodes:
            diags.append(diagnose("E_UNKNOWN_STAGE", name, "TEIR 中无此阶段"))

    # 域 + 同组必须同域（约束 B）
    for g in plan.get("groups") or []:
        members = list(g.get("members") or [])
        seen = []
        for m in members:
            d = assigned_domain(plan, m) or g.get("domain")
            if d and d not in seen:
                seen.append(d)
        if len(seen) > 1:
            diags.append(diagnose(
                "E_GROUP_DOMAIN", g.get("id") or ",".join(members),
                "同组必须同一执行域，实际 %s" % seen))
        gdom = g.get("domain")
        if gdom and seen and gdom not in seen:
            diags.append(diagnose(
                "E_GROUP_DOMAIN", g.get("id") or ",",
                "组标注域 %s 与成员域 %s 不一致" % (gdom, seen)))

    for name in seq:
        n = nodes.get(name)
        if not n:
            continue
        dom = assigned_domain(plan, name)
        if not dom:
            continue
        allowed = n.get("allowed_domains") or []
        if allowed and dom not in allowed:
            diags.append(diagnose(
                "E_DOMAIN", name,
                "域 %s 不在 allowed_domains %s" % (dom, allowed)))

    # 别名未证：不得把候选边当成「无依赖」去重排
    alias = (teir.get("transform") or {}).get("alias_uncertain")
    if alias:
        phase_order = [n["name"] for n in sorted(
            teir.get("nodes") or [],
            key=lambda x: (x.get("phase_us") is None, x.get("phase_us") or 0, x["name"]))]
        # 只要求相对顺序不逆于相位序
        pos_phase = {nm: i for i, nm in enumerate(phase_order)}
        for a in seq:
            for b in seq:
                if a == b or a not in pos_phase or b not in pos_phase:
                    continue
                if pos_phase[a] < pos_phase[b] and idx.get(a, -1) > idx.get(b, 10**9):
                    diags.append(diagnose(
                        "E_ALIAS_REORDER", "%s|%s" % (a, b),
                        "别名未证时不得把相位序颠倒"))
                    break

    age_claims = claims.get("edge_age") or plan.get("edge_age") or {}

    for e in teir.get("edges") or []:
        if e.get("kind") == "candidate":
            continue
        if e.get("kind") not in ("data", "call"):
            continue
        frm, to = e.get("from"), e.get("to")
        if frm not in idx or to not in idx:
            continue
        ver = e.get("version")
        i_p, i_c = idx[frm], idx[to]
        where = edge_key(frm, to)

        if ver == "same-job":
            if i_p >= i_c:
                diags.append(diagnose(
                    "E_SAME_JOB_ORDER", where,
                    "same-job 边要求本拍内 %s 在 %s 之前（解释器序 %d ≮ %d）"
                    % (frm, to, i_p, i_c)))
        elif ver == "previous-job":
            # 本拍若先写后读，消费者拿到的是本拍新值 → 年龄 0
            if i_p < i_c:
                diags.append(diagnose(
                    "E_PREV_JOB_BECAME_SAME", where,
                    "previous-job 边在本拍变成先写后读：%s 在 %s 之前，读到的不是 k-1"
                    % (frm, to)))
            claimed = age_claims.get(where)
            if claimed is None:
                claimed = e.get("claimed_age", 1)
            try:
                claimed_i = int(claimed)
            except (TypeError, ValueError):
                claimed_i = claimed
            if claimed_i != 1:
                diags.append(diagnose(
                    "E_AGE", where,
                    "跨拍边逻辑年龄必须为 1 拍，方案声称 %r（k-%s 过期）"
                    % (claimed, claimed_i)))

        if e.get("proven") is False and claims.get("treat_unproven_as_free"):
            diags.append(diagnose(
                "E_UNPROVEN_FREE", where,
                "未证明的边不得当成无约束"))

    # 期限：只有方案自己给出了 R̂ 才核对；没有 WCET 不假装有界
    task = teir.get("task") or {}
    deadline = task.get("deadline_us")
    cost = (plan.get("cost") or {})
    pred = cost.get("predicted_R_us")
    if deadline is not None and pred is not None:
        try:
            if float(pred) > float(deadline):
                diags.append(diagnose(
                    "E_DEADLINE", "cost.predicted_R_us",
                    "R̂=%.3f µs > deadline %s µs" % (float(pred), deadline)))
        except (TypeError, ValueError):
            pass

    ok = not diags
    return ok, diags


def mutate_age(plan, frm, to, age):
    """变异：把一条 previous-job 边的声称年龄改成 k-age。"""
    p = json.loads(json.dumps(plan))
    p.setdefault("edge_age", {})[edge_key(frm, to)] = age
    return p


def mutate_drop(plan, stage):
    p = json.loads(json.dumps(plan))
    for g in p.get("groups") or []:
        g["members"] = [m for m in g.get("members") or [] if m != stage]
    if "order" in p:
        p["order"] = [m for m in p["order"] if m != stage]
    return p


def mutate_swap(plan, a, b):
    """变异：对调方案里两个阶段的执行顺序（例如 plant 提前到 sample 前）。"""
    p = json.loads(json.dumps(plan))
    seq = flatten_plan(p)
    if a not in seq or b not in seq:
        raise SystemExit("swap 找不到 %s/%s" % (a, b))
    i, j = seq.index(a), seq.index(b)
    seq[i], seq[j] = seq[j], seq[i]
    # 写回：若只有一组，整组替换；否则按扁平序拆回原组大小
    groups = p.get("groups") or []
    k = 0
    for g in groups:
        n = len(g.get("members") or [])
        g["members"] = seq[k:k + n]
        k += n
    if not groups:
        p["order"] = seq
    return p


def mutate_domain(plan, stage, domain):
    p = json.loads(json.dumps(plan))
    p.setdefault("domains", {})[stage] = domain
    return p


def print_report(ok, diags, out_json=None):
    if ok:
        print("VERIFY PASS")
    else:
        print("VERIFY FAIL")
        for d in diags:
            print("  [%s] %s  %s" % (d["code"], d["where"], d["msg"]))
    if out_json:
        d = os.path.dirname(os.path.abspath(out_json))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(out_json, "w", encoding="utf-8") as f:
            json.dump({"ok": ok, "diagnostics": diags}, f,
                      ensure_ascii=False, indent=2)
            f.write("\n")


def main():
    ap = argparse.ArgumentParser(description="独立于规划器的 TEIR 验证器")
    ap.add_argument("--teir", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--claims", default=None)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()
    teir = load_json(args.teir)
    plan = load_json(args.plan)
    claims = load_json(args.claims) if args.claims else {}
    ok, diags = verify(teir, plan, claims)
    print_report(ok, diags, args.out)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
