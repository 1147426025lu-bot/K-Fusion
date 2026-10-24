# -*- coding: utf-8 -*-
"""ECoS 时间/控制契约校验（v2）。

**为什么必须单独校验**

    契约里把 stage 名写错（例如 `stage_plnt`），规划器就查不到这个阶段的
    窗口——于是"没有约束"会被当成"约束满足"，静默放行全融合。这类错误
    在图上、在自测里都看不出来，只有板上的执行器时序会不对。

    所以严格入口先校验契约：结构错、窗口自相矛盾、stage 不存在，一律硬错误。

契约 v2 结构：

    {
      "task": {"period_us": 1000, "deadline_us": 800},
      "timing_constraints": {
        "sample":   {"stage": "stage_sample", "earliest_us": 0,   "latest_us": 30},
        "actuator": {"stage": "stage_plant",  "earliest_us": 650, "latest_us": 850},
        "feedback_delay_max_us": 400
      },
      "evidence": {"exec": "bounded" | "empirical"}
    }

    sample / actuator 也可以是列表（多个阶段各有窗口）。
"""

import json


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _windows(node):
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def validate(dag, contract):
    errs, warns = [], []
    nodes = {n["name"] for n in (dag.get("nodes") or [])}
    period_dag = ((dag.get("phase") or {}).get("period_us")
                  or ((dag.get("meta") or {}).get("task") or {}).get("period_us"))

    if not isinstance(contract, dict):
        return ["契约顶层必须是对象"], warns

    task = contract.get("task") or {}
    ctr = contract.get("timing_constraints") or {}
    if not task and not ctr:
        errs.append("契约既没有 task 也没有 timing_constraints：等于没有契约")

    period = ctr.get("period_us") or task.get("period_us") or period_dag
    deadline = task.get("deadline_us") or ctr.get("deadline_us")
    if period is not None:
        try:
            period = float(period)
            if period <= 0:
                errs.append("period_us 必须 > 0")
        except (TypeError, ValueError):
            errs.append("period_us 不是数字：%r" % (period,))
            period = None
    if deadline is not None and period is not None:
        try:
            if float(deadline) > period:
                warns.append("deadline_us %.1f > period_us %.1f：期限跨拍，"
                             "需确认控制律允许" % (float(deadline), period))
        except (TypeError, ValueError):
            errs.append("deadline_us 不是数字：%r" % (deadline,))

    for kind in ("sample", "actuator", "output"):
        for it in _windows(ctr.get(kind)):
            if not isinstance(it, dict):
                errs.append("%s 的条目不是对象：%r" % (kind, it))
                continue
            stage = it.get("stage") or it.get("name")
            if not stage:
                errs.append("%s 条目缺 stage 字段" % kind)
                continue
            if stage not in nodes:
                errs.append("%s 条目指向 DAG 里不存在的阶段 %r（写错名字会让窗口"
                            "静默失效——这正是本校验要挡的）" % (kind, stage))
                continue
            lo, hi = it.get("earliest_us"), it.get("latest_us")
            if lo is None and hi is None:
                errs.append("%s/%s 既没 earliest_us 也没 latest_us" % (kind, stage))
                continue
            try:
                lo = None if lo is None else float(lo)
                hi = None if hi is None else float(hi)
            except (TypeError, ValueError):
                errs.append("%s/%s 的窗口不是数字" % (kind, stage))
                continue
            if lo is not None and hi is not None and lo > hi:
                errs.append("%s/%s 窗口下界 %.1f > 上界 %.1f"
                            % (kind, stage, lo, hi))
            if lo is not None and lo < 0:
                errs.append("%s/%s 窗口下界为负（%.1f）" % (kind, stage, lo))
            if period is not None and hi is not None and hi > period:
                errs.append("%s/%s 窗口上界 %.1f 超过 period %.1f"
                            % (kind, stage, hi, period))
            if lo is not None and hi is not None and (hi - lo) < 1.0:
                warns.append("%s/%s 窗口只有 %.1f µs，抖动可能直接吃掉它"
                             % (kind, stage, hi - lo))

    fb = ctr.get("feedback_delay_max_us")
    if fb is not None:
        try:
            fb = float(fb)
            if fb <= 0:
                errs.append("feedback_delay_max_us 必须 > 0")
            if period is not None and fb >= period:
                warns.append("feedback_delay_max_us %.1f ≥ period %.1f："
                             "等于没有限制" % (fb, period))
        except (TypeError, ValueError):
            errs.append("feedback_delay_max_us 不是数字：%r" % (fb,))

    ev = (contract.get("evidence") or {}).get("exec")
    if ev and ev not in ("bounded", "empirical"):
        warns.append("evidence.exec=%r 未识别（只认 bounded / empirical），"
                     "按 empirical 处理" % (ev,))

    if period is not None and period_dag is not None:
        try:
            if abs(float(period_dag) - float(period)) > 1e-9:
                warns.append("契约 period_us=%.0f 与 DAG 的 %.0f 不一致，"
                             "以契约为准" % (float(period), float(period_dag)))
        except (TypeError, ValueError):
            pass

    return errs, warns


def main():
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="ECoS 契约校验（v2）")
    ap.add_argument("dag")
    ap.add_argument("contract")
    a = ap.parse_args()
    errs, warns = validate(load(a.dag), load(a.contract))
    for w in warns:
        print("⚠ %s" % w, file=sys.stderr)
    if errs:
        for e in errs:
            print("❌ %s" % e, file=sys.stderr)
        print("契约校验失败：%d 条错误" % len(errs), file=sys.stderr)
        return 3
    print("✅ 契约校验通过")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
