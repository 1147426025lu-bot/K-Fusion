#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fusion_plan__融合规划.py — 在单一执行域内选融合边界（ECoS 约束优化器 v3：PR6/7/8）

它回答一个问题：**这段任务图，哪些相邻阶段可以并成一个周期体？**

它回答一个问题：**这段任务图，哪些相邻阶段可以并成一个周期体？**

为什么"相邻"不等于"可以合并"
------------------------------
融合的实质是把几段代码放进**同一次释放**里连续执行，也就是**不允许别的阶段插在中间**。
所以合法条件不是"它们相邻"，而是：

> 段内任意两个成员之间，不能存在一个**段外**的阶段，它在所有合法顺序里都必须排在中间。

这条在链式图上永远成立（链上没有旁支），但在有分支/汇合的图上会直接失效——
`cyclictest` 里就有大量这种段（例如 `print_hist` 与 `timediff_us` 之间必须插入别的阶段）。
规划器必须能把它们判成**不可融合**，并且说清"是谁挡住了"。

三条合法性（任一不过就不许合）
------------------------------
1. **不可分性**（inseparability）：段内成员的依赖闭包里不能夹着段外成员。
2. **域合法**：段内效果并集必须被目标平台接受（`mmio` 不许进内核域、`sleep` 不许进中断上下文…）；
   段的执行上下文 = 成员 `allowed_contexts` 的**交集**，为空即不可行。
3. **相位**：**读外部输入**的阶段（`mmio`）把采样瞬间写进了控制律，不能前拉；
   并入更早相位的组会被提前执行 = 提前采样，语义变了（骨架可用 `phase_pinned` 覆盖）。
4. **期限 / 单拍容量**：段内执行时间之和不得超过给定预算，也不得超过周期本身
   （超过周期的组永远跑不完，这不是"紧"而是"不可能"）。

跨周期（v2）
------------
旧版一见环就拒。但采样控制回路**本来就是环**：执行器写完、传感器下一拍才读到。
区别在于环的闭合边是不是**相位反向**的：

- 相位反向（生产者写得比消费者晚）→ 该边只能跨拍满足，是 `z⁻¹`，**合法**；
- 环内全是相位同向 → 环必须在一拍内闭合，**代数环**，无合法执行顺序，拒。

`dag_build` 已把这两类算好（`recurrences` / `algebraic_loops`）并给出
摘掉延迟边后的**相位序**（`topo_order_delayed`），本规划器直接用。

延迟反馈环**不影响融合合法性**：只要组是相位序上的连续段，环闭合边的
时间间隔就仍然 ≥ 一拍——切开也好、并在一组也好，读到的都还是上一拍的值。
融合真正改变的是**后段的输出相位**（被前拉到组头），这一项单独报出来（"该不该合"）。

代价模型（PR7：相位感知端到端，参数全部显式）
---------------------------------------------
规划器区分两件不同的事：
- **唤醒税累计 = G·c**：每周期释放 G 次、每次付唤醒代价 c 的累计开销（融合砍掉的就是 (G−1)·c）。
- **端到端响应 R**（PR7）= 最后一组完成 − 第一组释放相位。串行单资源下每组在头相位一次性
  释放、组内连续执行，组释放 = max(头相位, 上一组完成)。相位未知时退化为 `R̂ = G·c + ΣE`。

`c` 不是拍出来的：它来自实测（root cell 的中位唤醒延迟 ≈20 µs，NuttX ≈6 µs），
用 `--release-cost-us` 传进来。**融合省唤醒税是确定性的；相位坍缩（如闭环 798 µs）是另一笔
独立的收益，旧模型 `G·c+ΣE` 看不见，新模型如实给出。**

合法性（PR6/PR8/PR9-B：fail-closed 与硬约束）
---------------------------------------------
除原有「不可分性 / 域合法 / 相位钉住(phase_pinned) / 期限容量」外，本版新增：
- PR6：段内含 `interface_unresolved` 节点、或 `conflict`/`candidate` 两端无已证明顺序 → 拒绝；
- PR8：`--max-feedback-delay-us`（绝对死区上限）超限直接拒绝；
- **PR9-B（v4）**：`--contract` 给出的采样/输出窗口是**绝对周期时刻**
  `[earliest_us, latest_us]`，实际时刻按 `头相位 + c + 组内偏移` 算，**前拉和推后都拦**。
  旧语义 `sample_window_us`（相对组释放的漂移）保留，但只抓"推后"，已标 deprecated。
  另加 `deadline_us`：单段完成超期即判该段不可用。

方案验证（PR9-A）
-----------------
`--verify` 用**独立验证器**（`ecos/plan_verify.py`），不再把逐组 `group_legal()`
的结论当成整份方案合法的证明——那条路径曾让"完全倒序的单节点组"exit=0 通过。
求解器自己的输出也必须过同一个验证器（写在 plan.verification 里），
分叉会当场报错而不是悄悄产出错误方案。

用法
----
    python fusion_plan__融合规划.py dag.json --platforms caps.json --platform linux_kernel \\
        --release-cost-us 20 -o plan.json
    python fusion_plan__融合规划.py dag.json --platforms caps.json --platform linux_kernel \\
        --contract contract.json -o plan.json          # 绝对时间窗口
    python fusion_plan__融合规划.py dag.json --verify plan.json      # 独立复检

退出码：0 = 有可行方案；2 = 输入不可规划（代数环 / 未给平台）；3 = 无可行方案 / 验证失败
"""
import argparse
import hashlib
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECOS = os.path.join(_HERE, "ecos")
if _ECOS not in sys.path:
    sys.path.insert(0, _ECOS)

import temporal_model  # noqa: E402  （与验证器共用同一套时间语义）

# v1 只用这几种；出现别的就报错，不静默忽略
ORDER_KINDS = ("call", "spawn", "data", "anti")


def load_json(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _cost_digest(release_cost_us, exec_us):
    """成本模型摘要：把"这次规划用的 c 与各阶段执行时间"钉死。

    下游（验证器/代码生成）拿 plan 时必须用**同一份**成本，摘要不一致就报错，
    而不是用缺省 0 悄悄算出另一套结论（P0-1 的成因）。
    """
    blob = json.dumps(
        {"c": round(float(release_cost_us), 6),
         "e": {k: round(float(v), 6) for k, v in sorted((exec_us or {}).items())}},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def cost_from_plan(plan):
    """从 plan.cost_model 取 (c, exec_us)；没有就返回 (None, None)。

    这是**唯一**的成本来源。命令行 --exec-us 只能用于"与它核对"，不能替代它。
    """
    cm = (plan or {}).get("cost_model") or {}
    c = cm.get("release_cost_us")
    e = cm.get("exec_us")
    return (None if c is None else float(c),
            None if e is None else {k: float(v) for k, v in e.items()})


def build_reach(nodes, edges):
    """可达闭包：reach[a] = a 能传递到达的节点集合（含自己）。"""
    succ = {n: set() for n in nodes}
    for e in edges:
        if e["kind"] in ORDER_KINDS and e["from"] in succ and e["to"] in succ:
            succ[e["from"]].add(e["to"])
    reach = {}
    for n in nodes:
        seen, stack = {n}, [n]
        while stack:
            u = stack.pop()
            for v in succ[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        reach[n] = seen
    return reach


class Planner:
    def __init__(self, dag, platform, release_cost_us, exec_us=None, budget_us=None,
                 contract=None):
        self.dag = dag
        self.plat = platform
        self.c = float(release_cost_us)
        self.exec_us = exec_us or {}
        self.budget = budget_us
        self.contract = contract or {}

        # 有环时用"摘掉延迟边后的相位序"；strict 拓扑序为空不代表不可规划，
        # 只代表这张图里有反馈——是否合法由 algebraic_loops 决定。
        self.order = dag.get("topo_order") or dag.get("topo_order_delayed") or []
        self.nodes = [n["name"] for n in dag.get("nodes") or []]
        if not self.order or set(self.order) != set(self.nodes):
            raise ValueError("拓扑序不可用（有环且无相位序，或缺节点），规划器不接受")
        self.edges = dag.get("edges") or []
        self.node = {n["name"]: n for n in dag.get("nodes") or []}
        self.reach = build_reach(self.nodes, self.edges)
        self.reasons = {}    # (i, j) -> 拒绝原因（用于解释）

        # ---- 跨周期 ----
        phase_blk = dag.get("phase") or {}
        self.phase_known = bool(phase_blk.get("known"))
        self.period = phase_blk.get("period_us") or \
            ((dag.get("meta") or {}).get("task") or {}).get("period_us")
        self.phase = {}
        for n in self.nodes:
            ph = phase_blk.get("offsets", {}).get(n)
            if ph is None:
                ph = self.node[n].get("phase_us")
            if ph is not None:
                self.phase[n] = float(ph)
        self.recurrences = dag.get("recurrences") or []
        self.algebraic = dag.get("algebraic_loops") or []

        # ---- 不确定依赖（PR6，fail-closed）----
        # 规划器过去完全忽略这三类信息；现在读进来并用于合法性判定。
        self.conflicts = [(e["from"], e["to"])
                         for e in (dag.get("edges") or [])
                         if e.get("kind") == "conflict"]
        self.candidates = dag.get("candidates") or []
        self.unresolved = {n["name"]: n["interface_unresolved"]
                          for n in (dag.get("nodes") or [])
                          if n.get("interface_unresolved")}

        # ---- 控制语义契约（PR8，硬约束）----
        # 不默认开启：仅当 DAG 显式给出控制窗 / 反馈延迟上限，或命令行指定时才生效。
        ctrl = dag.get("control") or {}
        self.max_fb_delay_us = ctrl.get("max_feedback_delay_us")
        self.sample_window = {n["name"]: float(n["sample_window_us"])
                             for n in (dag.get("nodes") or [])
                             if n.get("sample_window_us") is not None}
        self.output_window = {n["name"]: float(n["output_window_us"])
                             for n in (dag.get("nodes") or [])
                             if n.get("output_window_us") is not None}
        # ---- 统一时间语义（v4）：绝对周期窗口 ----
        # 与 PlanVerifier 共用同一个 TemporalModel，避免"算得出的"和
        # "验得出的"是两个不同的定义（那正是 P0-2 的成因）。
        self.tm = temporal_model.TemporalModel(
            dag, contract=self.contract, exec_us=self.exec_us,
            release_cost_us=self.c)
        if self.max_fb_delay_us is None:
            self.max_fb_delay_us = self.tm.feedback_max
        if self.period is None:
            self.period = self.tm.period

        # 反馈死区硬上限：并入同组后闭合边间隔≈一整拍（≤period）。
        # 若上限 < period，则每条反馈闭合边都必须跨组（否则死区必超限）。
        self.fb_must_cut = set()
        if self.max_fb_delay_us is not None and self.period:
            if self.max_fb_delay_us < float(self.period):
                for r in self.recurrences:
                    for c in (r.get("closing_edges") or []):
                        self.fb_must_cut.add((c["from"], c["to"]))
        self.legal = {}  # 段合法性表（穷举路径也会用，确保属性存在）

    # -------- 相位 --------
    def ph(self, name):
        return self.phase.get(name, 0.0)

    def phase_pinned(self, name):
        """相位敏感：不能把它挪到更早的相位执行。

        判据不是猜的：**读外部输入**的阶段（mmio）把"采样瞬间"写进了控制律里，
        提前执行 = 提前采样，语义变了。骨架可以用 `phase_pinned` 显式覆盖。
        """
        n = self.node.get(name) or {}
        if "phase_pinned" in n:
            return bool(n["phase_pinned"])
        return "mmio" in (n.get("effects") or [])

    def group_phase(self, members):
        return min((self.ph(m) for m in members), default=0.0)

    def phase_shifts(self, members):
        """融合把组内后段的**输出相位提前**了多少（µs）。

        这是融合真正的语义代价：组在头相位一次性释放，后面的阶段跟着被前拉。
        对控制器通常无害（等于缩短死区时间），但对"必须与外部事件对齐"的阶段是破坏。
        """
        head = self.group_phase(members)
        return [(m, round(self.ph(m) - head, 3))
                for m in members if self.ph(m) > head]

    # -------- 组合法性 --------
    def group_legal(self, members):
        """任意成员集合能否并成一个周期体；返回 (ok, reason)。

        注意：对**连续段**而言，合法性检查 1（不可分性）在合法拓扑序下恒成立——
        任何夹在两个段内成员之间的节点，必然也落在这一连续区间里。
        保留这条检查是为了 `--verify`：手工写的方案可能是**非连续**的
        （例如 {a, d} 跳过 b、c），那种情况下它才真正起作用。
        """
        members = list(members)
        mset = set(members)

        # 1) 不可分性：段外节点不得被"夹"在段内两个成员之间
        for x in self.nodes:
            if x in mset:
                continue
            for a in members:
                if x not in self.reach[a]:
                    continue
                for b in members:
                    if a != b and b in self.reach[x]:
                        return False, ("不可分性：%s 必须位于 %s 与 %s 之间"
                                       % (x, a, b))

        # 2) 域合法
        eff = set()
        ctx = None
        for m in members:
            n = self.node[m]
            eff |= set(n.get("effects") or [])
            allowed = set(n.get("contexts") or n.get("allowed_contexts") or [])
            ctx = allowed if ctx is None else (ctx & allowed)
        if "mmio" in eff and not self.plat.get("allow_mmio"):
            return False, "域合法：段内含 mmio，但平台 allow_mmio=false"
        if self.plat.get("allow_sleep") is False and (eff & {"sleep", "blocking_io"}):
            return False, "域合法：段内含 sleep/blocking_io，但平台 allow_sleep=false"
        if "external" in eff:
            return False, "域合法：段内含 external（语义未归档），按 fail-closed 拒绝"
        if "unknown" in eff:
            return False, "域合法：段内含 unknown（无法证明），拒绝"
        if ctx is not None and not (ctx & set(self.plat.get("contexts") or [])):
            return False, ("域合法：段的上下文交集 %s 与平台 %s 无交集"
                           % (sorted(ctx), self.plat.get("contexts")))
        if ctx is not None and not ctx:
            return False, "域合法：成员 allowed_contexts 交集为空"

        # 3) 相位：相位敏感的成员不能被前拉（前拉 = 提前采样/提前输出）
        if self.phase_known:
            head = self.group_phase(members)
            for m in members:
                if self.ph(m) > head and self.phase_pinned(m):
                    return False, ("相位：%s 在 %.0f µs 释放且读外部输入，"
                                   "并入 %.0f µs 的头组会把它前拉 %.0f µs"
                                   "（等于提前采样），须设为独立组的头"
                                   % (m, self.ph(m), head, self.ph(m) - head))

        # 4) 期限 / 单拍容量
        tot = sum(self.exec_us.get(m, 0.0) for m in members)
        if self.budget and tot > self.budget:
            return False, ("期限：段内执行 %.1f µs 超过预算 %.1f µs"
                           % (tot, self.budget))
        if self.period and tot > float(self.period):
            return False, ("单拍容量：段内执行 %.1f µs 超过周期 %.1f µs，"
                           "这组永远跑不完" % (tot, float(self.period)))

        # 5) 不确定依赖（PR6，fail-closed）：未解除的不确定性不允许改变相对结构
        for m in members:
            u = self.unresolved.get(m)
            if u:
                return False, ("不确定：%s 接口未闭合（%s），效果/上下文可能不完整，"
                               "域合法性不可信，按 fail-closed 拒绝"
                               % (m, ",".join(u)))
        for (a, b) in self.conflicts:
            if a in mset and b in mset and \
               a not in self.reach.get(b, set()) and b not in self.reach.get(a, set()):
                return False, ("不确定：WAW 冲突 %s/%s 落在段内但无已证明顺序，"
                               "保序融合不能保证 happens-before（顺序不确定）"
                               % (a, b))
        for c in self.candidates:
            a, b = c.get("from"), c.get("to")
            if a in mset and b in mset and \
               a not in self.reach.get(b, set()) and b not in self.reach.get(a, set()):
                return False, ("不确定：未证明依赖 %s→%s 落在段内但无已证明顺序，"
                               "不能确定保持原始相对顺序（fail-closed）" % (a, b))

        # 6) 控制语义契约（PR8 v4，硬约束；绝对周期窗口，前拉和推后都抓）
        head = self.group_phase(members)
        for v in self.tm.violations(members, head):
            return False, v["msg"]
        # 旧语义（节点级相对漂移）仍支持，仅作 deprecated 通道
        for v in self.tm.legacy_drift(members, self.sample_window):
            return False, v["msg"]
        for v in self.tm.legacy_drift(members, self.output_window):
            return False, v["msg"]
        if self.tm.deadline is not None:
            _rows, fin = self.tm.timeline(members, head)
            if fin > self.tm.deadline + 1e-9:
                return False, ("期限：本段排在 %.1f µs、完成于 %.1f µs，"
                               "超过期限 %.1f µs%s"
                               % (head, fin, self.tm.deadline,
                                  "" if self.tm.evidence == "bounded"
                                  else "［empirical：经验预测］"))
        for (a, b) in self.fb_must_cut:
            if a in mset and b in mset:
                return False, ("控制：反馈边 %s→%s 若并入同组，死区≈一整拍（%.0f µs）"
                               "超过上限 %.0f µs，必须跨组"
                               % (a, b, float(self.period or 0), self.max_fb_delay_us))
        return True, "ok %.1f µs" % tot

    def segment_legal(self, i, j):
        """order[i..j]（连续段）能否并成一个周期体。"""
        return self.group_legal(self.order[i:j + 1])

    # -------- DP：最小化端到端响应 R（PR7） --------
    # 串行单资源下，每组在「组内头相位」一次性释放，组内连续执行；
    # 组释放时刻 = max(头相位, 上一组完成时刻)。端到端
    #     R = 最后一组完成 − 第一组释放相位。
    # 相位未知时头相位全 0 → 退化为 G·c + ΣE（与旧模型一致）。
    def seg_head(self, i, j):
        h = None
        for k in range(i, j + 1):
            p = self.phase.get(self.order[k], 0.0)
            if h is None or p < h:
                h = p
        return h if h is not None else 0.0

    def solve_dp(self):
        n = len(self.order)
        INF = float("inf")
        # best[j] = 覆盖 order[0..j-1] 时，最后一组的**绝对完成时刻**（最小化）
        best = [INF] * (n + 1)
        prev = [-1] * (n + 1)
        best[0] = 0.0
        legal = {}
        first_head = (self.phase.get(self.order[0], 0.0)
                      if self.phase_known else 0.0)
        for j in range(1, n + 1):
            for i in range(j):
                if best[i] == INF:
                    continue
                ok, why = self.segment_legal(i, j - 1)
                legal[(i, j - 1)] = (ok, why)
                if not ok:
                    continue
                head = self.seg_head(i, j - 1)
                # 本组释放 = max(头相位, 上一组完成)；组间有相位间隔则等待，否则连续
                start = head if head > best[i] else best[i]
                seg_exec = sum(self.exec_us.get(self.order[k], 0.0)
                               for k in range(i, j))
                finish = start + self.c + seg_exec
                # 端到端 R 相同时，偏好更大段（即更少组 / 更少唤醒税）
                if finish < best[j] or (finish == best[j] and i > prev[j]):
                    best[j] = finish
                    prev[j] = i
        self.legal = legal
        if best[n] == INF:
            return None
        groups, j = [], n
        while j > 0:
            i = prev[j]
            groups.append(list(range(i, j)))
            j = i
        groups.reverse()
        # 端到端 R（相位感知）；相位未知时退化为 G·c + ΣE
        self.end_to_end_us = best[n] - first_head
        return groups

    def _finish_time(self, named_groups):
        """给定分组（名字列表），返回最后一组的绝对完成时刻（同 solve 的模型）。"""
        t = 0.0
        for g in named_groups:
            head = min((self.phase.get(m, 0.0) for m in g), default=0.0)
            start = head if head > t else t
            seg_exec = sum(self.exec_us.get(m, 0.0) for m in g)
            t = start + self.c + seg_exec
        return t

    def feedback_ok(self, groups_idx):
        if self.max_fb_delay_us is None:
            return True
        recs = self.recurrence_report(groups_idx)
        for r in recs:
            for c in r["closing_edges"]:
                d = c.get("delay_us_merged")
                if d is not None and d > self.max_fb_delay_us:
                    return False
        return True

    def solve_exhaustive(self):
        """反馈硬约束下（PR8）：枚举所有连续划分（N 小，2^(N-1) 种），
        选端到端 R 最小且反馈死区全部 ≤ 上限的方案。"""
        n = len(self.order)
        INF = float("inf")
        best_groups, best_R = None, INF
        first_head = (self.phase.get(self.order[0], 0.0)
                      if self.phase_known else 0.0)
        for mask in (range(1 << (n - 1)) if n > 1 else [0]):
            cuts = set()
            for cut in range(n - 1):
                if mask & (1 << cut):
                    cuts.add(cut)
            groups_idx, start = [], 0
            for cut in range(n - 1):
                if cut in cuts:
                    groups_idx.append(list(range(start, cut + 1)))
                    start = cut + 1
            groups_idx.append(list(range(start, n)))
            if any(not self.segment_legal(g[0], g[-1])[0] for g in groups_idx):
                continue
            named = [[self.order[i] for i in g] for g in groups_idx]
            if not self.feedback_ok(groups_idx):
                continue
            R = self._finish_time(named) - first_head
            if R < best_R:
                best_R, best_groups = R, groups_idx
        if best_groups is None:
            return None
        self.end_to_end_us = best_R
        return best_groups

    def solve(self):
        # 反馈硬约束使「端到端可行」成了全局耦合条件，前缀 DP 无法表达；
        # 此时退化为小规模穷举（连续划分），保证找到真正可行的边界。
        if self.fb_must_cut:
            return self.solve_exhaustive()
        return self.solve_dp()

    # -------- 控制：反馈死区绝对上限（PR8，硬约束） --------
    def apply_feedback_limit(self, us):
        """命令行覆盖反馈闭合边的绝对死区上限，并重算必须跨组的边。"""
        self.max_fb_delay_us = us
        self.fb_must_cut = set()
        if us is not None and self.period and us < float(self.period):
            for r in self.recurrences:
                for c in (r.get("closing_edges") or []):
                    self.fb_must_cut.add((c["from"], c["to"]))

    # -------- 不确定依赖汇总（PR6） --------
    def uncertainty_report(self):
        out = {
            "unresolved_nodes": sorted(self.unresolved.keys()),
            "conflicts": [list(p) for p in self.conflicts],
            "candidates": [{"from": c.get("from"), "to": c.get("to")}
                           for c in self.candidates],
        }
        # 这些不确定项是否真的挡住了某个融合段（出现在 rejected_segments 里）
        blocked = []
        for (i, j), (ok, why) in getattr(self, "legal", {}).items():
            if not ok and ("不确定" in why or "接口未闭合" in why):
                blocked.append({"segment": [self.order[x] for x in range(i, j + 1)],
                                "reason": why})
        out["blocked_segments"] = blocked
        return out

    # -------- 相邻两组合并的解释（论文里最有用的一段） --------
    def merge_report(self, groups):
        out = []
        for k in range(len(groups) - 1):
            i = groups[k][0]
            j = groups[k + 1][-1]
            ok, why = self.segment_legal(i, j)
            members = [self.order[x] for x in range(i, j + 1)]
            out.append({
                "merged": members,
                "allowed": ok,
                "reason": why,
                "saving_us": round(self.c, 3) if ok else 0.0,
                # 该不该合：能合 ≠ 该合。把后段前拉的量摆出来，让人自己判
                "phase_advanced_us": (self.phase_shifts(members) if ok else []),
                "cost_note": ("省一次释放 %.1f µs；代价：后段输出相位提前"
                              "（见 phase_advanced_us），隔离性下降"
                              % self.c) if ok else "",
            })
        return out

    # -------- 反馈环落在哪（跨周期部分的核心陈述） --------
    def _exec_offset_in_group(self, members, member):
        """成员在其所属组内的执行起点（相对组释放时刻，µs）。members 是**名字**列表。

        注意：`solve()` 返回的组是 order 的**下标**列表，调用方必须先换成名字——
        直接拿下标去查 phase/exec 会静默全取默认值（相位全 0、执行全 0），
        算出来的死区就永远等于一整拍，看起来"很合理"，实际是没算。
        """
        off = self.c
        for m in members:
            if m == member:
                return off
            off += self.exec_us.get(m, 0.0)
        return off

    def recurrence_report(self, groups):
        """每个延迟反馈环在方案里怎么落位、闭合边的间隔变成了多少。

        这是融合对反馈环**唯一**的实质影响：融合把组内后段前拉到组头，
        环闭合边的时间间隔随之改变（通常被拉长到接近一整拍）。
        采样序列**逐位不变**（读到的仍是上一拍的同一个值），
        但环的**死区时间**变了——对控制律的连续域含义而言这是真变化，必须报出来。
        """
        named = [[self.order[i] for i in g] for g in groups]
        where = {}
        for gi, g in enumerate(named):
            for m in g:
                where[m] = gi
        out = []
        for r in self.recurrences:
            gids = {m: where.get(m) for m in r["ring"]}
            cut, closes = [], []
            for c in r.get("closing_edges") or []:
                gp, gc = where.get(c["from"]), where.get(c["to"])
                if gp is None or gc is None:
                    continue
                t_prod = self.group_phase(named[gp]) + \
                    self._exec_offset_in_group(named[gp], c["from"])
                t_cons = self.group_phase(named[gc]) + \
                    self._exec_offset_in_group(named[gc], c["to"])
                period = float(self.period or 0.0)
                merged = (period + t_cons - t_prod) if period else None
                old = c.get("delay_us_unmerged")
                closes.append({
                    "from": c["from"], "to": c["to"],
                    "producer_offset_us": round(t_prod, 3),
                    "consumer_offset_us": round(t_cons, 3),
                    "delay_us_merged": round(merged, 3) if merged is not None else None,
                    "delay_us_unmerged": old,
                    "inflation": (round(merged / old, 2)
                                  if merged is not None and old else None),
                })
                if gp != gc:
                    cut.append({"from": c["from"], "to": c["to"],
                                "from_group": "g%d" % gp, "to_group": "g%d" % gc})
            out.append({
                "ring": r["ring"],
                "closing_edges": closes,
                "group_of": {m: ("g%s" % v if v is not None else None)
                             for m, v in gids.items()},
                "whole_in_one_group": not cut,
                "cut_at": cut,
                "verdict": (
                    ("环是强连通分量，不可分性规则下只能整体成组（本规划器不会切开环）"
                     if not cut else
                     "⚠ 环被切开在 %s：不可分性本应挡住，方案来自外部，需复核"
                     % ", ".join("%s(g%s)|%s(g%s)"
                                 % (c["from"], c["from_group"],
                                    c["to"], c["to_group"]) for c in cut))
                    + "；采样序列逐位不变，但闭合边间隔被拉长——那是回路的死区时间"),
            })
        return out


def summarize(planner, groups):
    out = []
    for gi, g in enumerate(groups):
        members = [planner.order[x] for x in g]
        eff, ctx = set(), None
        for m in members:
            n = planner.node[m]
            eff |= set(n.get("effects") or [])
            allowed = set(n.get("contexts") or n.get("allowed_contexts") or [])
            ctx = allowed if ctx is None else (ctx & allowed)
        out.append({
            "id": "g%d" % gi,
            "members": members,
            "effects": sorted(eff),
            "contexts": sorted(ctx or []),
            "exec_us": round(sum(planner.exec_us.get(m, 0.0) for m in members), 3),
            "release_phase_us": (planner.group_phase(members)
                                 if planner.phase_known else None),
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="单域融合边界规划（ECoS PR 4 v1）")
    ap.add_argument("dag")
    ap.add_argument("--platforms", required=False, default=None)
    ap.add_argument("--platform", default=None)
    ap.add_argument("--release-cost-us", type=float, default=None,
                    help="每次释放的唤醒代价（实测中位数；root cell≈20，NuttX≈6）。"
                         "缺省 20；复检时以 plan.cost_model 里的值为准")
    ap.add_argument("--exec-us", default=None,
                    help="JSON：{阶段名: 执行时间 µs}，缺省按 0 计（只比组数）")
    ap.add_argument("--budget-us", type=float, default=None,
                    help="段内执行时间上限（期限可行性）")
    ap.add_argument("--max-delay-inflation", type=float, default=None,
                    help="允许的反馈环死区时间放大倍数（融合把后段前拉，环闭合边因此变长）；"
                         "超过则记 warning（相对闸门，需配合 --fail-on-warning 才拒）")
    ap.add_argument("--max-feedback-delay-us", type=float, default=None,
                    help="反馈闭合边的**绝对**死区上限（µs）。超过则硬性拒绝（PR8），"
                         "不靠 --fail-on-warning。<= period 时每条反馈边都必须跨组")
    ap.add_argument("--contract", default=None,
                    help="时间/控制语义契约（v2）。给 {task:{period_us,deadline_us}, "
                         "timing_constraints:{sample:{stage,earliest_us,latest_us}, "
                         "actuator:{...}, feedback_delay_max_us}} 即可启用"
                         "**绝对周期窗口**（PR9-B）；不给则退回 DAG 内联字段")
    ap.add_argument("--fail-on-warning", action="store_true",
                    help="有 warning 时非零退出（CI 用）")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--verify", default=None, help="复检已有 plan.json 的合法性")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    dag = load_json(args.dag)
    if dag.get("algebraic_loops"):
        print("!! 检出**代数环**（拍内闭合），规划器拒绝：不存在合法执行顺序。",
              file=sys.stderr)
        for r in dag["algebraic_loops"]:
            print("   环 %s" % " -> ".join(r.get("ring") or []), file=sys.stderr)
            print("     %s" % r.get("why", ""), file=sys.stderr)
        print("   → 这不是融合算法能绕开的：要么模型错了（两个不同缓冲用了同名区域），"
              "要么设计错了（真的要求同一拍内首尾相接）。", file=sys.stderr)
        return 2
    if dag.get("cycles") and not (dag.get("topo_order") or
                                  dag.get("topo_order_delayed")):
        print("!! DAG 有环且无法摘出延迟边，规划器拒绝", file=sys.stderr)
        return 2

    if args.platforms:
        caps = load_json(args.platforms)
        plats = caps.get("platforms") or []
        if not plats:
            print("!! platform_caps 为空 → 无法判定", file=sys.stderr)
            return 2
        if args.platform and args.platform != "all":
            plats = [p for p in plats if p.get("name") == args.platform]
            if not plats:
                print("!! 找不到平台 %r" % args.platform, file=sys.stderr)
                return 2
        platform = plats[0]
    else:
        print("!! 必须给 --platforms（没有平台能力就无法判定合法性）",
              file=sys.stderr)
        return 2

    exec_us = None
    if args.exec_us:
        # 允许直接内联 JSON（演示时少建一个文件）
        exec_us = (json.loads(args.exec_us) if args.exec_us.lstrip().startswith("{")
                   else load_json(args.exec_us))
    planner = Planner(dag, platform,
                      (args.release_cost_us if args.release_cost_us is not None else 20.0),
                      exec_us, args.budget_us,
                      contract=(load_json(args.contract) if args.contract else None))
    if args.max_feedback_delay_us is not None:
        planner.apply_feedback_limit(args.max_feedback_delay_us)

    # 复检模式：独立验证器（PR9-A）。不调用 group_legal 的逐组结论当整体结论。
    if args.verify:
        import plan_verify
        plan = load_json(args.verify)
        cm = plan.get("cost_model") or {}
        pc, pe = cost_from_plan(plan)
        base_c = pc if pc is not None else planner.c
        # 成本模型必须一致：命令行给了 --exec-us 就核对摘要，不许各用一套
        if exec_us is not None and cm.get("digest") and \
                _cost_digest(base_c, exec_us) != cm["digest"]:
            print("!! 复检用的执行时间与方案里的 cost_model 不一致（摘要 %s ≠ %s）"
                  % (_cost_digest(base_c, exec_us), cm["digest"]), file=sys.stderr)
            print("   → 规划与复检必须用同一份成本，否则两边会得出不同结论。",
                  file=sys.stderr)
            return 2
        if pe is not None:
            exec_us = pe          # 以方案为准（P0-1：不传就退化成"执行时间=0"）
        rep = plan_verify.verify_plan(
            dag, plan, platform, base_c, exec_us, args.budget_us,
            contract=planner.contract)
        rc = plan_verify.print_report(rep, plan, sys.stdout)
        if not rep["ok"]:
            # 再落一份到 stderr，方便 CI 只抓错误流
            for e in rep["errors"]:
                print("❌ [%s] %s" % (e["code"], e["msg"]), file=sys.stderr)
            print("❌ 方案验证失败，禁止进入代码生成", file=sys.stderr)
        return rc

    groups = planner.solve()
    if groups is None:
        # 说清"为什么不行"。最要紧的是区分两种情况：
        #   (a) 某些节点**单独**就进不了这个域（外部调用未归档 / mmio 等）——
        #       此时任何划分都无解，问题不在融合；
        #   (b) 单节点都合法，但每组都因为"夹着段外节点"而不可分——
        #       这才是融合边界本身的问题。
        print("!! 无可行方案：所有分段都被判非法", file=sys.stderr)
        bad_single = []
        for k, name in enumerate(planner.order):
            ok, why = planner.segment_legal(k, k)
            if not ok:
                bad_single.append((name, why))
        if bad_single:
            timing_only = all(w.startswith("期限") for _, w in bad_single)
            if timing_only:
                print("   (a) 这些节点**单独**就已超出期限，任何划分都无解：",
                      file=sys.stderr)
            else:
                print("   (a) 这些节点**单独**就进不了平台 %s，任何划分都无解："
                      % planner.plat.get("name"), file=sys.stderr)
            for name, why in bad_single[:10]:
                print("       %-20s %s" % (name, why), file=sys.stderr)
            if len(bad_single) > 10:
                print("       …共 %d 个" % len(bad_single), file=sys.stderr)
            if timing_only:
                print("   → 这是**时间不可行**（期限过紧 / 相位太晚 / 释放代价过大），"
                      "不是融合能解决的：规划器只报不可行，"
                      "不会输出一个『最优』但违约的方案。", file=sys.stderr)
            else:
                print("   → 这属于「输入层」问题（效果未归档 / 域不允许），"
                      "不是融合算法能解决的。", file=sys.stderr)
        else:
            print("   (b) 单节点都合法，但合并后都不可分（见 rejected_segments）",
                  file=sys.stderr)
        return 3

    gs = summarize(planner, groups)
    total_exec = sum(g["exec_us"] for g in gs)
    # PR9-D：端到端一律走统一递推（含组间积压），不再各处自己算
    sched = planner.tm.schedule(gs)
    predicted = (round(sched[-1]["finish_us"] - sched[0]["phase_us"], 3)
                 if sched else (len(gs) * planner.c + total_exec))
    dp_pred = getattr(planner, "end_to_end_us", None)
    phase_span = (gs[-1]["release_phase_us"] - gs[0]["release_phase_us"]) \
        if planner.phase_known else 0.0
    recs = planner.recurrence_report(groups)

    # 整份方案的期限检查（唯一判据）：单段检查会**低估**积压，不能替它背书
    dv = planner.tm.deadline_violation(gs)
    if dv:
        print("!! %s" % dv["msg"], file=sys.stderr)
        for r in sched:
            if r["backlog_us"] > 0:
                print("   组 %s：相位 %.1f µs，实际开始 %.1f µs（被前组推迟 %.1f µs）"
                      % (r["id"], r["phase_us"], r["start_us"], r["backlog_us"]),
                      file=sys.stderr)
        print("   → 方案被拒。可以把该组的相位后移、缩短组执行时间，"
              "或放宽期限；不要拿『单段都合法』当整方案合法的证明。",
              file=sys.stderr)
        return 3
    warnings = []
    for r in recs:
        for c in r["closing_edges"]:
            if (args.max_delay_inflation and c.get("inflation")
                    and c["inflation"] > args.max_delay_inflation):
                warnings.append(
                    "环闭合边 %s→%s 的死区时间 ×%.2f（%.0f→%.0f µs）超过上限 ×%.2f"
                    % (c["from"], c["to"], c["inflation"],
                       c["delay_us_unmerged"], c["delay_us_merged"],
                       args.max_delay_inflation))
    # PR8 硬约束（绝对值）：任何反馈闭合边死区超过绝对上限 → 直接拒绝
    if planner.max_fb_delay_us is not None:
        for r in recs:
            for c in r["closing_edges"]:
                d1 = c.get("delay_us_merged")
                if d1 is not None and d1 > planner.max_fb_delay_us:
                    print("!! 反馈闭合边 %s→%s 死区 %.0f µs 超过绝对上限 %.0f µs，"
                          "方案被拒（PR8 硬约束）" % (c["from"], c["to"], d1,
                          planner.max_fb_delay_us), file=sys.stderr)
                    return 3
    plan = {
        "version": 4,
        "meta": {
            "dag": args.dag,
            "platform": platform.get("name"),
            "release_cost_us": planner.c,
            "assumption": ("端到端 R = 最后一组完成 − 第一组释放相位；"
                           "相位未知时退化为 R̂ = G·c + ΣE。c 来自实测，不是拟合参数"),
            "phase_known": planner.phase_known,
            "period_us": planner.period,
            "deadline_us": planner.tm.deadline,
            "temporal_source": planner.tm.source,
            "temporal_evidence": planner.tm.evidence,
            "contract": args.contract,
            "max_feedback_delay_us": planner.max_fb_delay_us,
        },
        "order": planner.order,
        "groups": gs,
        "recurrences": recs,
        "cost": {
            "groups": len(gs),
            "exec_total_us": round(total_exec, 3),
            "release_tax_us": round(len(gs) * planner.c, 3),
            "phase_span_us": round(phase_span, 3),
            "end_to_end_us": round(predicted, 3),
            "predicted_R_us": round(predicted, 3),
            "dp_predicted_R_us": (round(float(dp_pred), 3)
                                  if dp_pred is not None else None),
        },
        # 统一时间线：规划器/验证器/生成器都从这份表读，不再各算一遍
        "schedule": sched,
        # 成本模型随方案走：复检与代码生成**必须**用同一份执行时间，
        # 否则"规划时说满足、复检时按 0 执行时间说也满足"（P0-1 的成因）
        "cost_model": {
            "version": 1,
            "release_cost_us": planner.c,
            "exec_us": {k: float(v) for k, v in sorted(planner.exec_us.items())},
            "digest": _cost_digest(planner.c, planner.exec_us),
            "evidence": planner.tm.evidence,
        },
        "uncertainty": planner.uncertainty_report(),
        "merge_opportunities": planner.merge_report(groups),
        "warnings": warnings,
        "rejected_segments": [
            {"segment": [planner.order[x] for x in range(i, j + 1)],
             "reason": why}
            for (i, j), (ok, why) in sorted(planner.legal.items()) if not ok
        ],
    }

    # 求解器的输出必须过**独立**验证器。否则"能算出来的"和"能验出来的"
    # 又分成两套语义，P0-1 那类漏洞会再长回来。
    import plan_verify
    rep = plan_verify.verify_plan(dag, plan, platform, planner.c, exec_us,
                                  args.budget_us, contract=planner.contract)
    plan["verification"] = {"ok": rep["ok"], "errors": rep["errors"],
                           "warnings": rep["warnings"], "stats": rep["stats"]}
    if not rep["ok"]:
        print("!! 求解器给出的方案未通过独立验证器——求解器与验证器语义已分叉，"
              "必须修（不允许把这样的方案交给代码生成）", file=sys.stderr)
        for e in rep["errors"]:
            print("   [%s] %s" % (e["code"], e["msg"]), file=sys.stderr)
        return 3

    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
            f.write("\n")
    if not args.quiet:
        print("== 平台 %s · 释放代价 c=%.1f µs ==" % (platform.get("name"), planner.c))
        print("== 顺序 ==\n  " + " -> ".join(planner.order))
        if planner.phase_known:
            print("  （相位序：%s）"
                  % "  ".join("%s@%.0f" % (n, planner.ph(n))
                              for n in planner.order))
        print("== 方案：%d 组 ==" % len(gs))
        for g in gs:
            ph = ("" if not planner.phase_known
                  else "  t=%.0fµs" % g["release_phase_us"])
            print("  %-4s %-50s exec≈%6.1f µs%s  ctx=%s"
                  % (g["id"], " + ".join(g["members"]), g["exec_us"], ph,
                     ",".join(g["contexts"]) or "-"))
        print("== 代价 ==")
        print("  释放税(累计) %d×%.1f = %.1f µs ｜ 执行 %.1f µs ｜ **R̂ ≈ %.1f µs（端到端）**"
              % (len(gs), planner.c, len(gs) * planner.c, total_exec, predicted))
        if planner.phase_known:
            print("  端到端分解：相位跨度 %.0f µs ＋ 末组唤醒 %.1f µs ＋ 末组执行 %.1f µs"
                  % (phase_span, planner.c,
                     (gs[-1]["exec_us"] if gs else 0.0)))
        late = [r for r in sched if r["backlog_us"] > 0]
        if late:
            print("  组间积压（前组未完成 → 后组推迟）：%s"
                  % "，".join("%s 相位 %.0f→实际开始 %.0f（+%.1f）"
                              % (r["id"], r["phase_us"], r["start_us"], r["backlog_us"])
                              for r in late))
        if dp_pred is not None and abs(float(dp_pred) - predicted) > 0.01:
            print("  ⚠ DP 预测 %.1f µs 与统一时间线 %.1f µs 不一致"
                  "（两者必须由同一递推给出，请查 solve_dp）"
                  % (float(dp_pred), predicted))
        if recs:
            print("== ↻ 反馈环落位（采样序列不变，死区时间会变）==")
            for r in recs:
                print("  环 %s ｜ %s" % (" -> ".join(r["ring"]), r["verdict"]))
                for c in r["closing_edges"]:
                    d0, d1 = c["delay_us_unmerged"], c["delay_us_merged"]
                    infl = c["inflation"]
                    print("    闭合边 %-14s → %-14s 间隔 %s → %s%s"
                          % (c["from"], c["to"],
                             ("%.0f µs" % d0) if d0 is not None else "?",
                             ("%.0f µs" % d1) if d1 is not None else "?",
                             ("" if infl is None else
                              "（×%.2f）" % infl)))
        if len(gs) > 1:
            print("== 还能再合吗 ==")
            for m in planner.merge_report(groups):
                mark = "可合并" if m["allowed"] else "不可合并"
                extra = ("省 %.1f µs" % m["saving_us"]) if m["allowed"] else m["reason"]
                print("  %s：%s（%s）" % (mark, " + ".join(m["merged"]), extra))
                if m["allowed"] and m["phase_advanced_us"]:
                    print("     · 该不该合：前拉 %s"
                          % ", ".join("%s 提前 %.0f µs" % (a, d)
                                      for a, d in m["phase_advanced_us"]))
        rej = plan["rejected_segments"]
        if rej:
            print("== 被判非法的段 %d 个（前 6）==" % len(rej))
            for r in rej[:6]:
                print("  %-46s %s" % (" + ".join(r["segment"]), r["reason"]))
        if warnings:
            print("== ⚠ warning %d 条 ==" % len(warnings))
            for w in warnings:
                print("  " + w)
    return 3 if (warnings and args.fail_on_warning) else 0


if __name__ == "__main__":
    sys.exit(main())
