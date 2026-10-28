# -*- coding: utf-8 -*-
"""event_interpreter — Gate 3：与规划器分离的「独立事件解释器」。

给定 TEIR 的**数据版本模型** + 一份执行 trace（每周期各阶段的进入/退出时间戳），
**不信任**规划器，独立反推并校验时序语义是否兑现：

  · same_period 边 A→B：同周期内 A 退出时刻 ≤ B 进入时刻
        （生产者先写完，消费者才读——功能等价 ≠ 时间正确 的那条硬约束）
  · prev_period 边 A→B（lag=L）：B(k) 读的是 A(k−L) 的写
        ⇒ A(k−L) 的退出时刻 ≤ B(k) 的进入时刻
  · 期限：每周期跨度 (max exit − min enter) ≤ deadline_us

trace 格式：[{k: 周期号, stages: {阶段名: (enter_us, exit_us), ...}}, ...]
时间戳单位与 deadline_us 一致（µs）。
返回 {ok, errors, warnings}。
"""

import math


def check_trace(teir, trace, deadline_us=None):
    """独立校验执行 trace 是否满足 TEIR 声明的版本/期限约束。

    teir 需要 data_edges：[{from, to, version, lag_periods, xdom}, ...]
    """
    data_edges = teir.get("data_edges") or []
    # 周期号 -> {name: (enter, exit)}
    periods = {}
    for rec in (trace or []):
        k = rec.get("k")
        periods[k] = dict(rec.get("stages") or {})

    errors = []
    warnings = []

    for e in data_edges:
        a, b = e.get("from"), e.get("to")
        ver = e.get("version")
        lag = int(e.get("lag_periods") or 0)
        for k, stages_k in periods.items():
            if b not in stages_k:
                continue
            t_enter_b = stages_k[b][0]
            src_k = k if ver == "same_period" else (k - lag)
            src = periods.get(src_k)
            if src is None or a not in src:
                warnings.append("边 %s->%s 周期 %s 缺生产者记录（源周期 %s）"
                                % (a, b, k, src_k))
                continue
            t_exit_a = src[a][1]
            if t_exit_a > t_enter_b + 1e-9:
                errors.append(
                    "版本违例 %s->%s(%s, lag=%d) 周期 %s：生产者退出 %.3f > 消费者进入 %.3f"
                    % (a, b, ver, lag, k, t_exit_a, t_enter_b))

    # 期限：每周期跨度
    if deadline_us is not None:
        for k, stages_k in periods.items():
            if not stages_k:
                continue
            all_t = [t for (ent, ex) in stages_k.values() for t in (ent, ex)]
            span = max(all_t) - min(all_t)
            if span > float(deadline_us) + 1e-9:
                errors.append("期限违例 周期 %s：跨度 %.3f > deadline %.3f"
                             % (k, span, float(deadline_us)))

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


# ---------------- 一个最小 TEIR 构造助手（测试 / 离线复核用） ----------------
def make_teir(data_edges):
    """从边列表构造 check_trace 所需的极简 teir（只含 data_edges）。"""
    return {"data_edges": data_edges}
