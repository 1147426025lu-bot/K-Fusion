#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""let_decide.py — Gate 5：按需 LET，不强制所有程序 LET 化

融合会把组内后段前拉到组头相位。若契约钉住了采样/提交时刻：

  - 无 LET：全融合可能非法（输出提前生效 / 采样被提前）
  - 有 LET：计算仍可融合，但输入快照与输出提交留在原相位（多一次定时事件）
  - LET 更贵：编译器主动不用，改选保留钉住相位的选择性融合

严格周期模式下，把周期从 T 拉成 1.5T 来「消除 stale」一律拒绝。

用法：
  python let_decide.py --teir task.teir.json --time five_stage_let.time.json -o let.json
"""
from __future__ import print_function

import argparse
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))


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


def load_verifier():
    path = os.path.join(KF, "verify", "plan_verify.py")
    spec = importlib.util.spec_from_file_location("plan_verify", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def phase_map(teir):
    return {n["name"]: float(n.get("phase_us") or 0)
            for n in teir.get("nodes") or []}


def order_of(teir):
    nodes = list(teir.get("nodes") or [])
    nodes.sort(key=lambda n: (n.get("phase_us") is None,
                              float(n.get("phase_us") or 0), n["name"]))
    return [n["name"] for n in nodes]


def groups_plan(order, groups, platform, let_events=None, period_us=None):
    gs = []
    for i, members in enumerate(groups):
        gs.append({"id": "g%d" % i, "members": list(members)})
    meta = {
        "platform": platform,
        "generator": "let_decide",
        "let": bool(let_events),
    }
    if period_us is not None:
        meta["period_us"] = period_us
    return {
        "version": 2,
        "meta": meta,
        "order": [m for g in groups for m in g],
        "groups": gs,
        "let_events": list(let_events or []),
    }


def group_head_phase(members, ph):
    return min(ph[m] for m in members)


def pulled_stages(members, ph):
    head = group_head_phase(members, ph)
    return [(m, ph[m], head) for m in members if ph[m] > head + 1e-9]


def pin_violations(plan, ph, contract, apply_let):
    """对照钉扎相位。apply_let=True 时用 LET 事件抵消对应违例。"""
    pins = (contract.get("pins") or {})
    events = list(plan.get("let_events") or [])
    covered = set()
    if apply_let:
        for ev in events:
            covered.add((ev.get("stage"), ev.get("kind")))
    diags = []
    for g in plan.get("groups") or []:
        members = g.get("members") or []
        if not members:
            continue
        head = group_head_phase(members, ph)
        for m in members:
            pin = pins.get(m)
            if not pin:
                continue
            kind = pin.get("kind")
            want = float(pin.get("phase_us"))
            pulled = ph[m] > head + 1e-9
            if kind == "input_snapshot" and pin.get("pull_forbidden") and pulled:
                if (m, "input_snapshot") in covered:
                    continue
                diags.append({
                    "code": "E_PIN_PULL",
                    "where": m,
                    "msg": "采样相位 %s 被前拉到组头 %.0f，无输入快照 LET" % (want, head),
                })
            if kind == "output_commit" and pin.get("early_commit_forbidden"):
                # 组头早于钉住的提交时刻 ⇒ 输出会提前生效
                if head + 1e-9 < want and pulled:
                    if (m, "output_commit") in covered:
                        continue
                    diags.append({
                        "code": "E_PIN_EARLY_COMMIT",
                        "where": m,
                        "msg": "提交钉在 %.0f µs，融合后组头 %.0f，无输出保持 LET"
                               % (want, head),
                    })
    return diags


def let_repair(plan, ph, contract):
    """为被前拉的钉扎阶段补快照/提交事件。"""
    pins = contract.get("pins") or {}
    events = []
    for g in plan.get("groups") or []:
        members = g.get("members") or []
        if not members:
            continue
        head = group_head_phase(members, ph)
        for m in members:
            pin = pins.get(m) or {}
            if ph[m] <= head + 1e-9:
                continue
            kind = pin.get("kind")
            if kind == "input_snapshot" and pin.get("pull_forbidden"):
                events.append({
                    "kind": "input_snapshot",
                    "stage": m,
                    "at_us": float(pin["phase_us"]),
                    "compute_at_us": head,
                })
            if kind == "output_commit" and pin.get("early_commit_forbidden"):
                events.append({
                    "kind": "output_commit",
                    "stage": m,
                    "at_us": float(pin["phase_us"]),
                    "compute_at_us": head,
                })
    repaired = json.loads(json.dumps(plan))
    repaired["let_events"] = events
    repaired["meta"]["let"] = bool(events)
    return repaired


def pin_preserving_groups(order, pins, ph):
    """钉住的阶段单独成组；中间未钉住的连续阶段可融合。"""
    groups, cur = [], []
    for name in order:
        pin = pins.get(name)
        pinned = bool(pin) and (
            pin.get("pull_forbidden") or pin.get("early_commit_forbidden"))
        if pinned:
            if cur:
                groups.append(cur)
                cur = []
            groups.append([name])
        else:
            cur.append(name)
    if cur:
        groups.append(cur)
    return groups or [[n] for n in order]


def window_ok(contract):
    """同一条边上多条相位窗取交集；空集则整份部署非法。"""
    diags = []
    by = {}
    for w in contract.get("windows") or []:
        k = (w.get("from"), w.get("to"))
        by.setdefault(k, []).append(w)
    for k, lst in by.items():
        lo = max(float(x["w_us"]) + float(x.get("H_us") or 0)
                 + float(x.get("eps_us") or 0) for x in lst)
        hi = min(float(x["r_us"]) for x in lst)
        if lo > hi + 1e-9:
            diags.append({
                "code": "E_WINDOW_EMPTY",
                "where": "%s->%s" % k,
                "msg": "各窗单独可写，交集空：提交下界 %.0f > 读取上界 %.0f" % (lo, hi),
            })
    return diags


def period_ok(plan, contract):
    if contract.get("allow_period_stretch"):
        return []
    want = contract.get("period_us")
    got = (plan.get("meta") or {}).get("period_us")
    if got is None:
        return []
    if want is not None and abs(float(got) - float(want)) > 1e-6:
        return [{
            "code": "E_PERIOD_STRETCH",
            "where": "period_us",
            "msg": "严格周期模式拒绝 %s → %s" % (want, got),
        }]
    return []


def cost_plan(plan, c_us, let_event_us, exec_us):
    g = len(plan.get("groups") or [])
    n_let = len(plan.get("let_events") or [])
    total = g * c_us + n_let * let_event_us + exec_us
    return {
        "groups": g,
        "let_events": n_let,
        "c_us": c_us,
        "let_event_us": let_event_us,
        "exec_us": exec_us,
        "total_us": round(total, 3),
        "uses_let": n_let > 0,
    }


def decide(teir, contract, verifier, c_us, let_event_us, exec_us,
           platform="linux_userspace"):
    ph = phase_map(teir)
    order = order_of(teir)
    pins = contract.get("pins") or {}
    period = contract.get("period_us")

    def eval_plan(name, groups, let=False, period_us=None):
        plan = groups_plan(order, groups, platform, period_us=period_us)
        if let:
            plan = let_repair(plan, ph, contract)
        ok_v, d_v = verifier.verify(teir, plan)
        d_pin = pin_violations(plan, ph, contract, apply_let=True)
        d_per = period_ok(plan, contract)
        d_win = window_ok(contract)
        diags = list(d_v) + d_pin + d_per + d_win
        legal = ok_v and not d_pin and not d_per and not d_win
        rec = {
            "id": name,
            "legal": legal,
            "diagnostics": diags,
            "plan": plan,
            "cost": cost_plan(plan, c_us, let_event_us, exec_us),
            "why": None,
        }
        if not legal:
            rec["why"] = "拒绝: " + ", ".join(d["code"] for d in diags[:5])
        else:
            rec["why"] = ("LET×%d" % rec["cost"]["let_events"]
                          if rec["cost"]["uses_let"] else "不用 LET")
        return rec

    unfused = [[n] for n in order]
    fused = [list(order)]
    selective = pin_preserving_groups(order, pins, ph)

    cands = [
        eval_plan("unfused", unfused),
        eval_plan("full_fuse", fused, let=False),
        eval_plan("full_fuse_let", fused, let=True),
        eval_plan("selective_pins", selective, let=False),
        eval_plan("stretch_period", unfused, let=False,
                  period_us=(period or 1000) * 1.5),
    ]

    legal = [c for c in cands if c["legal"]]

    def rank(c):
        let_bit = 1 if c["cost"]["uses_let"] else 0
        return (c["cost"]["total_us"], let_bit, c["id"])

    chosen = min(legal, key=rank) if legal else None
    if chosen:
        others = [c["id"] for c in legal if c["id"] != chosen["id"]]
        extra = []
        full = next((c for c in cands if c["id"] == "full_fuse"), None)
        if full and not full["legal"]:
            extra.append("全融合无 LET 非法")
        if chosen["cost"]["uses_let"]:
            extra.append("LET 修复钉扎后仍更便宜")
        else:
            extra.append("未采用 LET（钉扎已由分组保住，或 LET 更贵）")
        chosen["why"] = chosen["why"] + "; " + "; ".join(extra)

    return {
        "version": 1,
        "kind": "let_decide",
        "task": teir.get("task") or {},
        "contract": {
            "mode": contract.get("mode"),
            "period_us": period,
            "allow_period_stretch": bool(contract.get("allow_period_stretch")),
            "pinned": sorted(pins.keys()),
        },
        "model": {
            "formula": "Cost = G*c + |LET|*let_event + E",
            "c_us": c_us,
            "let_event_us": let_event_us,
            "note": "LET 是可选变换；更贵就不用。不是 WCET。",
        },
        "candidates": cands,
        "chosen": None if chosen is None else chosen["id"],
        "chosen_record": chosen,
        "n_legal": len(legal),
    }


def main():
    ap = argparse.ArgumentParser(description="按需 LET 决策")
    ap.add_argument("--teir", required=True)
    ap.add_argument("--time", required=True, help="时间契约 JSON（pins / 周期）")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--c-us", type=float, default=21.0)
    ap.add_argument("--let-event-us", type=float, default=21.0,
                    help="每次 LET 快照/提交事件的代价；设大则编译器不用 LET")
    ap.add_argument("--exec-us", type=float, default=0.0)
    ap.add_argument("--platform", default="linux_userspace")
    args = ap.parse_args()

    teir = load_json(args.teir)
    contract = load_json(args.time)
    v = load_verifier()
    doc = decide(teir, contract, v, args.c_us, args.let_event_us,
                 args.exec_us, args.platform)
    dump_json(args.out, doc)
    print("LET decide legal=%d  chosen=%s" % (doc["n_legal"], doc["chosen"]))
    for c in doc["candidates"]:
        mark = "→" if c["id"] == doc["chosen"] else " "
        st = "LEGAL " if c["legal"] else "REJECT"
        print("  %s %-16s %s Cost=%s  %s"
              % (mark, c["id"], st,
                 c["cost"]["total_us"] if c["legal"] else "-",
                 c["why"] or ""))
    print("wrote %s" % args.out)
    return 0 if doc["chosen"] else 3


if __name__ == "__main__":
    sys.exit(main())
