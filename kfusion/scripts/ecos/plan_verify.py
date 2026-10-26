# -*- coding: utf-8 -*-
"""ECoS 全局方案验证器（PR9-A）。

**为什么要独立于求解器**

    求解器内部的 group_legal() 只回答「这一段能不能并成一拍」——它是
    局部谓词。但「整份部署方案是否合法」是全局性质：每个组单独合法、
    成员也全覆盖，并不推出组间执行顺序合法。

    实证（修复前，commit 95293b8）：把 four_stage 的拓扑序完全倒过来，
    写成 4 个单节点组

        stage_clamp | stage_pid | stage_fir | stage_sample

    ——先算执行器输出、最后才采样——`--verify` 返回 **exit=0，四组全绿**。
    原因是当时只做了「逐组 group_legal + 覆盖检查」，没有任何组间顺序校验。

    所以本模块不调用 group_legal()，而是自己从 DAG 重建事实（拓扑序、
    同拍边、跨周期闭合边、冲突、未证依赖、域能力），对**整份方案**做
    四类校验：完整性 / 执行顺序 / 合法性 / 时间语义。

**跨周期边怎么处理**

    `recurrences[].closing_edges` 里的边是相位反向边，只能跨拍满足（z⁻¹）。
    它们**不能**参与同拍顺序约束——否则五阶段闭环（plant@800 → sample@0）
    会被误判成越序。验证器把它们单独拿出来，只检查「是否被融进同一组」
    导致死区变成一整拍。

**证据级别**

    执行时间只有实测量级时，期限检查输出的是**经验预测**，不得宣称
    「已证明硬实时期限」。验证器把这一条写进报告，不藏在脚注里。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

import temporal_model  # noqa: E402
import domain_model    # noqa: E402


def _reach(nodes, edges):
    succ = {n: set() for n in nodes}
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a in succ and b in succ:
            succ[a].add(b)
    out = {}
    for n in nodes:
        seen, stack = set(), list(succ[n])
        while stack:
            u = stack.pop()
            if u in seen:
                continue
            seen.add(u)
            stack.extend(succ[u])
        out[n] = seen
    return out


class PlanVerifier(object):
    def __init__(self, dag, platform, release_cost_us=20.0, exec_us=None,
                 budget_us=None, contract=None):
        dag = dag or {}
        self.dag = dag
        self.contract = contract or {}
        self.plat = platform or {}
        self.exec_us = exec_us or {}
        self.budget = budget_us
        self.c = float(release_cost_us)

        self.nodes = [n["name"] for n in (dag.get("nodes") or [])]
        self.node = {n["name"]: n for n in (dag.get("nodes") or [])}
        self.order = dag.get("topo_order") or dag.get("topo_order_delayed") or []
        self.pos = {n: i for i, n in enumerate(self.order)}

        # 跨周期闭合边（z⁻¹）：不参与同拍顺序约束
        self.closing = set()
        self.closing_meta = {}
        for r in (dag.get("recurrences") or []):
            for c in (r.get("closing_edges") or []):
                key = (c.get("from"), c.get("to"))
                self.closing.add(key)
                self.closing_meta[key] = c

        # 时间与跨域两个模型都先建好：后面的"同拍边"定义要用到它们
        self.tm = temporal_model.TemporalModel(
            dag, contract=contract, exec_us=self.exec_us,
            release_cost_us=self.c)

        # 同拍边 = 非闭合边、非冲突边、**非跨域边**
        # 跨域边与 z⁻¹ 同构：生产者在一个 cell、消费者在另一个 cell，只能在下一个
        # 周期边界看到它，所以它不约束同一拍内的先后（也不该被它否掉合法方案）。
        self.dom = domain_model.load(dag, period_us=self.tm.period,
                                     topo=self.order)
        self.edges = [e for e in (dag.get("edges") or [])
                      if (e.get("from"), e.get("to")) not in self.closing
                      and (e.get("from"), e.get("to")) not in self.dom.xdom_set]
        self.same_cycle = [e for e in self.edges if e.get("kind") != "conflict"]
        self.conflicts = [e for e in self.edges if e.get("kind") == "conflict"]
        self.reach = _reach(self.nodes, self.same_cycle)

        self.candidates = dag.get("candidates") or []
        self.unresolved = {n["name"]: n["interface_unresolved"]
                           for n in (dag.get("nodes") or [])
                           if n.get("interface_unresolved")}

        # 旧语义（节点级相对漂移）仍然支持，但只作 deprecated 通道
        self.legacy_sample = {n["name"]: float(n["sample_window_us"])
                              for n in (dag.get("nodes") or [])
                              if n.get("sample_window_us") is not None}
        self.legacy_output = {n["name"]: float(n["output_window_us"])
                              for n in (dag.get("nodes") or [])
                              if n.get("output_window_us") is not None}

    # ------------------------------------------------------------------
    def verify(self, plan):
        errs, warns = [], []
        sched = []
        groups = (plan or {}).get("groups") or []
        add = lambda code, msg: errs.append({"code": code, "msg": msg})

        if not groups:
            add("EMPTY_PLAN", "方案没有任何组")
            return self._report(errs, warns, plan)

        # ---- 1) 结构完整性：成员已知、不重复、不遗漏 ----
        flat = []
        for gi, g in enumerate(groups):
            members = list(g.get("members") or [])
            gid = g.get("id") or ("g%d" % gi)
            if not members:
                add("EMPTY_GROUP", "组 %s 没有成员" % gid)
                continue
            for m in members:
                if m not in self.node:
                    add("UNKNOWN_NODE", "组 %s 含未知成员 %s" % (gid, m))
                flat.append(m)
        seen, dup = set(), set()
        for m in flat:
            (dup if m in seen else seen).add(m)
        if dup:
            add("DUP_NODE", "成员重复出现：%s" % ", ".join(sorted(dup)))
        missing = [n for n in self.nodes if n not in seen]
        if missing:
            add("MISSING_NODE", "覆盖不完整：漏掉 %s" % ", ".join(sorted(missing)))
        extra = [m for m in seen if m not in self.node]
        if extra:
            add("MISSING_NODE", "覆盖不完整：多出未知成员 %s" % ", ".join(sorted(extra)))

        # ---- 2) 执行顺序 ----
        # 2a) 扁平化成一条执行序（单核顺序执行模型），下标即实际执行次序
        seq = [m for m in flat if m in self.node]
        idx = {}
        for i, m in enumerate(seq):
            idx.setdefault(m, i)

        # 2b) 组内顺序必须与拓扑序一致
        for gi, g in enumerate(groups):
            members = [m for m in (g.get("members") or []) if m in self.pos]
            posv = [self.pos[m] for m in members]
            if posv != sorted(posv):
                first_bad = None
                for k in range(1, len(posv)):
                    if posv[k] < posv[k - 1]:
                        first_bad = members[k]
                        break
                add("INTRA_ORDER",
                    "组 %s 的成员顺序与拓扑序相反（%s 被排到了它依赖的节点之前）"
                    % (g.get("id"), first_bad))

        # 2c) 组间顺序：同拍边的两端必须保持 before 关系
        for e in self.same_cycle:
            a, b = e.get("from"), e.get("to")
            if a not in idx or b not in idx:
                continue
            if idx[a] >= idx[b]:
                add("EDGE_ORDER",
                    "依赖越序：%s→%s（%s@第%d位 未早于 %s@第%d位）%s"
                    % (a, b, a, idx[a], b, idx[b],
                       "，证据 %s" % ",".join(e.get("evidence") or [])
                       if e.get("evidence") else ""))

        # 2d) 不可分性：组内两人之间不得夹着组外节点（融合不能跨过它执行）
        for gi, g in enumerate(groups):
            members = [m for m in (g.get("members") or []) if m in self.node]
            mset = set(members)
            for x in self.nodes:
                if x in mset:
                    continue
                for a in members:
                    if x not in self.reach.get(a, set()):
                        continue
                    for b in members:
                        if a != b and b in self.reach.get(x, set()):
                            add("INSEPARABLE",
                                "不可分性：%s 必须位于 %s 与 %s 之间，"
                                "组 %s 跨过了它（执行次序会颠倒）"
                                % (x, a, b, g.get("id")))
                            break

        # 2e) 冲突（WAW，无方向）：只能保序，不得同组、不得反向
        for e in self.conflicts:
            a, b = e.get("from"), e.get("to")
            if a in idx and b in idx:
                if idx[a] == idx[b] or (idx[a] > idx[b]) != (self.pos.get(a, 0) > self.pos.get(b, 0)):
                    add("CONFLICT_ORDER",
                        "不确定：WAW 冲突 %s/%s 被重排（原序 %s→%s，"
                        "方案里 %s 在 %s 之后），保序融合不能保证 happens-before"
                        % (a, b, a, b, a, b))
            same = [g for g in groups if a in (g.get("members") or [])
                    and b in (g.get("members") or [])]
            if same:
                add("CONFLICT_FUSED",
                    "不确定：WAW 冲突 %s/%s 落在段内但无已证明顺序，"
                    "保序融合不能保证 happens-before（顺序不确定）" % (a, b))

        # 2f) 未证明依赖 / 接口未闭合：fail-closed
        for c in self.candidates:
            a, b = c.get("from"), c.get("to")
            same = [g for g in groups if a in (g.get("members") or [])
                    and b in (g.get("members") or [])]
            if same:
                add("UNPROVEN_FUSED",
                    "不确定：未证明依赖 %s→%s 落在段内，不能确定保持原始相对顺序"
                    "（fail-closed）" % (a, b))
        for m, reasons in self.unresolved.items():
            for g in groups:
                members = g.get("members") or []
                if m in members and len(members) > 1:
                    add("UNRESOLVED_FUSED",
                        "不确定：%s 接口未闭合（%s），与其它阶段同组后域合法性不可信"
                        "（fail-closed）" % (m, ",".join(reasons)))

        # ---- 3) 各组合法性（域能力 / 容量）----
        for gi, g in enumerate(groups):
            members = [m for m in (g.get("members") or []) if m in self.node]
            if not members:
                continue
            gid = g.get("id") or ("g%d" % gi)
            eff, ctx = set(), None
            for m in members:
                n = self.node[m]
                eff |= set(n.get("effects") or [])
                allowed = set(n.get("contexts") or n.get("allowed_contexts") or [])
                ctx = allowed if ctx is None else (ctx & allowed)
            if "mmio" in eff and not self.plat.get("allow_mmio"):
                add("DOMAIN_MMIO", "域合法：组 %s 含 mmio，但平台 %s allow_mmio=false"
                    % (gid, self.plat.get("name")))
            if self.plat.get("allow_sleep") is False and (eff & {"sleep", "blocking_io"}):
                add("DOMAIN_SLEEP", "域合法：组 %s 含 sleep/blocking_io，但平台不允许"
                    % gid)
            if "external" in eff:
                add("DOMAIN_EXTERNAL", "域合法：组 %s 含 external（语义未归档），"
                    "按 fail-closed 拒绝" % gid)
            if "unknown" in eff:
                add("DOMAIN_UNKNOWN", "域合法：组 %s 含 unknown（无法证明），拒绝" % gid)
            if ctx is not None and not (ctx & set(self.plat.get("contexts") or [])):
                add("DOMAIN_CTX", "域合法：组 %s 的上下文交集 %s 与平台 %s 无交集"
                    % (gid, sorted(ctx), self.plat.get("contexts")))
            tot = sum(float(self.exec_us.get(m, 0.0)) for m in members)
            if self.budget and tot > self.budget + 1e-9:
                add("CAPACITY", "期限：组 %s 执行 %.1f µs 超过预算 %.1f µs"
                    % (gid, tot, self.budget))
            if self.tm.period and tot > float(self.tm.period) + 1e-9:
                add("CAPACITY_PERIOD", "单拍容量：组 %s 执行 %.1f µs 超过周期 %.1f µs"
                    % (gid, tot, float(self.tm.period)))
            # 3b) 跨域不可融合：融合要求"同一次释放"，两个 cell 各有内核与调度器，
            #     物理上不存在跨域融合。这条要独立判，不能信规划器自己的结论。
            if self.dom.mixed(members):
                add("DOMAIN_MIXED",
                    "跨域：组 %s 的成员分属 %s，跨域融合不存在"
                    "（不同 cell 各有内核与调度器），必须切开"
                    % (gid, " / ".join(self.dom.domains_of(members))))

        # ---- 4) 时间语义（走统一时间线，含组间积压）----
        # 只按相位算会低估完成时刻：前一组没跑完，本组就得推迟。
        sched = self.tm.schedule(groups)
        for gi, g in enumerate(groups):
            members = [m for m in (g.get("members") or []) if m in self.node]
            if not members:
                continue
            gid = g.get("id") or ("g%d" % gi)
            row = sched[gi] if gi < len(sched) else None
            if row is not None:
                for v in self.tm.violations_at(row):
                    add(v["code"], "组 %s：%s" % (gid, v["msg"]))
                if row["backlog_us"] > 0:
                    warns.append(
                        "组 %s：相位 %.1f µs，实际开始 %.1f µs（被前组推迟 %.1f µs）"
                        % (gid, row["phase_us"], row["start_us"], row["backlog_us"]))
            for v in self.tm.legacy_drift(members, self.legacy_sample):
                add(v["code"], "组 %s：%s" % (gid, v["msg"]))
            for v in self.tm.legacy_drift(members, self.legacy_output):
                add(v["code"], "组 %s：%s" % (gid, v["msg"]))

        # 4b) 反馈闭合边：算**总死区**并对照契约上限。
        #     以前这里只判"反馈边并入同一组（融合）"这一种情形；反馈边跨组
        #     （含跨域）时直接跳过 ⇒ "死区因跨域翻倍"在验证器里是静默的。
        #     实测后果（PR10-D）：xdom_loop 单域稳定收敛，跨域后反馈延迟
        #     从 1 拍变 2 拍、回路发散到 ±60000，而工具链一路绿灯。
        #     现在死区计算与判据都走 temporal_model 里**同一份**实现，
        #     规划器与验证器不可能再给出两个不同的定义。
        fb_errs, fb_warns = self.tm.feedback_violations(
            groups, dom=self.dom, sched=sched)
        for code, msg in fb_errs:
            add(code, msg)
        warns.extend(fb_warns)
        for note in (getattr(self.tm, "notes", None) or []):
            warns.append("契约：%s" % note)

        # 4b-2) 证据级别与主张是否相称
        #     契约声称 bounded（有可信上界）却拿不出 wcet ⇒ 这是**未证明的证明**，
        #     拒绝这种声明；只有经验成本时只能出实验性方案。
        if self.tm.evidence == "bounded" and not (self.contract.get("wcet_us")):
            add("FEEDBACK_MODEL_UNPROVEN",
                "证据：契约声称 evidence.exec=bounded（硬实时上界），"
                "但没有 wcet_us 或任何有界执行时间凭据。"
                "反馈死区满足上限 ≠ 证明了控制稳定；没有可信成本就只能按 "
                "empirical 出实验性方案")

        # 4c) 期限（含组间积压）
        d = self.tm.deadline_violation(groups)
        if d:
            add(d["code"], d["msg"])

        # 4d) 跨域：每条跨域边多消耗一整拍（不是 µs）
        #     独立重算一次——规划器算的 extra_periods 不能自证。
        if self.dom.multi and self.dom.extra_periods:
            extra = self.dom.extra_latency_us
            comp = (sched[-1]["finish_us"] if sched else 0.0)
            if self.tm.deadline is not None and \
                    comp + extra > float(self.tm.deadline) + 1e-9:
                add("XDOM_DEADLINE",
                    "跨域期限：段内完成 %.1f µs ＋ 跨域 %d 拍（%.0f µs）＝ %.1f µs，"
                    "超过期限 %.1f µs；跨域边 %s"
                    % (comp, self.dom.extra_periods, extra, comp + extra,
                       float(self.tm.deadline),
                       ", ".join("%s→%s" % e for e in self.dom.xdom)))
            warns.append(
                "跨域：%d 条跨域边，端到端至少多 %d 拍（%.0f µs，按 %.0f µs/拍）"
                % (len(self.dom.xdom), self.dom.extra_periods, extra,
                   float(self.tm.period or 0)))

        rep = self._report(errs, warns, plan)
        if sched:
            rep["stats"]["completion_us"] = sched[-1]["finish_us"]
            rep["stats"]["backlog_us"] = round(
                sum(r["backlog_us"] for r in sched), 3)
            rep["schedule"] = sched
        return rep

    # ------------------------------------------------------------------
    def _head_phase(self, g, members):
        v = g.get("release_phase_us")
        if v is not None:
            return float(v)
        phases = []
        for m in members:
            n = self.node.get(m) or {}
            ph = n.get("phase_us")
            if ph is None:
                ph = ((self.dag.get("phase") or {}).get("offsets") or {}).get(m)
            if ph is not None:
                phases.append(float(ph))
        return min(phases) if phases else 0.0

    def _report(self, errs, warns, plan):
        return {
            "ok": not errs,
            "errors": errs,
            "warnings": warns,
            "stats": {
                "groups": len((plan or {}).get("groups") or []),
                "nodes": len(self.nodes),
                "same_cycle_edges": len(self.same_cycle),
                "cross_cycle_edges": len(self.closing),
                "evidence": self.tm.evidence,
                "temporal_source": self.tm.source,
                "domains": self.dom.domains,
                "cross_domain_edges": len(self.dom.xdom),
                "cross_domain_periods": self.dom.extra_periods,
                "cross_domain_extra_us": round(self.dom.extra_latency_us, 3),
            },
        }


def verify_plan(dag, plan, platform, release_cost_us=20.0, exec_us=None,
                budget_us=None, contract=None):
    v = PlanVerifier(dag, platform, release_cost_us, exec_us, budget_us, contract)
    return v.verify(plan)


def print_report(rep, plan=None, out=None):
    out = out or sys.stdout
    groups = (plan or {}).get("groups") or []
    for gi, g in enumerate(groups):
        gid = g.get("id") or ("g%d" % gi)
        members = g.get("members") or []
        print("· 组 %-4s %s" % (gid, " + ".join(members)), file=out)
    if rep["ok"]:
        print("✅ 方案验证通过（%d 组 · 同拍边 %d · 跨周期边 %d · 时间证据 %s）"
              % (rep["stats"]["groups"], rep["stats"]["same_cycle_edges"],
                 rep["stats"]["cross_cycle_edges"], rep["stats"]["evidence"]),
              file=out)
    else:
        print("❌ 方案验证失败：%d 条违规" % len(rep["errors"]), file=out)
        for e in rep["errors"]:
            print("   [%s] %s" % (e["code"], e["msg"]), file=out)
    for w in rep["warnings"]:
        print("   ⚠ %s" % w, file=out)
    return 0 if rep["ok"] else 3
