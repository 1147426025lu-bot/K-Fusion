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
        self.dag = dag
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
        # ⚠ 字段位置容错（PR10-E）。`control.feedback_delay_max_us` 是**非契约位置**：
        #   TemporalModel 原来只读 timing_constraints，而这唯一一份跨域夹具恰好把它
        #   写在顶层 control 下 ⇒ 反馈上限**从未被读到**，且没有任何提示。
        #   这里做**显式规范化并记录**，绝不静默忽略；严格入口
        #   （contract_validate）会把这种位置判成错误。
        self.notes = []
        legacy_fb = _num((contract.get("control") or {}).get("feedback_delay_max_us"))
        if legacy_fb is not None and _num(ctr.get("feedback_delay_max_us")) is None:
            self.notes.append(
                "契约把 feedback_delay_max_us 写在顶层 control 下（非契约位置），"
                "已按 %.0f µs 规范化；应移到 timing_constraints 下" % legacy_fb)
        self.feedback_max = _first(
            _num(ctr.get("feedback_delay_max_us")),
            legacy_fb,
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

    # -------- 反馈死区（PR10-E：规划器与验证器的唯一实现）--------
    def _member_offset(self, members, member):
        """成员在组内的执行起点（相对组释放时刻，µs）。

        组的第一次释放要付释放代价 c，组内按方案顺序累加各阶段执行时间。
        口径与规划器完全一致——**这就是同一个函数**，不允许各写一份。

        ⚠ `members` 必须是**名字**列表。规划器的 `solve()` 返回的组是 order 的
        **下标**列表，调用方必须先换成名字；直接拿下标去查 phase/exec 会静默
        全取默认值（相位全 0、执行全 0），算出来的死区永远等于一整拍——
        看起来"很合理"，实际是没算。
        """
        off = self.c
        for m in members:
            if m == member:
                return off
            off += float(self.exec_us.get(m, 0.0))
        return off

    def feedback_delays(self, groups, dom=None, sched=None):
        """每条反馈闭合边的**总死区**，带来源分解（规划器/验证器共用）。

        公式（PR10-B 定下，PR10-D 在板上验证过）：

            dead_us = (1 + xd) * period + t_cons - t_prod

        · `1`   ：闭合边自身的 z⁻¹。生产者相位晚于消费者相位，一拍之内不可能
                  满足，只能等到**下一个周期边界**——延迟由调度强制。
        · `xd`  ：跨域附加的那一拍（dom.is_cross 为真时取 1）。实测依据 PR10：
                  1 kHz 下跨域往返 p50 = 1005.26 µs（≈1.005 拍）。
        · `t_cons - t_prod`：两者在各自组内的偏移之差（含组释放代价与执行时间）。

        ⚠ **两笔不能分开各加一次**。跨域闭合边"既是 z⁻¹ 又是跨域"，但它们
        描述的是同一个"等到下一个边界"，所以合并成 (1+xd)；重复累加会把 2 拍
        算成 3 拍（PR10-E 实现时踩过）。

        sched：`schedule(groups)` 的输出。给了就用**实际开始时刻**（含组间积压），
        没给就用名义相位。死区是"生产者写完到消费者读到"的真实间隔，
        被积压推后的组只会让它更长，用名义相位会低估。
        """
        period = float(self.period or 0.0)
        rows = list(sched or [])
        members_of, start_of = {}, {}
        for gi, g in enumerate(groups or []):
            gid = g.get("id") or ("g%d" % gi)
            members = list(g.get("members") or [])
            members_of[gid] = members
            nominal = float(g.get("release_phase_us") or 0.0)
            actual = (float(rows[gi].get("start_us", nominal))
                      if gi < len(rows) and rows[gi] is not None else nominal)
            start_of[gid] = actual
        gid_of = {}
        for gid, members in members_of.items():
            for m in members:
                gid_of[m] = gid

        out = []
        for r in (self.dag.get("recurrences") or []):
            for c in (r.get("closing_edges") or []):
                a, b = c.get("from"), c.get("to")
                ga, gb = gid_of.get(a), gid_of.get(b)
                if ga is None or gb is None:
                    continue
                t_prod = start_of[ga] + self._member_offset(members_of[ga], a)
                t_cons = start_of[gb] + self._member_offset(members_of[gb], b)
                cross = bool(dom is not None and dom.is_cross(a, b))
                xd = 1 if cross else 0
                dead = ((1 + xd) * period + t_cons - t_prod) if period else None
                # 跨域那部分单独拆出来，便于报"哪来的"：一个周期边界 vs 一拍
                out.append({
                    "from": a, "to": b,
                    "from_group": ga, "to_group": gb,
                    "same_group": ga == gb,
                    "cross_domain": cross,
                    "dead_us": round(dead, 3) if dead is not None else None,
                    "components": {
                        "z1_periods": 1,
                        "cross_domain_periods": xd,
                        "period_us": period,
                        "producer_offset_us": round(t_prod, 3),
                        "consumer_offset_us": round(t_cons, 3),
                        "phase_gap_us": round(t_cons - t_prod, 3),
                    },
                    "unmerged_us": c.get("delay_us_unmerged"),
                    "cap_us": self.feedback_max,
                })
        return out

    def feedback_violations(self, groups, dom=None, sched=None):
        """把 feedback_delays 的结果按契约上限判一遍。

        返回 (errors, warnings)，元素是 (code, msg)。

        三条规则（PR10-E）：
        1. 死区 > 上限 → 报错。**跨组也算**——以前只看"反馈边并入同一组"，
           反馈边跨组（跨域）时直接跳过，于是"死区因跨域翻倍"在验证器里是静默的。
        2. **这条反馈边自己跨域**、但契约没给上限 → 报错。
           跨域恰恰是把死区多拉一整拍的那一步，没有上限就无法证明这一步
           保住了时序语义，按 fail-closed 拒。（反馈边不跨域、只是方案别处
           跨 domain 的不强行要求：那种情形死区仍是一拍，与单域同构。）
        3. 上限在内 → 提示（保留数值，便于对读）。
        """
        errs, warns = [], []
        delay_list = self.feedback_delays(groups, dom=dom, sched=sched)
        if not delay_list:
            return errs, warns
        cap = self.feedback_max
        for f in delay_list:
            tag = "%s→%s" % (f["from"], f["to"])
            if cap is None:
                # 只在**这条反馈边自己跨域**时才要求契约给上限：
                # 跨域正是把死区多加一整拍的那一步，没有上限就无法证明
                # 这一步保住了时序语义 ⇒ fail-closed。
                # 反馈边不跨域、只是方案别处跨 domain，则不强行要求
                # （那种情形死区仍是一拍，与单域同构）。
                if f["cross_domain"]:
                    errs.append((
                        "FEEDBACK_CONTRACT_MISSING",
                        "控制：反馈边 %s 跨域（%s → %s），但契约没有给 "
                        "feedback_delay_max_us。跨域会把死区拉长一整拍，"
                        "没有上限就无法证明这一步保住了时序语义（fail-closed）"
                        % (tag, f["from_group"], f["to_group"])))
                # 反馈边不跨域又没有上限：**不额外告警**。
                # 没给上限就没有可判的约束，而死区数值本来就会出现在
                # recurrence_report 里（"间隔 200 → 998 µs"）；再补一条
                # warning 只会让 --fail-on-warning 的单域夹具无端变红。
                continue
            if f["dead_us"] is not None and f["dead_us"] > float(cap) + 1e-9:
                if f["same_group"]:
                    errs.append((
                        "FEEDBACK_FUSED",
                        "控制：反馈边 %s 并入同组，死区≈%.0f µs（一整拍）"
                        "超过上限 %.0f µs，必须跨组"
                        % (tag, f["dead_us"], float(cap))))
                else:
                    errs.append((
                        "FEEDBACK_DEADZONE",
                        "控制：反馈边 %s 跨组（%s → %s），死区 = %d 拍"
                        "（z⁻¹ %d 拍 ＋ 跨域 %d 拍）＝ %.0f µs，超过上限 %.0f µs；"
                        "跨域拆分会把这个反馈环的延迟加长，回路可能因此失稳"
                        % (tag, f["from_group"], f["to_group"],
                           f["components"]["z1_periods"]
                           + f["components"]["cross_domain_periods"],
                           f["components"]["z1_periods"],
                           f["components"]["cross_domain_periods"],
                           f["dead_us"], float(cap))))
            else:
                warns.append(
                    "控制：反馈边 %s 死区 %.0f µs%s（上限 %.0f µs）"
                    % (tag, f["dead_us"] or 0.0,
                       "，跨域 +1 拍" if f["cross_domain"] else "", float(cap)))
        return errs, warns

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
