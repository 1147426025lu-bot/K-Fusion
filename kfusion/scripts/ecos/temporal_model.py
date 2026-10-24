# -*- coding: utf-8 -*-
"""ECoS 统一时间语义模型（v4，PR9-B）。

一次控制周期内，阶段的**实际执行时刻**（绝对，相对周期起点 kT）：

    inst(m) = head_phase(g) + L + Σ_{k ∈ g, k 早于 m} C_k

  L   释放→开始代价（实测 c：root cell ≈20 µs，NuttX ≈6 µs）
  C_k 阶段执行时间

契约里的窗口一律是**绝对周期内时刻**：

    earliest_us ≤ inst(m) ≤ latest_us

为什么不能再用「相对组释放的漂移」：

    PR8 v1 只检查 off（组内偏移）是否超过一个标量阈值。这只能发现
    **被推后**，发现不了**被前拉**——而融合把后段前拉到头相位，主要
    语义风险恰恰是前拉（提前采样 / 提前输出）。

    反例：B 原计划 400 µs 采样，窗口 [380, 420]；融进 0 µs 头组后
    inst(B) = 0 + 20 + 0.6 = 20.6 µs。相对偏移只有 0.6 µs，
    旧实现判「没问题」；绝对时刻却已越窗 359 µs。

同一套定义被两处消费：

    · 规划器 group_legal()  —— 搜索时判可行性（不能搜出非法方案）
    · PlanVerifier          —— 复检整份方案（不信任求解器输出）

两者共用本模块，保证「算得出的」和「验得出的」是同一个语义。
"""


def _num(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


class TemporalModel(object):
    """把 DAG / 契约里的时间信息归一化成一个可查询对象。"""

    def __init__(self, dag, contract=None, exec_us=None, release_cost_us=20.0):
        dag = dag or {}
        contract = contract or {}
        meta_task = ((dag.get("meta") or {}).get("task") or {})
        ctr = contract.get("timing_constraints") or {}
        ctask = contract.get("task") or {}
        phase_blk = dag.get("phase") or {}

        self.c = float(release_cost_us)
        self.exec_us = exec_us or {}

        # ---- 周期 / 期限：契约 > DAG phase > meta.task ----
        self.period = _first(_num(ctr.get("period_us")), _num(ctask.get("period_us")),
                             _num(phase_blk.get("period_us")),
                             _num(meta_task.get("period_us")))
        self.deadline = _first(_num(ctask.get("deadline_us")),
                               _num(ctr.get("deadline_us")),
                               _num(meta_task.get("deadline_us")))

        # ---- 绝对窗口 ----
        self.sample = self._windows(ctr.get("sample"), "sample")
        self.actuator = self._windows(ctr.get("actuator") or ctr.get("output"),
                                      "actuator")

        # ---- 反馈死区上限：契约 > DAG.control ----
        self.feedback_max = _first(
            _num(ctr.get("feedback_delay_max_us")),
            _num((dag.get("control") or {}).get("max_feedback_delay_us")))

        # ---- 证据级别 ----
        # bounded：有可信上界（WCET 分析 / 静态上界）；empirical：只有实测量级。
        # 只有 bounded 才允许宣称"证明了硬实时期限"。
        self.evidence = (contract.get("evidence") or {}).get("exec") \
            or ("bounded" if contract.get("wcet_us") else "empirical")
        self.has_abs_windows = bool(self.sample or self.actuator)
        self.source = ("contract" if (contract.get("timing_constraints")
                                      or contract.get("task")) else
                       ("dag" if (self.has_abs_windows or self.feedback_max is not None)
                        else "none"))

    # -------- 解析 --------
    @staticmethod
    def _windows(node, kind):
        """接受单条或多条 {stage, earliest_us, latest_us}。"""
        if node is None:
            return {}
        items = node if isinstance(node, list) else [node]
        out = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            stage = it.get("stage") or it.get("name")
            lo = _first(_num(it.get("earliest_us")), _num(it.get("min_us")),
                        _num((it.get("window") or {}).get("earliest_us")))
            hi = _first(_num(it.get("latest_us")), _num(it.get("max_us")),
                        _num((it.get("window") or {}).get("latest_us")))
            if stage and (lo is not None or hi is not None):
                out[stage] = (lo, hi, kind)
        return out

    def window_of(self, stage):
        """绝对窗口 (lo, hi, kind)；同一阶段同时有两类时取更严的那条。"""
        cands = [w for w in (self.sample.get(stage), self.actuator.get(stage))
                 if w is not None]
        if not cands:
            return None
        lo = max((w[0] for w in cands if w[0] is not None), default=None)
        hi = min((w[1] for w in cands if w[1] is not None), default=None)
        return (lo, hi, cands[0][2])

    # -------- 时间线 --------
    def timeline(self, members, head_phase):
        """组内每个成员的绝对执行时刻。

        返回 [{stage, offset_us, instant_us}]，offset 是相对组释放的偏移。
        也返回组完成时刻 fin = head + c + ΣC。
        """
        off = self.c
        rows = []
        for m in members:
            rows.append({
                "stage": m,
                "offset_us": round(off, 3),
                "instant_us": round(float(head_phase) + off, 3),
            })
            off += float(self.exec_us.get(m, 0.0))
        return rows, round(float(head_phase) + off, 3)

    # -------- 违规检查 --------
    def violations(self, members, head_phase):
        """绝对窗口违规列表（前拉、推后都抓）。"""
        rows, _fin = self.timeline(members, head_phase)
        bad = []
        for r in rows:
            w = self.window_of(r["stage"])
            if not w:
                continue
            lo, hi, kind = w
            t = r["instant_us"]
            label = "采样" if kind == "sample" else "输出生效"
            if lo is not None and t < lo - 1e-9:
                bad.append({
                    "code": "WINDOW_EARLY", "stage": r["stage"], "kind": kind,
                    "instant_us": t, "lo": lo, "hi": hi,
                    "msg": ("控制：%s 的%s时刻 %.1f µs 早于窗口下界 %.1f µs"
                            "（融合把它前拉了 %.1f µs = 提前%s）"
                            % (r["stage"], label, t, lo, lo - t, label)),
                })
            if hi is not None and t > hi + 1e-9:
                bad.append({
                    "code": "WINDOW_LATE", "stage": r["stage"], "kind": kind,
                    "instant_us": t, "lo": lo, "hi": hi,
                    "msg": ("控制：%s 的%s时刻 %.1f µs 晚于窗口上界 %.1f µs"
                            "（%s被推迟 %.1f µs）"
                            % (r["stage"], label, t, hi, label, t - hi)),
                })
        return bad

    def legacy_drift(self, members, windows):
        """旧语义：节点级 sample_window_us / output_window_us（相对组释放的漂移）。

        保留它是为了不破坏已有 DAG；新契约请用绝对窗口。
        """
        if not windows:
            return []
        rows, _ = self.timeline(members, 0.0)
        bad = []
        for r in rows:
            w = windows.get(r["stage"])
            if w is None:
                continue
            if r["offset_us"] > float(w) + 1e-9:
                bad.append({
                    "code": "LEGACY_DRIFT", "stage": r["stage"],
                    "offset_us": r["offset_us"], "limit_us": float(w),
                    "msg": ("控制（旧语义）：%s 组内偏移 %.1f µs 超过相对上限 %.1f µs"
                            % (r["stage"], r["offset_us"], float(w))),
                })
        return bad

    # -------- 期限 --------
    def response_us(self, groups, exec_us=None, phase_known=True):
        """端到端完成时刻（相对周期起点）与响应时间。

        completion = max_g (head_phase(g) + c + ΣC_g)
        response   = completion − head_phase(第一组)
        """
        ex = exec_us if exec_us is not None else self.exec_us
        completion, first_head = 0.0, None
        for g in groups:
            members = g.get("members") or []
            head = g.get("release_phase_us")
            head = float(head) if head is not None else 0.0
            if first_head is None:
                first_head = head
            _rows, fin = self.timeline(members, head)
            completion = max(completion, fin)
        if first_head is None:
            return 0.0, 0.0
        return completion, round(completion - first_head, 3)

    def deadline_violation(self, groups):
        if self.deadline is None:
            return None
        completion, resp = self.response_us(groups)
        if completion > self.deadline + 1e-9:
            return {
                "code": "DEADLINE", "completion_us": completion,
                "deadline_us": self.deadline, "response_us": resp,
                "evidence": self.evidence,
                "msg": ("期限：末组完成 %.1f µs 超过期限 %.1f µs（超出 %.1f µs）%s"
                        % (completion, self.deadline, completion - self.deadline,
                           "" if self.evidence == "bounded"
                           else "［证据级别 empirical：这是经验预测，不是硬实时证明］")),
            }
        return None
