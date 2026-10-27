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
        "feedback_delay_max_us": 400,
        "xdom_max_age_ticks": 1
      },
      "evidence": {"exec": "bounded" | "empirical"}
    }

    sample / actuator 也可以是列表（多个阶段各有窗口）。
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


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

    # ---- 反馈死区上限 ----
    # 位置必须对。PR10-E 之前这里是**静默失效**的：唯一一份跨域夹具把
    # feedback_delay_max_us 写在顶层 control 下，而消费者只读
    # timing_constraints ⇒ 上限从未被读到、校验器也不报错，
    # 于是"跨域把反馈死区拉长一整拍"这件事一路无人过问（板上实测发散）。
    ctrl_blk = contract.get("control") or {}
    if isinstance(ctrl_blk, dict) and "feedback_delay_max_us" in ctrl_blk:
        errs.append(
            "契约：feedback_delay_max_us 写在顶层 control 下（非契约位置）。"
            "请移到 timing_constraints 下——消费者只读那里，"
            "放错位置等于这条约束不存在")

    fb = ctr.get("feedback_delay_max_us")
    if fb is not None:
        try:
            fb = float(fb)
            if fb <= 0:
                errs.append("feedback_delay_max_us 必须 > 0")
            if period is not None and fb >= period:
                # 别写成"等于没有限制"：一拍以内的死区（z⁻¹）确实拦不住，
                # 但两拍以上的情形（跨域、多组切开）它照样能拦——PR10-D 的
                # 跨域夹具正是 2 拍 ≈ 1399 µs 被 1000 µs 的上限挡住。
                warns.append(
                    "feedback_delay_max_us %.1f ≥ period %.1f：拦不住一拍以内的"
                    "反馈死区（如融合把闭合边拉到整拍），但仍约束两拍以上的"
                    "情形（跨域 +1 拍、多组切开）" % (fb, period))
        except (TypeError, ValueError):
            errs.append("feedback_delay_max_us 不是数字：%r" % (fb,))

    # ---- 跨域数据年龄预算（PR11-A）----
    # 位置同样的坑：写在 control 下就没人读。这里把它当成一等约束。
    if isinstance(ctrl_blk, dict) and "xdom_max_age_ticks" in ctrl_blk:
        errs.append(
            "契约：xdom_max_age_ticks 写在顶层 control 下（非契约位置）。"
            "请移到 timing_constraints 下——生成器只读那里，"
            "放错位置等于这条约束不存在，代码会按『不设预算』生成")
    age_budget = ctr.get("xdom_max_age_ticks")
    if age_budget is not None:
        try:
            age_budget = float(age_budget)
            if age_budget != int(age_budget):
                errs.append("xdom_max_age_ticks 必须是整数拍：%r" % (age_budget,))
            if age_budget < 1:
                # 0 拍在物理上不可能：生产者写完那一刻消费者已经晚了一点点，
                # 允许 0 等于要求"零延迟"，只能靠拒收全部数据来满足。
                errs.append(
                    "xdom_max_age_ticks 必须 ≥ 1（0 拍等于要求零延迟，"
                    "物理上不可满足，只会把所有数据都判成过期）")
            if age_budget >= 2:
                warns.append(
                    "xdom_max_age_ticks=%.0f 允许 age 到 %d 拍；而实测（PR10-F §13.12）"
                    "该闭环在 age 1–2 已经发散，0–1 才收敛——预算是运行时的兜底，"
                    "不是控制稳定的证明" % (age_budget, int(age_budget)))
        except (TypeError, ValueError):
            errs.append("xdom_max_age_ticks 不是数字：%r" % (age_budget,))

    # ---- 抖动余量（PR11-C）：模型不含抖动，判据要扣掉实测残差上界 ----
    if isinstance(ctrl_blk, dict) and "xdom_jitter_us" in ctrl_blk:
        errs.append(
            "契约：xdom_jitter_us 写在顶层 control 下（非契约位置）。"
            "请移到 timing_constraints 下——判据只读那里")
    jit = ctr.get("xdom_jitter_us")
    if jit is not None:
        try:
            jit = float(jit)
            if jit < 0:
                errs.append("xdom_jitter_us 不能为负：%r" % (jit,))
        except (TypeError, ValueError):
            errs.append("xdom_jitter_us 不是数字：%r" % (jit,))

    ev = (contract.get("evidence") or {}).get("exec")
    if ev and ev not in ("bounded", "empirical"):
        warns.append("evidence.exec=%r 未识别（只认 bounded / empirical），"
                     "按 empirical 处理" % (ev,))
    # 声称"已证明"就得拿得出凭据。bounded 意味着有可信上界（WCET / 静态上界），
    # 只有实测均值却标 bounded，等于把经验预测冒充硬实时证明——
    # 这种声明必须拒，而不是仅仅提示。
    if ev == "bounded" and contract.get("wcet_us") is None:
        errs.append(
            "证据：evidence.exec=bounded 但没有 wcet_us（或有界执行时间凭据）。"
            "反馈死区满足上限 ≠ 证明了控制稳定；"
            "没有可信成本就只能标 empirical、出实验性方案")

    if period is not None and period_dag is not None:
        try:
            if abs(float(period_dag) - float(period)) > 1e-9:
                warns.append("契约 period_us=%.0f 与 DAG 的 %.0f 不一致，"
                             "以契约为准" % (float(period), float(period_dag)))
        except (TypeError, ValueError):
            pass

    # ---- 跨域部署可行性（PR11-C）----
    # 结构对了不等于排得下：契约声明了各域周期，就必须存在安全相位窗，
    # 否则这个部署在"数据年龄 ≤ 预算"下**不可实现**——生成期就该拒，
    # 而不是生成出来靠运行时拒收兜底（PR10-F §14 已实证拒收救不回）。
    try:
        import xdom_timeline as _tl
        e2, w2 = _tl.check_contract(dag, contract)
        errs.extend(e2)
        warns.extend(w2)
    except Exception as ex:      # 判不了就 fail-closed，绝不静默放行
        errs.append("跨域部署可行性检查无法执行：%s: %s" % (type(ex).__name__, ex))

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
