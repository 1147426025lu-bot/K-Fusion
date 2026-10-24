#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fusion_plan__融合规划.py — 在单一执行域内选融合边界（ECoS PR 4 第一版）

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

代价模型（v1 刻意简单，参数全部显式）
--------------------------------------
串行链上，末段的端到端响应约等于

    R̂ = G·c + Σ E_g

其中 `G` 是组数（= 每周期释放次数）、`c` 是**每次释放的唤醒代价**、`E_g` 是组内执行时间之和。
`c` 不是拍出来的：它来自实测（root cell 的中位唤醒延迟 ≈20 µs，NuttX ≈6 µs），
用 `--release-cost-us` 传进来。**融合省下来的就是 (G−1)·c**——这部分是确定性的，
不是统计性的。

用法
----
    python fusion_plan__融合规划.py dag.json --platforms caps.json --platform linux_kernel \\
        --release-cost-us 20 -o plan.json
    python fusion_plan__融合规划.py dag.json --verify plan.json      # 复检一个已有方案

退出码：0 = 有可行方案；2 = 输入不可规划（代数环 / 未给平台）；3 = 无可行方案
"""
import argparse
import io
import json
import sys

# v1 只用这几种；出现别的就报错，不静默忽略
ORDER_KINDS = ("call", "spawn", "data", "anti")


def load_json(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def build_reach(nodes, edges, skip=None):
    """可达闭包：reach[a] = a 能传递到达的节点集合（含自己）。

    skip: 要忽略的 (from, to) 边集合。跨周期延迟边（z⁻¹）必须跳过——
    否则整张环变成强连通，不可分性会把**任何**真子段都判非法，
    只剩「全合」和「全不合」两种，融合搜索失去意义。
    """
    skip = skip or set()
    succ = {n: set() for n in nodes}
    for e in edges:
        if e["kind"] not in ORDER_KINDS:
            continue
        a, b = e["from"], e["to"]
        if a not in succ or b not in succ:
            continue
        if (a, b) in skip:
            continue
        succ[a].add(b)
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


def closing_edge_pairs(dag):
    """从 recurrences[].closing_edges 收集跨拍延迟边，供可达性计算跳过。"""
    out = set()
    for r in dag.get("recurrences") or []:
        for c in r.get("closing_edges") or []:
            a, b = c.get("from"), c.get("to")
            if a and b:
                out.add((a, b))
    return out


class Planner:
    def __init__(self, dag, platform, release_cost_us, exec_us=None, budget_us=None):
        self.dag = dag
        self.plat = platform
        self.c = float(release_cost_us)
        self.exec_us = exec_us or {}
        self.budget = budget_us

        # 有环时用"摘掉延迟边后的相位序"；strict 拓扑序为空不代表不可规划，
        # 只代表这张图里有反馈——是否合法由 algebraic_loops 决定。
        self.order = dag.get("topo_order") or dag.get("topo_order_delayed") or []
        self.nodes = [n["name"] for n in dag.get("nodes") or []]
        if not self.order or set(self.order) != set(self.nodes):
            raise ValueError("拓扑序不可用（有环且无相位序，或缺节点），规划器不接受")
        self.edges = dag.get("edges") or []
        self.node = {n["name"]: n for n in dag.get("nodes") or []}
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
        # 不可分性按「摘掉延迟边后的 DAG」算（与 topo_order_delayed 一致）
        self.delay_edges = closing_edge_pairs(dag)
        self.reach = build_reach(self.nodes, self.edges, skip=self.delay_edges)

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
        return True, "ok %.1f µs" % tot

    def segment_legal(self, i, j):
        """order[i..j]（连续段）能否并成一个周期体。"""
        return self.group_legal(self.order[i:j + 1])

    # -------- DP：最小化 G·c + ΣE（串行链上即 R̂） --------
    def _fill_legal(self):
        """填 self.legal[(i,j)] = (ok, why)：order[i..j] 能否并成一组。"""
        n = len(self.order)
        legal = {}
        for i in range(n):
            for j in range(i, n):
                legal[(i, j)] = self.segment_legal(i, j)
        self.legal = legal
        return legal

    def solve(self):
        n = len(self.order)
        INF = float("inf")
        best = [INF] * (n + 1)
        prev = [-1] * (n + 1)
        best[0] = 0.0
        legal = self._fill_legal()
        for j in range(1, n + 1):
            for i in range(j):
                if best[i] == INF:
                    continue
                ok, why = legal[(i, j - 1)]
                if not ok:
                    continue
                # 每加一个组：多付一次释放代价 c；组内执行时间累加
                seg_exec = sum(self.exec_us.get(m, 0.0)
                               for m in self.order[i:j])
                val = best[i] + self.c + seg_exec
                if val < best[j]:
                    best[j] = val
                    prev[j] = i
        if best[n] == INF:
            return None
        groups, j = [], n
        while j > 0:
            i = prev[j]
            groups.append(list(range(i, j)))
            j = i
        groups.reverse()
        return groups

    def enumerate_partitions(self):
        """枚举相位序上所有合法分段（ECoS §10 的 A|B|C / A|BC / AB|C / ABC）。

        对 n 个阶段，候选切点在相邻两阶段之间，共 2^(n-1) 种划分；
        每段再用 group_legal 过滤。小图（≤8 阶段）穷举可做正确性 oracle。
        返回按 predicted_R 升序的列表，每项是 groups（下标列表）。
        """
        n = len(self.order)
        if n == 0:
            return []
        if n > 12:
            raise SystemExit("enumerate 只服务小图（≤12 阶段）；当前 %d" % n)
        legal = self._fill_legal()
        out = []

        def rec(start, cur):
            if start == n:
                out.append([list(g) for g in cur])
                return
            for end in range(start, n):
                ok, _why = legal[(start, end)]
                if not ok:
                    continue
                cur.append(list(range(start, end + 1)))
                rec(end + 1, cur)
                cur.pop()

        rec(0, [])
        # 按 R̂ = G·c + ΣE 排序（串行链上 ΣE 对所有划分相同，实质按组数排）
        total_exec = sum(self.exec_us.get(m, 0.0) for m in self.order)

        def key(groups):
            return len(groups) * self.c + total_exec

        out.sort(key=key)
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
                    ("当前方案把环整段落在同一组（延迟边已从不可分性里摘掉，"
                     "切开环在分段上是合法的；选全合是因为 R̂ 更优）"
                     if not cut else
                     "环在 %s 切开：采样仍读上一拍，但组间释放会拉长端到端 R"
                     % ", ".join("%s→%s" % (c["from"], c["to"]) for c in cut))
                    + "；采样序列逐位不变，死区时间随融合变化"),
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
    ap.add_argument("--release-cost-us", type=float, default=20.0,
                    help="每次释放的唤醒代价（实测中位数；root cell≈20，NuttX≈6）")
    ap.add_argument("--exec-us", default=None,
                    help="JSON：{阶段名: 执行时间 µs}，缺省按 0 计（只比组数）")
    ap.add_argument("--budget-us", type=float, default=None,
                    help="段内执行时间上限（期限可行性）")
    ap.add_argument("--max-delay-inflation", type=float, default=None,
                    help="允许的反馈环死区时间放大倍数（融合把后段前拉，环闭合边因此变长）；"
                         "超过则记 warning")
    ap.add_argument("--fail-on-warning", action="store_true",
                    help="有 warning 时非零退出（CI 用）")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--verify", default=None, help="复检已有 plan.json 的合法性")
    ap.add_argument("--enumerate", action="store_true",
                    help="列出所有合法分段（小图穷举 oracle；输出 candidates[]）")
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
    planner = Planner(dag, platform, args.release_cost_us, exec_us, args.budget_us)

    # 复检模式
    if args.verify:
        plan = load_json(args.verify)
        bad = 0
        for g in plan.get("groups") or []:
            names = g.get("members") or []
            idx = [planner.order.index(x) for x in names if x in planner.order]
            if len(idx) != len(names) or not idx:
                print("❌ 组 %s 含未知成员 %s" % (g.get("id"), names), file=sys.stderr)
                bad += 1
                continue
            # 连续与否只作提示，合法性交给 group_legal 判（它会报"谁挡住了"）
            if idx != list(range(min(idx), max(idx) + 1)):
                print("   · 组 %s 在拓扑序里不连续（%s），按非连续组校验"
                      % (g.get("id"), names))
            ok, why = planner.group_legal(names)
            if not ok:
                print("❌ 组 %s 非法：%s" % (g.get("id"), why), file=sys.stderr)
                bad += 1
            else:
                print("✅ 组 %s 合法（%s）" % (g.get("id"), why))
        covered = [m for g in plan.get("groups") or [] for m in g.get("members") or []]
        if sorted(covered) != sorted(planner.nodes):
            print("❌ 覆盖不完整：%s" % (set(planner.nodes) - set(covered)), file=sys.stderr)
            bad += 1
        return 0 if bad == 0 else 3

    if args.enumerate:
        parts = planner.enumerate_partitions()
        total_exec = sum(planner.exec_us.get(m, 0.0) for m in planner.order)
        cands = []
        for gi, groups in enumerate(parts):
            gs = summarize(planner, groups)
            predicted = len(gs) * planner.c + total_exec
            # 标签形如 sample+fir|pid+clamp|plant（对应方案里的 A|BC / AB|C / ABC）
            label = "|".join("+".join(
                n.replace("stage_", "") for n in g["members"]) for g in gs)
            cands.append({
                "id": "c%d" % gi,
                "label": label,
                "groups": gs,
                "cost": {
                    "groups": len(gs),
                    "exec_total_us": round(total_exec, 3),
                    "predicted_R_us": round(predicted, 3),
                    "release_tax_us": round(len(gs) * planner.c, 3),
                },
                "recurrences": planner.recurrence_report(groups),
            })
        doc = {
            "version": 2,
            "meta": {
                "dag": args.dag,
                "platform": platform.get("name"),
                "release_cost_us": planner.c,
                "mode": "enumerate",
                "n_candidates": len(cands),
                "assumption": "串行链上 R̂ = G·c + ΣE；穷举全部合法分段",
            },
            "order": planner.order,
            "candidates": cands,
            "rejected_segments": [
                {"segment": [planner.order[x] for x in range(i, j + 1)],
                 "reason": why}
                for (i, j), (ok, why) in sorted(planner.legal.items()) if not ok
            ],
        }
        if args.out:
            with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
        if not args.quiet:
            print("== 枚举 %d 个合法分段（平台 %s，c=%.1f µs）=="
                  % (len(cands), platform.get("name"), planner.c))
            for c in cands:
                print("  %-4s  G=%d  R̂≈%7.1f µs  %s"
                      % (c["id"], c["cost"]["groups"],
                         c["cost"]["predicted_R_us"], c["label"]))
            if not cands:
                print("  （无合法分段）")
                return 3
        return 0 if cands else 3

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
            print("   (a) 这些节点**单独**就进不了平台 %s，任何划分都无解："
                  % planner.plat.get("name"), file=sys.stderr)
            for name, why in bad_single[:10]:
                print("       %-20s %s" % (name, why), file=sys.stderr)
            if len(bad_single) > 10:
                print("       …共 %d 个" % len(bad_single), file=sys.stderr)
            print("   → 这属于「输入层」问题（效果未归档 / 域不允许），"
                  "不是融合算法能解决的。", file=sys.stderr)
        else:
            print("   (b) 单节点都合法，但合并后都不可分（见 rejected_segments）",
                  file=sys.stderr)
        return 3

    gs = summarize(planner, groups)
    total_exec = sum(g["exec_us"] for g in gs)
    predicted = len(gs) * planner.c + total_exec
    recs = planner.recurrence_report(groups)
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
    plan = {
        "version": 2,
        "meta": {
            "dag": args.dag,
            "platform": platform.get("name"),
            "release_cost_us": planner.c,
            "assumption": "串行链上 R̂ = G·c + ΣE；c 来自实测，不是拟合参数",
            "phase_known": planner.phase_known,
            "period_us": planner.period,
        },
        "order": planner.order,
        "groups": gs,
        "recurrences": recs,
        "cost": {
            "groups": len(gs),
            "exec_total_us": round(total_exec, 3),
            "predicted_R_us": round(predicted, 3),
            "release_tax_us": round(len(gs) * planner.c, 3),
        },
        "merge_opportunities": planner.merge_report(groups),
        "warnings": warnings,
        "rejected_segments": [
            {"segment": [planner.order[x] for x in range(i, j + 1)],
             "reason": why}
            for (i, j), (ok, why) in sorted(planner.legal.items()) if not ok
        ],
    }
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
        print("  释放税 %d×%.1f = %.1f µs ｜ 执行 %.1f µs ｜ **R̂ ≈ %.1f µs**"
              % (len(gs), planner.c, len(gs) * planner.c, total_exec, predicted))
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
