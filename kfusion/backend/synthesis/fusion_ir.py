# -*- coding: utf-8 -*-
"""融合 IR（fusion IR）：已验证的 ECoS 方案 → 两个后端共用的中间表示。

为什么要单独一层
----------------
PR9-C 要求 Linux `.ko` 与 NuttX 程序都从**同一个方案**生成。如果两个 emitter
各自去读 plan.json，它们对"组顺序/相位/调用表达式"的理解迟早会分叉——
那正是 P0-1 的同类错误（同一份事实两套解释）。所以先把 plan 归一化成 IR：

    plan.json（已验证） + dag.json + contract
        → FusionIR（阶段/组/相位/调用/执行时间/禁止效果）
            → linux_kernel_emitter / nuttx_emitter

IR 只描述"一拍里要执行什么、在什么相位、调用哪个函数"，**不含任何
调度原语**（不写 sleep、不写 timer、不写线程）——调度是宿主的事。
这条边界是审查明确要求的：生成的周期体不得自带 `clock_nanosleep()`。

在环（ring-0）里的额外约束
--------------------------
内核态不能睡眠、不能阻塞、不能做未归档的外部调用。平台能力表已经挡了一部分，
但 IR 层再挡一次（`ring0_violations()`），避免"方案是给用户态验的、却拿去编内核"。
"""

import json


class IRError(Exception):
    pass


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class Stage(object):
    def __init__(self, name, node, call, exec_us, phase_us):
        self.name = name
        self.call = call
        self.exec_us = float(exec_us or 0.0)
        self.effects = list(node.get("effects") or [])
        self.contexts = list(node.get("contexts") or node.get("allowed_contexts") or [])
        self.phase_us = None if phase_us is None else float(phase_us)

    @property
    def ident(self):
        """C 标识符（阶段名可能含 . 或 -）。"""
        out = []
        for ch in self.name:
            out.append(ch if (ch.isalnum() or ch == "_") else "_")
        s = "".join(out)
        return s if not s[:1].isdigit() else "s_" + s


class Group(object):
    def __init__(self, gid, stages, phase_us):
        self.gid = gid
        self.stages = stages
        self.phase_us = None if phase_us is None else float(phase_us)

    @property
    def body(self):
        return "ecos_body_%s" % _ident(self.gid)

    @property
    def exec_us(self):
        return sum(s.exec_us for s in self.stages)


def _ident(s):
    out = []
    for ch in s:
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out)
    return s if not s[:1].isdigit() else "s_" + s


class FusionIR(object):
    def __init__(self, period_us, deadline_us, groups, order, release_cost_us,
                 evidence, source_name, meta=None):
        self.period_us = period_us
        self.deadline_us = deadline_us
        self.groups = groups
        self.order = order
        self.release_cost_us = release_cost_us
        self.evidence = evidence
        self.source_name = source_name
        self.meta = meta or {}

    # ------------------------------------------------------------------
    @classmethod
    def from_plan(cls, plan, dag, contract=None, exec_us=None, calls=None,
                  source_name="fused"):
        calls = calls or {}
        exec_us = exec_us or {}
        nodes = {n["name"]: n for n in (dag.get("nodes") or [])}
        order = dag.get("topo_order") or dag.get("topo_order_delayed") or []
        phase_blk = dag.get("phase") or {}
        phase_known = bool(phase_blk.get("known"))
        offsets = phase_blk.get("offsets") or {}
        period = (phase_blk.get("period_us")
                  or ((dag.get("meta") or {}).get("task") or {}).get("period_us")
                  or (contract or {}).get("task", {}).get("period_us"))
        deadline = ((contract or {}).get("task") or {}).get("deadline_us") \
            or ((dag.get("meta") or {}).get("task") or {}).get("deadline_us")

        raw_groups = plan.get("groups") or []
        if not raw_groups:
            raise IRError("方案没有 groups")

        groups = []
        for gi, g in enumerate(raw_groups):
            gid = g.get("id") or ("g%d" % gi)
            members = list(g.get("members") or [])
            if not members:
                raise IRError("组 %s 没有成员" % gid)
            stages = []
            for m in members:
                node = nodes.get(m)
                if node is None:
                    raise IRError("组 %s 含 DAG 里不存在的阶段 %r" % (gid, m))
                ph = offsets.get(m)
                if ph is None:
                    ph = node.get("phase_us")
                call = calls.get(m) or node.get("call") or ("%s()" % m)
                stages.append(Stage(m, node, call, exec_us.get(m, 0.0), ph))
            phase = g.get("release_phase_us")
            if phase is None and phase_known:
                phase = min(s.phase_us for s in stages if s.phase_us is not None) \
                    if any(s.phase_us is not None for s in stages) else None
            groups.append(Group(gid, stages, phase))

        # 顺序一致性：IR 展平后的顺序必须等于方案展平顺序（防上游乱序）
        flat_plan = [m for g in raw_groups for m in (g.get("members") or [])]
        flat_ir = [s.name for g in groups for s in g.stages]
        if flat_plan != flat_ir:
            raise IRError("IR 展平顺序与方案不一致：%s vs %s" % (flat_ir, flat_plan))
        if order and set(flat_ir) != set(order):
            raise IRError("方案阶段集合与 DAG 不一致：%s vs %s"
                          % (sorted(flat_ir), sorted(order)))

        evidence = "empirical"
        if (contract or {}).get("wcet_us"):
            evidence = "bounded"
        if ((contract or {}).get("evidence") or {}).get("exec"):
            evidence = contract["evidence"]["exec"]

        cm = plan.get("cost_model") or {}
        release_cost = cm.get("release_cost_us")
        if release_cost is None:
            release_cost = (plan.get("meta") or {}).get("release_cost_us")
        ir = cls(period, deadline, groups, flat_ir,
                 float(release_cost if release_cost is not None else 20.0),
                 evidence, source_name)
        ir.phase_known = phase_known
        ir.plan_version = plan.get("version")
        ir.verified = bool((plan.get("verification") or {}).get("ok"))
        return ir

    # ------------------------------------------------------------------
    def ring0_violations(self):
        """内核态禁止项。返回 [(stage, reason)]，空表示可以进 ring-0。"""
        bad = []
        for g in self.groups:
            for s in g.stages:
                eff = set(s.effects)
                if eff & {"sleep", "blocking_io"}:
                    bad.append((s.name, "含 sleep/blocking_io：周期体必须在一个周期预算内"
                                         "跑完；内核线程里睡眠会直接错过相位并拖住整核。"
                                         "这种阶段只能留在 NuttX / 用户态"))
                if "external" in eff:
                    bad.append((s.name, "含 external（语义未归档）：不能在 ring-0 执行"))
                if "unknown" in eff:
                    bad.append((s.name, "含 unknown（效果不可证明）：fail-closed"))
        return bad

    def timing_violations(self):
        """生成前的最低时间前提：相位/周期必须已知，且组相位是绝对周期内时刻。"""
        bad = []
        if not self.period_us:
            bad.append((None, "没有 period_us：周期体无法定节拍"))
        if not self.phase_known:
            bad.append((None, "相位未知：无法按相位释放，只能退化成 G·c 串行模型，"
                              "生成物会丢掉方案的核心语义"))
        for g in self.groups:
            if g.phase_us is None:
                bad.append((g.gid, "组没有释放相位"))
            elif self.period_us and g.phase_us >= float(self.period_us):
                bad.append((g.gid, "组相位 %.1f 超出周期 %.1f" % (g.phase_us, self.period_us)))
        return bad

    def to_json(self):
        return {
            "period_us": self.period_us,
            "deadline_us": self.deadline_us,
            "release_cost_us": self.release_cost_us,
            "evidence": self.evidence,
            "verified": self.verified,
            "groups": [
                {"id": g.gid, "phase_us": g.phase_us, "exec_us": round(g.exec_us, 3),
                 "body": g.body,
                 "stages": [{"name": s.name, "call": s.call, "exec_us": s.exec_us,
                             "effects": s.effects, "phase_us": s.phase_us}
                            for s in g.stages]}
                for g in self.groups
            ],
            "order": self.order,
        }
