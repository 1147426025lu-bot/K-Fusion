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

    # -------- 统一时间线（PR9-D：唯一的状态递推）--------
    def schedule(self, groups):
        """单核顺序执行的统一时间线。

            S_g = max(φ_g, F_{g-1}) + L
            F_g = S_g + Σ_{i∈g} C_i

        **为什么必须只此一处**

            规划器 solve_dp() 早就用 max(φ_g, 上一组完成) 递推（否则它会低估算力），
            但验证器的 response_us() 曾经对每组**独立**按相位算完成时刻再取 max。
            两者在"组执行时间 > 相位间隔"时不一致，而且低估是危险方向：

            实测反例（backlog 夹具）：a@0 执行 500、b@100 执行 500、L=20
              · 独立算：max(0+20+500, 100+20+500) = 620 µs  → 期限 1000 判通过
              · 真实：  F_a=520，S_b=max(100,520)+20=540，F_b=1040 → 违约 40 µs

            所以这里给出唯一实现，规划器/验证器/代码生成都调它。
            跨域/多核要在此基础上加资源与通信约束，而不是另写一套。
        """
        rows = []
        prev_finish = 0.0
        for g in groups:
            members = list(g.get("members") or [])
            phi = g.get("release_phase_us")
            phi = float(phi) if phi is not None else 0.0
            start = max(phi, prev_finish) + self.c
            exec_sum = sum(float(self.exec_us.get(m, 0.0)) for m in members)
            finish = start + exec_sum
            rows.append({
                "id": g.get("id"),
                "members": members,
                "phase_us": round(phi, 3),
                "backlog_us": round(max(0.0, prev_finish - phi), 3),
                "start_us": round(start, 3),
                "finish_us": round(finish, 3),
                "exec_us": round(exec_sum, 3),
            })
            prev_finish = finish
        return rows

    def stage_instants(self, row):
        """组内每个阶段的绝对执行时刻（基于**实际**开始时刻，不是相位）。"""
        off = float(row["start_us"])
        out = []
        for m in row["members"]:
            out.append({"stage": m, "instant_us": round(off, 3),
                        "offset_us": round(off - float(row["start_us"]), 3)})
            off += float(self.exec_us.get(m, 0.0))
        return out

    # -------- 时间线 --------
    def timeline(self, members, head_phase):
        """组内每个成员的绝对执行时刻（**不含**组间积压；单段分析用）。

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
    def _check_stage_windows(self, stage, instant):
        w = self.window_of(stage)
        if not w:
            return []
        lo, hi, kind = w
        label = "采样" if kind == "sample" else "输出生效"
        bad = []
        if lo is not None and instant < lo - 1e-9:
            bad.append({
                "code": "WINDOW_EARLY", "stage": stage, "kind": kind,
                "instant_us": instant, "lo": lo, "hi": hi,
                "msg": ("控制：%s 的%s时刻 %.1f µs 早于窗口下界 %.1f µs"
                        "（融合把它前拉了 %.1f µs = 提前%s）"
                        % (stage, label, instant, lo, lo - instant, label)),
            })
        if hi is not None and instant > hi + 1e-9:
            bad.append({
                "code": "WINDOW_LATE", "stage": stage, "kind": kind,
                "instant_us": instant, "lo": lo, "hi": hi,
                "msg": ("控制：%s 的%s时刻 %.1f µs 晚于窗口上界 %.1f µs"
                        "（%s被推迟 %.1f µs）"
                        % (stage, label, instant, hi, label, instant - hi)),
            })
        return bad

    def violations(self, members, head_phase):
        """单段检查（规划器搜索时用；按相位当开始时刻，不含组间积压）。"""
        rows, _fin = self.timeline(members, head_phase)
        bad = []
        for r in rows:
            bad.extend(self._check_stage_windows(r["stage"], r["instant_us"]))
        return bad

    def violations_at(self, row):
        """按**实际开始时刻**检查窗口（验证器用；含组间积压）。"""
        bad = []
        for r in self.stage_instants(row):
            bad.extend(self._check_stage_windows(r["stage"], r["instant_us"]))
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
        """端到端完成时刻与响应时间（**含组间积压**，走统一递推）。

        completion = F_末组（schedule 的最后一行）
        response   = completion − φ_首组
        """
        rows = self.schedule(groups)
        if not rows:
            return 0.0, 0.0
        completion = rows[-1]["finish_us"]
        return completion, round(completion - rows[0]["phase_us"], 3)

    def deadline_violation(self, groups):
        """整份方案的期限检查（含积压）。这是唯一的期限判据。"""
        if self.deadline is None:
            return None
        rows = self.schedule(groups)
        if not rows:
            return None
        completion = rows[-1]["finish_us"]
        resp = round(completion - rows[0]["phase_us"], 3)
        if completion > self.deadline + 1e-9:
            worst = max(rows, key=lambda r: r["backlog_us"])
            return {
                "code": "DEADLINE", "completion_us": completion,
                "deadline_us": self.deadline, "response_us": resp,
                "backlog_us": worst["backlog_us"], "backlog_group": worst["id"],
                "evidence": self.evidence,
                "msg": ("期限：末组完成 %.1f µs 超过期限 %.1f µs（超出 %.1f µs）%s"
                        "%s"
                        % (completion, self.deadline, completion - self.deadline,
                           "" if self.evidence == "bounded"
                           else "［证据级别 empirical：这是经验预测，不是硬实时证明］",
                           ("；其中 %s 因前组未完成被推迟了 %.1f µs"
                            % (worst["id"], worst["backlog_us"]))
                           if worst["backlog_us"] > 0 else "")),
            }
        return None
