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

    # ---- 周期性重同步（PR11-D §25）：三个字段都必须落在 timing_constraints 下 ----
    # 写在别处会被 emit 静默忽略（它只读 timing_constraints），那等于"配了没生效"——
    # 与 PR10-E 那个 feedback_delay_max_us 写错位置的坑同型，这里显式判错。
    if isinstance(ctrl_blk, dict):
        for _k in ("xdom_resync_us", "xdom_resync_at_us", "xdom_resync_cooldown_us",
                   "xdom_resync_hi_us"):
            if _k in ctrl_blk:
                errs.append("契约：%s 写在顶层 control 下（非契约位置）；"
                            "请移到 timing_constraints 下——emit 只读那里" % _k)
    _rs = ctr.get("xdom_resync_us")
    _rat = ctr.get("xdom_resync_at_us")
    _rcool = ctr.get("xdom_resync_cooldown_us")
    _rhi = ctr.get("xdom_resync_hi_us")
    for _name, _v in (("xdom_resync_us", _rs), ("xdom_resync_at_us", _rat),
                      ("xdom_resync_cooldown_us", _rcool),
                      ("xdom_resync_hi_us", _rhi)):
        if _v is None:
            continue
        try:
            _f = float(_v)
            if _f < 0:
                errs.append("%s 不能为负：%r" % (_name, _v))
        except (TypeError, ValueError):
            errs.append("%s 不是数字：%r" % (_name, _v))
    # 开了平移却没有触发阈值 ⇒ 永远不会触发（配了等于没配）
    # 例外：只给上边界（hi）而不给下边界（at）是合法的——那正是"只守涨、不守掉"的配置。
    try:
        if _rs and float(_rs) > 0 and not ((_rat and float(_rat) > 0)
                                           or (_rhi and float(_rhi) > 0)):
            errs.append("xdom_resync_us>0 但 xdom_resync_at_us 与 xdom_resync_hi_us 都为 0："
                        "没有触发阈值，重同步永远不会发生（配了等于没配）")
    except (TypeError, ValueError):
        pass
    # ---- 双向重同步的两个边界（PR11-D §26 补的"另一半"）----
    # 为什么这些必须是**错**而不是警告：每一条都对应"配了不生效"或"生效了但方向反"，
    # 而这类装置的错误在板上表现为"重同步跑了、样本还是掉了"，最难归因。
    try:
        _hi = float(_rhi) if _rhi is not None else 0.0
        _at = float(_rat) if _rat is not None else 0.0
        _st = float(_rs) if _rs is not None else 0.0
    except (TypeError, ValueError):
        _hi = _at = _st = 0.0
    # 年龄预算（µs）：上/下边界的判据都要拿它当尺子。
    _budget_us = None
    try:
        if age_budget is not None and period is not None:
            _budget_us = int(float(age_budget)) * float(period)
    except (TypeError, ValueError):
        _budget_us = None
    if _hi > 0:
        if _st <= 0:
            errs.append("xdom_resync_hi_us>0 但 xdom_resync_us 为 0："
                        "上边界没有平移量，触发也没东西可搬")
        if _at > 0 and _at >= _hi:
            errs.append("xdom_resync_at_us(%.0f) ≥ xdom_resync_hi_us(%.0f)："
                        "两个门限交叉，同一拍会同时满足两个条件、方向互相抵消。"
                        "必须 at < hi（下边界在下、上边界在上）" % (_at, _hi))
        # 上边界的意义是"在**越界之前**搬回来"。门限不早于预算 ⇒ 等越界才动作，
        # 而越界那一拍已经被拒收了（拒收发生在同一拍的 age 判定里）。
        if _budget_us is None:
            errs.append("xdom_resync_hi_us>0 但没有 xdom_max_age_ticks："
                        "没有年龄预算就没有'越界'这回事，上边界无处安放")
        elif _hi >= _budget_us:
            errs.append("xdom_resync_hi_us(%.0f) ≥ 年龄预算 %.0f µs："
                        "触发点不早于越界点 ⇒ 越界那一拍已经拒收，重同步来晚了"
                        % (_hi, _budget_us))
        elif _budget_us - _hi < 2 * _st:
            warns.append("xdom_resync_hi_us(%.0f) 距预算 %.0f µs 只有 %.0f µs，"
                         "不足 2 个步长(%.0f)：一次抖动就可能直接跨过触发点越界"
                         % (_hi, _budget_us, _budget_us - _hi, 2 * _st))
    # 平移量必须小于一个周期：后移会让"这一拍"变长，前移会让它变短；
    # 一旦 |d| ≥ P，前移就会跨过上一拍的释放时刻（时间倒退）或吞掉整拍。
    if _st > 0 and period is not None and _st >= float(period):
        errs.append("xdom_resync_us(%.0f) ≥ period_us(%.0f)：平移量不能达到一个整拍，"
                    "否则前移会让本拍早于上一拍（时间倒退）" % (_st, float(period)))
    # 步长必须小于两个门限之间的距离：否则"前移一次"会直接落到下边界以下，
    # 下一拍就被下边界抓回去——年龄在两个极端之间来回搬，比不搬更差。
    # 冷却期只是**掩盖**了这种振荡（15 s 内不允许再搬），不是设计正确。
    if _st > 0 and _hi > 0 and _at > 0 and _st >= (_hi - _at):
        errs.append("xdom_resync_us(%.0f) ≥ hi−lo = %.0f µs：一次前移会直接落到下边界以下，"
                    "两个门限会来回搬（年龄在两个极端之间跳，带宽 = hi−lo 而不是 step）。"
                    "要求 step < hi−lo" % (_st, _hi - _at))
    # 下边界的对称约束：搬完不能越过预算。参考模型给的是"带宽退化"——
    # lo=400/step=700 时稳态带宽不是 700 而是**整周期 1000**（后移后立刻越界，
    # 年龄被迫在回绕与预算之间来回），比"余量不足"严重一档，所以判错不判警告。
    if _st > 0 and _at > 0 and _budget_us is not None and _at + _st >= _budget_us:
        errs.append("xdom_resync_at_us + xdom_resync_us = %.0f ≥ 年龄预算 %.0f µs："
                    "后移之后立刻越界，年龄会在回绕与预算之间来回，"
                    "带宽退化成整个周期（模型实测：lo=400/step=700 时带 0…1000）"
                    % (_at + _st, _budget_us))

    # ---- 重同步必须**只有一个域**动手（PR11-D §26.4）----
    # 为什么这是硬错：age 是两个域网格的**相对**相位，只有一个自由度。两侧各按
    # 自己的门限搬，等于对同一个量下两次控制；实测两侧在同一秒一个 +500 一个 −500，
    # 相对相位一次被推 1000 µs（整拍），当场制造了 41 次拒收。
    if isinstance(ctrl_blk, dict) and "xdom_resync_domain" in ctrl_blk:
        errs.append("契约：xdom_resync_domain 写在顶层 control 下（非契约位置）；"
                    "请移到 timing_constraints 下——emit 只读那里")
    _rsdom = ctr.get("xdom_resync_domain")
    if _rsdom is not None and not isinstance(_rsdom, str):
        errs.append("xdom_resync_domain 要写域名（字符串），不是 %r" % (_rsdom,))
        _rsdom = None
    if _st > 0 and not _rsdom:
        errs.append("xdom_resync_us>0 但没有 xdom_resync_domain：年龄是两域的**相对**相位，"
                    "只有一个自由度，必须写明哪一侧动手（另一侧完全不生成这套代码）。"
                    "两侧都配会把相对相位一次推过整拍")
    if _rsdom:
        _dom_of = {n.get("name"): n.get("domain") for n in (dag.get("nodes") or [])}
        _doms = {d for d in _dom_of.values() if d}
        if _rsdom not in _doms:
            errs.append("xdom_resync_domain=%r 不是本方案的域（可用：%s）"
                        % (_rsdom, ", ".join(sorted(_doms)) or "无"))
        else:
            # 主域必须有**跨域入边**：桥只在 sub 时触发；没有入边就永远不会触发。
            _inbound = [e for e in (dag.get("edges") or [])
                        if _dom_of.get(e.get("to")) == _rsdom
                        and _dom_of.get(e.get("from")) not in (None, _rsdom)]
            if not _inbound:
                errs.append("xdom_resync_domain=%r 没有任何跨域入边："
                            "它是纯生产者，桥里没有 sub，重同步永远不会触发（配了等于没配）"
                            % (_rsdom,))

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
