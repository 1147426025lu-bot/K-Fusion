#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dag_build__任务DAG生成.py — 从效果摘要恢复任务依赖图（ECoS PR 3）

为什么需要它
------------
`EffectSummaryAnalysis` 回答的是「**一个函数**会读/写哪些区域、会不会睡、会不会碰 MMIO」。
但 ECoS 的优化对象是**任务图**：哪个阶段必须在哪个之前、哪些可以并行/融合。
这一步把「函数级摘要」抬到「任务级依赖」，并且**把每条边的语义与证据强度标出来**——
因为图上一条假边会让求解器把有依赖的两段判成可重排，那是正确性问题，不是精度问题。

边的语义（四类，**方向性不同，绝不能混**）
------------------------------------------
| kind       | 条件                    | 约束                       | 可证性 |
|------------|-------------------------|----------------------------|--------|
| `call`     | A（传递地）调用 B       | A 在 B 之前                | 可证   |
| `data`     | A 写 r，B 读 r（RAW）   | A 在 B 之前                | 可证（r 须为模块级具体区域）|
| `anti`     | A 读 r，B 写 r（WAR）   | A 在 B 之前                | 可证；已有反向 RAW 则不重复|
| `conflict` | A 写 r，B 也写 r（WAW） | **无方向**：只禁重排/融合  | 可证   |
| `candidate`| 区域只到 `arg:N`（多绑定）| 不详                     | **未证明**，不得当依赖 |

三个必须处理的陷阱（都是本项目实际踩过的）
------------------------------------------
1. **WAR 与反向 RAW 是同一个约束**
   `fir_filter` 读 `@g_sensor_raw`、`sensor_prepare` 写它 → 会同时得到
   `sensor_prepare→fir_filter [RAW]` 与 `fir_filter→sensor_prepare [WAR]`。
   但"每周期顺序执行"语义下，那个读**就是**消费这次写，不是第二条独立约束；
   两条一起画，读图的人会以为存在环。规范：**已有反向 RAW 时不出 WAR**。

2. **WAW 无方向**
   两阶段都写同一区域时不能重排、不能融合，但**谁先谁后自由**。
   若同时画 a→b 与 b→a，那是假环。规范：WAW 出**一条无向 `conflict`**，不参与拓扑。

3. **调用者/被调者之间不生成数据边**
   摘要里调用者的 reads/writes **包含**所有被调函数的效果（跨过程传播的结果）。
   于是 `chain_main` 会"写" `@g_sensor_raw`（因为 sensor_prepare 写它），而
   `sensor_prepare` 又"读"它 —— 按区域名硬连边必成环。
   规范：若 A 传递调用 B，A/B 之间只保留 `call` 边，数据重叠记 `explained_by_call`。

区域名的三级具体化
------------------
`arg:N` 是**函数局部**名字：`fir_filter` 的 `arg:0` 与 `pid_step` 的 `arg:0` 无关。
用 pass 导出的 `interface_bindings`（形参 → 所有调用点传入的区域）分三级：

| 级别 | 条件 | 处理 |
|---|---|---|
| proven | 唯一调用点、绑到唯一具体区域 | 落地成真名，可连 `data` |
| alternatives | 多个调用点绑到不同区域 | 真信息但不确定这一拍走哪条 → `candidate` |
| unresolved | 未被调用 / 绑定仍是相对名 / `heap:?` | **零信息**，不造边，节点标 `interface_unresolved` |

第三级很重要：`arg:0` 与 `arg:0` 同名**不代表任何别名关系**，造边是纯噪声；
但"不造边"也不能被读成"没有依赖"——所以必须在节点上显式标出接口未闭合。

用法
----
    python dag_build__任务DAG生成.py summary.json -o dag.json --dot dag.dot
    python dag_build__任务DAG生成.py summary.json --stages stages.json
    python dag_build__任务DAG生成.py summary.json --expect "A->B,B->C"
    python dag_build__任务DAG生成.py summary.json --expect ... --fail-on-mismatch

跨周期依赖（v2）：环分两类，判据是相位
--------------------------------------
采样控制回路**本来就是环**——执行器写出去，下一拍传感器才读回来。旧版一见环就拒，
等于宣布"不支持控制回路"。v2 用**相位**把它拆开：

| 类型 | 条件 | 含义 | 处理 |
|---|---|---|---|
| 延迟反馈环 | 环内存在边 u→v 且 phase(u) > phase(v) | 该边一拍内不可能满足 ⇒ 读上一拍 ⇒ z⁻¹ | 合法，`recurrences[]` |
| 代数环 | 环内每条边 phase(u) ≤ phase(v) | 一拍内首尾相接 ⇒ 无合法执行顺序 | 拒绝，`algebraic_loops[]` |
| 相位未知 | 函数视图 / 骨架没给 `offset_us`/`step_us` | 无法证明跨了拍边界 | 一律按代数环（fail-closed） |

相位是**人工给的设计事实**（每阶段在一拍内的释放时刻），分析不猜它。
给了相位之后，`topo_order_delayed` 给出"摘掉闭合边后按相位排"的顺序，
规划器用它；`topo_order` 在有环时仍为空（严格模式不变）。

为什么不猜：把代数环误判成反馈环，板上会算出与设计不符的值，而图上完全看不出来。
"看起来像反馈环"不是证据。
"""
import argparse
import io
import json
import sys

REGION_MAX = 12          # 每条边最多列几个证据区域（其余只计数）


def load_json(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def is_concrete(region):
    """模块级区域：跨函数可以直接比较名字。"""
    if not region:
        return False
    if region in ("heap:?", "rel:?"):
        return False
    if region.startswith(("arg:", "rel:")):
        return False
    return True


def short(region, limit=40):
    return region if len(region) <= limit else region[: limit - 3] + "..."


# ---------------------------------------------------------------- 区域具体化

class Binder:
    """把函数局部的 `arg:N` 具体化。

    三个信息源（按可靠性从高到低）：
      1. `callsites`   —— 调用点明细：谁、在哪个调用点、传了什么。**可以递归**
                          （实参本身是上层的 arg:K 时，继续往上解）
      2. `thread_entries` —— pthread_create 的 (entry, arg)：entry 的 arg:0 就是那个 arg
      3. `interface_bindings` —— 聚合视图，只在没有 callsites 时兜底（丢掉了调用者身份）

    解析结果三态（见文件头）：unique / multi / none。`none` 是**零信息**，
    调用方必须据此标"接口未闭合"，不能当成"没有别名"。
    """

    MAX_DEPTH = 4          # 递归深度：超过就放弃（宁缺勿错）

    def __init__(self, summary):
        self.bindings = summary.get("interface_bindings") or {}
        self.sites = {}        # callee -> [(caller, args)]
        self.threads = {}      # entry  -> [(caller, arg_region)]
        for rec in summary.get("callsites") or []:
            self.sites.setdefault(rec["callee"], []).append(
                (rec["caller"], rec.get("args") or []))
        for rec in summary.get("thread_entries") or []:
            self.threads.setdefault(rec["entry"], []).append(
                (rec["caller"], rec.get("arg")))

    def _incoming(self, fn, idx):
        """fn 的第 idx 个形参，在所有调用点收到的区域（含线程入口来源）。"""
        out = []
        for caller, args in self.sites.get(fn) or ():
            k = int(idx)
            if k < len(args) and args[k] != "-":
                out.append((caller, args[k]))
        # 线程入口：entry 的形参 0 来自 pthread_create 的第 4 个实参
        if idx == "0":
            for caller, reg in self.threads.get(fn) or ():
                if reg and reg != "-":
                    out.append((caller, reg))
        if not out:      # 没有明细 → 退回聚合绑定（无调用者身份，只能看唯一性）
            for reg in (self.bindings.get(fn) or {}).get(str(idx)) or []:
                out.append((None, reg))
        return out

    def resolve(self, fn, region, depth=0, seen=None):
        """返回 (status, regions)；status ∈ {unique, multi, none}。"""
        if is_concrete(region):
            return "unique", {region}
        if not region.startswith("arg:"):
            return "none", set()          # heap:? 等：零信息

        key = (fn, region)
        seen = set() if seen is None else seen
        if depth > self.MAX_DEPTH or key in seen:
            return "none", set()          # 环 / 太深：不猜
        seen = seen | {key}

        incoming = self._incoming(fn, region[4:])
        if not incoming:
            return "none", set()

        out = set()
        for caller, reg in incoming:
            st, rs = self.resolve(caller, reg, depth + 1, seen) if caller else \
                ("unique", {reg}) if is_concrete(reg) else ("none", set())
            if st == "none":
                # 有一个入口解不出来 → 整体不可证（宁可标未闭合，不要半对）
                return "none", set()
            out |= rs
        if not out:
            return "none", set()
        return ("unique" if len(out) == 1 else "multi"), out

    def concretize(self, fn, regions, stats):
        """返回 (proven, alternatives, unresolved)。"""
        proven, alts, unresolved = set(), set(), set()
        for r in regions or []:
            st, rs = self.resolve(fn, r)
            if st == "unique" and len(rs) == 1:
                proven |= rs
                if not is_concrete(r):
                    stats["concretized"] += 1
            elif st in ("unique", "multi") and rs:
                alts |= {"alt:" + x for x in rs}
                stats["multi_binding"] += 1
            else:
                unresolved.add(r)
                stats["unresolved"] += 1
        return proven, alts, unresolved


# ---------------------------------------------------------------- 调用可达性

def direct_calls(summary):
    return {fn: set(info.get("calls") or ())
            for fn, info in (summary.get("functions") or {}).items()}


def reachable(g, start):
    seen, stack = set(), list(g.get(start) or ())
    while stack:
        n = stack.pop()
        if n in seen or n == start:
            continue
        seen.add(n)
        stack.extend(g.get(n) or ())
    return seen


# ---------------------------------------------------------------- 图

class Dag:
    def __init__(self):
        self.nodes = {}
        self.edges = {}        # (from, to, kind) -> dict
        self.candidates = {}   # (a, b) sorted -> dict
        self.phase = {}        # 阶段名 -> 一拍内的释放相位（µs）
        self.period_us = None
        self.phase_known = False

    def add_node(self, name, **kw):
        n = self.nodes.setdefault(name, {"name": name, "fns": []})
        n.update({k: v for k, v in kw.items() if v is not None})
        return n

    def add_edge(self, a, b, kind, evidence):
        e = self.edges.setdefault(
            (a, b, kind), {"from": a, "to": b, "kind": kind, "evidence": set()})
        e["evidence"].update(evidence)

    def add_conflict(self, a, b, evidence):
        a, b = sorted((a, b))
        e = self.edges.setdefault(
            (a, b, "conflict"),
            {"from": a, "to": b, "kind": "conflict", "evidence": set(),
             "undirected": True})
        e["evidence"].update(evidence)

    def add_candidate(self, a, b, evidence):
        key = tuple(sorted((a, b)))
        e = self.candidates.setdefault(
            key, {"from": key[0], "to": key[1], "kind": "candidate",
                  "evidence": set()})
        e["evidence"].update(evidence)

    def finish(self, naive=False):
        """naive=True 关掉全部冗余抑制，只用于**证明这些抑制不是空转**。"""
        if not naive:
            # 冗余抑制 a：call 已保证顺序时，同向 data/anti 是多余的
            for (a, b, kind) in list(self.edges):
                callish = (a, b, "call") in self.edges or \
                          (a, b, "spawn") in self.edges
                if kind in ("data", "anti") and callish:
                    self.edges[(a, b, "call")]["evidence"] |= \
                        self.edges.pop((a, b, kind))["evidence"]
            # 冗余抑制 b（陷阱 1）：反向 RAW 已存在时，同区域的 WAR 不是独立约束
            for (a, b, kind) in list(self.edges):
                if kind == "anti" and (b, a, "data") in self.edges:
                    self.edges.pop((a, b, kind))

        edges = []
        for e in self.edges.values():
            ev = sorted(e["evidence"])
            item = {
                "from": e["from"], "to": e["to"], "kind": e["kind"],
                "evidence": [short(x) for x in ev[:REGION_MAX]],
                "evidence_count": len(ev), "proven": True,
            }
            if e.get("undirected"):
                item["undirected"] = True
                item["constraint"] = "禁止重排/融合；不含顺序"
            elif e["kind"] == "anti":
                item["constraint"] = "读先于写"
            edges.append(item)
        edges.sort(key=lambda x: (x["from"], x["to"], x["kind"]))

        cands = []
        for e in self.candidates.values():
            ev = sorted(e["evidence"])
            cands.append({
                "from": e["from"], "to": e["to"], "kind": "candidate",
                "evidence": [short(x) for x in ev[:REGION_MAX]],
                "evidence_count": len(ev), "proven": False,
                "why": "该函数被多个调用点绑到不同区域（alt:*），无法确定这一拍"
                       "走的是哪一块 → 可能有依赖，未证明",
            })
        cands.sort(key=lambda x: (x["from"], x["to"]))

        order, cycles = topo(self.nodes, edges)

        # 跨周期依赖：有环才需要判；无环时 phase/recurrence 都是空的
        recs, algs = classify_cycles(self.nodes, edges, self.phase,
                                     self.period_us, self.phase_known)
        delayed = order
        if cycles:
            drop = {(c["from"], c["to"])
                    for r in recs for c in r["closing_edges"]}
            if drop:
                # 摘掉延迟边后若能排完，说明剩下的环全是代数环 → 该报错
                delayed, _ = topo(self.nodes, edges, drop=drop,
                                  sort_key=(lambda n: self.phase.get(n, 0.0))
                                  if self.phase_known else None)
        return edges, cands, order, cycles, recs, algs, delayed


def topo(nodes, edges, drop=(), sort_key=None):
    """只按产生顺序的边排序（call/data/anti）；conflict 无方向，不参与。

    drop：要**忽略**的有向边集合 {(from, to)}——用于"摘掉环闭合边后的拓扑序"
    （见 `classify_cycles`）。sort_key 给定时按 (key, 名字) 出堆，
    这样有相位信息时输出的是**相位序**，而相位序才等于真实释放顺序。
    """
    import heapq
    names = set(nodes)
    succ = {n: set() for n in names}
    indeg = {n: 0 for n in names}
    for e in edges:
        if e["kind"] == "conflict":
            continue
        a, b = e["from"], e["to"]
        if (a, b) in drop:
            continue
        if a in names and b in names and b not in succ[a]:
            succ[a].add(b)
            indeg[b] += 1

    prio = (lambda n: (sort_key(n), n)) if sort_key else (lambda n: n)
    ready = [prio(n) if sort_key else n for n in names if indeg[n] == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        item = heapq.heappop(ready)
        n = item[1] if sort_key else item
        order.append(n)
        for m in sorted(succ[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(ready, prio(m) if sort_key else m)

    if len(order) == len(names):
        return order, []

    left = names - set(order)
    cyc, seen, stack = [], set(), []

    def dfs(v):
        stack.append(v)
        seen.add(v)
        for w in sorted(succ[v]):
            if w not in left:
                continue
            if w in stack:
                cyc.extend(stack[stack.index(w):] + [w])
                return True
            if w not in seen and dfs(w):
                return True
        stack.pop()
        return False

    for v in sorted(left):
        if v not in seen:
            stack.clear()
            if dfs(v):
                break
    return order, ([cyc] if cyc else [sorted(left)])


# ---------------------------------------------------------------- 跨周期依赖（反馈环）

def order_succ(nodes, edges, drop=()):
    """产生顺序关系（call/spawn/data/anti）；conflict 无方向，不参与。"""
    succ = {n: set() for n in nodes}
    for e in edges:
        if e["kind"] == "conflict":
            continue
        if (e["from"], e["to"]) in drop:
            continue
        if e["from"] in succ and e["to"] in succ:
            succ[e["from"]].add(e["to"])
    return succ


def tarjan(nodes, succ):
    """迭代式 Tarjan（避免递归爆栈）：返回强连通分量列表，按最小节点名排序。"""
    index = {}
    low = {}
    on_stack = {}
    stack = []
    out = []
    counter = [0]

    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(succ[root])))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            v, it = work[-1]
            descended = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, iter(sorted(succ[w]))))
                    descended = True
                    break
                if on_stack.get(w):
                    low[v] = min(low[v], index[w])
            if descended:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                out.append(set(comp))
    return sorted(out, key=lambda c: min(c))


def classify_cycles(nodes, edges, phase, period_us, phase_known):
    """把环分成两类。这是"跨周期依赖"的全部内容，判据只有一条：

    > **环里存在一条"相位反向"的边吗？**（生产者相位 > 消费者相位）

    - 有 → **延迟反馈环**（recurrence）。这条边在一拍之内无法满足，
      只能把读推迟到**下一拍**；这正好是采样控制里 z⁻¹ 的物理含义。
      环因此是**可实现**的，且延迟是**调度强制的**，不是代码里写的。
    - 没有（环内每条边的相位都不晚于其消费者）→ **代数环**。
      所有边都要求"同一拍内先写后读"，而环把它们首尾相接，于是
      不存在任何合法执行顺序。这不是"优化受限"，是**模型/设计有缺陷**，必须拒。

    为什么不猜：函数视图没有相位信息，此时所有环一律判代数环（fail-closed）。
    "看起来像反馈环"不是证据——把代数环当反馈环放行，板上会算出与设计不符的值，
    而图上什么都看不出来。
    """
    succ = order_succ(nodes, edges)
    comps = [c for c in tarjan(nodes, succ) if len(c) > 1]
    # 自环（节点写自己读自己）在阶段视图里是"同段内状态传递"，不算环
    recurrences, algebraic = [], []

    for comp in comps:
        internal = [e for e in edges
                    if e["kind"] != "conflict"
                    and e["from"] in comp and e["to"] in comp]
        back = [e for e in internal
                if phase.get(e["from"], 0.0) > phase.get(e["to"], 0.0)]
        ring = sorted(comp, key=lambda n: (phase.get(n, 0.0), n))
        base = {
            "ring": ring,
            "edges": [{"from": e["from"], "to": e["to"], "kind": e["kind"],
                       "evidence": e.get("evidence") or [],
                       "phase_from": phase.get(e["from"]),
                       "phase_to": phase.get(e["to"])}
                      for e in internal],
        }
        if not phase_known:
            base["why"] = ("无相位信息（函数视图或骨架未给 offset_us/step_us）"
                           "→ 无法证明环跨越了拍边界，按 fail-closed 判代数环")
            algebraic.append(base)
            continue
        if not back:
            base["why"] = ("环内每条边的生产者相位都不晚于消费者，"
                           "即环必须在**同一拍内**闭合 → 不存在合法执行顺序")
            algebraic.append(base)
            continue
        closing = []
        for e in back:
            pf, pt = phase[e["from"]], phase[e["to"]]
            # 未融合时，从生产者写完到消费者读到的最短间隔
            delay_us = period_us + pt - pf if period_us else None
            closing.append({
                "from": e["from"], "to": e["to"],
                "phase_from": pf, "phase_to": pt,
                "delay_periods": 1,
                "delay_us_unmerged": (round(delay_us, 3)
                                      if delay_us is not None else None),
                "evidence": e.get("evidence") or [],
            })
        base["closing_edges"] = closing
        base["delay_periods"] = 1
        base["why"] = ("相位反向边 %s 在一拍内无法满足，只能读到上一拍的值；"
                       "延迟由调度强制，不是代码里写的 z⁻¹"
                       % ", ".join("%s→%s" % (c["from"], c["to"])
                                   for c in closing))
        recurrences.append(base)
    return recurrences, algebraic


# ---------------------------------------------------------------- 边推导（两视图共用）

def plain(regions):
    """剥掉 alt: 前缀：备选区域参与比较时必须与已证名同形。"""
    return {r[4:] if r.startswith("alt:") else r for r in regions}


def derive_edges(dag, R, W, A, skip_pairs=(), naive=False):
    """R/W/A 为 name -> 已证区域 / 备选区域；skip_pairs 内的无序对不连数据边。

    naive=True 用于反证：不跳过调用对、WAR 不抑制、WAW 出双向。
    """
    names = list(R)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if not naive and ((a, b) in skip_pairs or (b, a) in skip_pairs):
                continue
            raw_ab = W[a] & R[b]
            raw_ba = W[b] & R[a]
            if raw_ab:
                dag.add_edge(a, b, "data", raw_ab)
            if raw_ba:
                dag.add_edge(b, a, "data", raw_ba)
            # WAR：仅在反向 RAW 不存在时才是独立约束（陷阱 1）
            war_ab = R[a] & W[b]
            if war_ab and (naive or not raw_ba):
                dag.add_edge(a, b, "anti", war_ab)
            war_ba = R[b] & W[a]
            if war_ba and (naive or not raw_ab):
                dag.add_edge(b, a, "anti", war_ba)
            # WAW：无方向（陷阱 2）；naive 时退化成两条有向边
            both = W[a] & W[b]
            if both:
                if naive:
                    dag.add_edge(a, b, "anti", both)
                    dag.add_edge(b, a, "anti", both)
                else:
                    dag.add_conflict(a, b, both)

            # 多绑定区域 → 候选（未证明）。
            # 注意：备选集合带 alt: 前缀，比较前必须剥掉，否则与已证名永远配不上，
            # 候选边会被**静默丢弃**——那比多一条噪声边危险得多。
            pa, pb = plain(A[a]), plain(A[b])
            proven_a, proven_b = (R[a] | W[a]), (R[b] | W[b])
            alt_hit = ((pa & (proven_b | pb)) | (pb & proven_a)) - (raw_ab | raw_ba)
            if alt_hit:
                dag.add_candidate(a, b, alt_hit)


# ---------------------------------------------------------------- 两个视图

def build_function_graph(summary, stats, naive=False):
    funcs = summary.get("functions") or {}
    binder = Binder(summary)
    g = direct_calls(summary)
    dag = Dag()

    R, W, A, U = {}, {}, {}, {}
    for fn, info in funcs.items():
        r, ra, ru = binder.concretize(fn, info.get("reads"), stats)
        w, wa, wu = binder.concretize(fn, info.get("writes"), stats)
        R[fn], W[fn], A[fn] = r, w, (ra | wa)
        U[fn] = ru | wu
        dag.add_node(fn, effects=info.get("effects") or [],
                     contexts=info.get("allowed_contexts") or [],
                     recursive=bool(info.get("recursive")) or None,
                     interface_unresolved=sorted(U[fn]) or None)

    for a in funcs:
        for b in (g.get(a) or ()):
            if b in funcs:
                dag.add_edge(a, b, "call", ["%s()" % b])

    # 线程入口：pthread_create 把 entry 交给库去起线程 → entry 不在 calls 里。
    # 语义上是 **spawn**（创建即执行），与"直接调用"不是一回事，单独一类边；
    # 但它同样产生顺序约束（创建先于该线程的第一次执行），所以参与拓扑。
    g_aug = {k: set(v) for k, v in g.items()}
    for entry, lst in binder.threads.items():
        for caller, _ in lst:
            if caller in funcs and entry in funcs:
                dag.add_edge(caller, entry, "spawn", ["pthread_create(%s)" % entry])
                g_aug.setdefault(caller, set()).add(entry)
    g = g_aug

    # 陷阱 3：调用者/被调者之间不连数据边（区域是传播来的，必成环）
    reach = {fn: reachable(g, fn) for fn in funcs}
    skip = set()
    for a in funcs:
        for b in reach[a]:
            if b in funcs:
                skip.add(tuple(sorted((a, b))))
                stats["explained_by_call"] += 1

    derive_edges(dag, R, W, A, skip_pairs=skip, naive=naive)
    return dag


def build_stage_graph(summary, skeleton, stats, naive=False):
    funcs = summary.get("functions") or {}
    binder = Binder(summary)
    g = direct_calls(summary)
    dag = Dag()

    fn2stage, stage_fns = {}, {}
    task = skeleton.get("task") or {}
    dag.period_us = task.get("period_us")
    step = task.get("step_us")
    explicit_phase = False
    for k, st in enumerate(skeleton.get("stages") or []):
        name = st["name"]
        fns = st.get("fn")
        fns = [fns] if isinstance(fns, str) else list(fns or [])
        stage_fns[name] = fns
        dag.add_node(name, fns=fns, context=st.get("context"),
                     declared_deps=list(st.get("depends_on") or []))
        for f in fns:
            fn2stage[f] = name

        # 相位：显式 offset_us/phase_us 优先；否则按 step_us 等距铺开；
        # 两者都没有 = **相位未知**（不是"都为 0"）——这决定了环能不能判成反馈环。
        ph = st.get("offset_us", st.get("phase_us"))
        if ph is not None:
            explicit_phase = True
        elif step is not None:
            ph = float(step) * k
        else:
            ph = None
        if ph is not None:
            dag.phase[name] = float(ph)
    dag.phase_known = explicit_phase or (step is not None)

    missing = [f for f in fn2stage if f not in funcs]
    if missing:
        print("!! 这些阶段函数不在摘要里（未经分析，效果未知）: %s"
              % ", ".join(missing), file=sys.stderr)

    R, W, A, U = {}, {}, {}, {}
    for name, fns in stage_fns.items():
        r_all, w_all, a_all, u_all = set(), set(), set(), set()
        for f in fns:
            info = funcs.get(f) or {}
            r, ra, ru = binder.concretize(f, info.get("reads"), stats)
            w, wa, wu = binder.concretize(f, info.get("writes"), stats)
            r_all |= r
            w_all |= w
            a_all |= (ra | wa)
            u_all |= (ru | wu)
        R[name], W[name], A[name], U[name] = r_all, w_all, a_all, u_all
        if u_all:
            dag.nodes[name]["interface_unresolved"] = sorted(u_all)

        # 阶段的效果 = 成员效果并集（"任一处有就整段有"，与契约一致）；
        # 上下文 = 成员 allowed_contexts 的**交集**（与 effect_to_contract 同一规范：
        # 有一个函数不能进中断，整段就不能进）。缺字段的按最保守的 {"thread"} 参与。
        eff, ctx = set(), None
        for f in fns:
            info = funcs.get(f) or {}
            eff |= set(info.get("effects") or [])
            allowed = set(info.get("allowed_contexts") or []) or {"thread"}
            ctx = allowed if ctx is None else (ctx & allowed)
        order = ["mmio", "sleep", "blocking_io", "state_read", "state_write",
                 "external", "unknown", "pure"]
        dag.nodes[name]["effects"] = [e for e in order if e in eff]
        dag.nodes[name]["contexts"] = [c for c in ("thread", "softirq")
                                       if ctx and c in ctx]

    for a, fns_a in stage_fns.items():
        for f in fns_a:
            for t in reachable(g, f):
                b = fn2stage.get(t)
                if b and b != a:
                    dag.add_edge(a, b, "call", ["%s() → %s()" % (f, t)])
    # spawn 也跨越阶段边界时同样成边
    for entry, lst in binder.threads.items():
        b = fn2stage.get(entry)
        for caller, _ in lst:
            a = fn2stage.get(caller)
            if a and b and a != b:
                dag.add_edge(a, b, "spawn", ["pthread_create(%s)" % entry])

    derive_edges(dag, R, W, A, naive=naive)
    return dag


# ---------------------------------------------------------------- 输出

KIND_STYLE = {
    "call": dict(color="#6b7280", style="dashed", label="call"),
    "spawn": dict(color="#0f766e", style="dashed", label="spawn(thread)"),
    "data": dict(color="#1d4ed8", style="solid", label="data RAW"),
    "anti": dict(color="#b45309", style="dotted", label="anti WAR"),
    "conflict": dict(color="#7c3aed", style="bold", label="conflict WAW"),
    "candidate": dict(color="#b91c1c", style="dashed", label="candidate?"),
}


def to_json(dag, edges, cands, order, cycles, meta, stats,
            recs=None, algs=None, delayed=None):
    nodes = []
    for n in sorted(dag.nodes.values(), key=lambda x: x["name"]):
        n = dict(n)
        if dag.phase_known and n["name"] in dag.phase:
            n["phase_us"] = dag.phase[n["name"]]
        nodes.append(n)
    return {
        "version": 2,
        "meta": meta,
        "nodes": nodes,
        "edges": edges,
        "candidates": cands,
        "phase": {
            "known": dag.phase_known,
            "unit": "us",
            "period_us": dag.period_us,
            "offsets": {k: dag.phase[k] for k in sorted(dag.phase)},
            "note": "一拍内各阶段的释放相位。跨周期判定只用它——"
                    "生产者的相位晚于消费者时，该边只能跨拍满足（z⁻¹）。",
        },
        "topo_order": order,
        "topo_order_delayed": delayed if delayed is not None else order,
        "recurrences": recs or [],
        "algebraic_loops": algs or [],
        "cycles": cycles,
        "stats": dict(
            nodes=len(dag.nodes), edges=len(edges),
            by_kind={k: sum(1 for e in edges if e["kind"] == k)
                     for k in ("call", "spawn", "data", "anti", "conflict")},
            candidates=len(cands),
            recurrences=len(recs or []), algebraic_loops=len(algs or []),
            **stats),
    }


def to_dot(dag, edges, cands, cycles, recs=None, algs=None):
    algs = algs or []
    back = {(c["from"], c["to"]) for r in (recs or []) for c in r["closing_edges"]}
    L = ["digraph task_dag {",
         "  rankdir=LR;",
         '  graph [fontname="Helvetica"];',
         '  node [shape=box, style="rounded,filled", fillcolor="#eef2ff",'
         ' color="#4338ca", fontname="Helvetica", fontsize=10];',
         '  edge [fontname="Helvetica", fontsize=8];']

    for name in sorted(dag.nodes):
        n = dag.nodes[name]
        eff = ",".join(n.get("effects") or []) or "-"
        head = name
        if n.get("fns") and n["fns"] != [name]:
            head = "%s\\n[%s]" % (name, ", ".join(n["fns"]))
        label = "%s\\n%s" % (head, eff)
        ctx = n.get("contexts") or []
        if ctx:
            label += "\\nctx: " + ",".join(ctx)
        if n.get("interface_unresolved"):
            label += "\\n⚠ iff:" + ",".join(n["interface_unresolved"])
        if dag.phase_known and name in dag.phase:
            label += "\\nt=%.0fµs" % dag.phase[name]
        L.append('  "%s" [label="%s"];' % (name, label))

    for e in edges:
        st = KIND_STYLE[e["kind"]]
        n_ev = e.get("evidence_count", len(e["evidence"]))
        tip = "\\n".join(e["evidence"][:4]) + ("\\n..." if n_ev > 4 else "")
        if (e["from"], e["to"]) in back:
            # 相位反向边 = 环的闭合边 = z⁻¹：画成红色粗线，一眼能看出环在哪断
            attrs = ('color="#dc2626", style=bold, penwidth=2, '
                     'label="z⁻¹ %s", constraint=false, tooltip="%s"'
                     % (st["label"], tip.replace('"', "'")))
            L.append('  "%s" -> "%s" [%s];' % (e["from"], e["to"], attrs))
            continue
        attrs = 'color="%s", style=%s, label="%s", tooltip="%s"' % (
            st["color"], st["style"], st["label"], tip.replace('"', "'"))
        if e.get("undirected"):
            attrs += ", dir=none"
        L.append('  "%s" -> "%s" [%s];' % (e["from"], e["to"], attrs))

    for e in cands:
        st = KIND_STYLE["candidate"]
        tip = "\\n".join(e["evidence"][:4])
        L.append('  "%s" -> "%s" [color="%s", style=%s, label="%s",'
                 ' constraint=false, tooltip="%s"];'
                 % (e["from"], e["to"], st["color"], st["style"],
                    st["label"], tip.replace('"', "'")))

    if algs:
        L.insert(3, '  graph [label="⛔ 代数环：拍内闭合，无合法执行顺序",'
                    ' fontcolor="#b91c1c"];')
    elif recs:
        L.insert(3, '  graph [label="↻ 延迟反馈环 %d 个（红色 z⁻¹ 边为环闭合处）",'
                    ' fontcolor="#b45309"];' % len(recs))
    elif cycles:
        L.insert(3, '  graph [label="⚠ 检测到环，拓扑序不可用",'
                    ' fontcolor="#b91c1c"];')
    L.append("}")
    return "\n".join(L) + "\n"


def parse_expect(s):
    out = set()
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "->" not in part:
            raise SystemExit("--expect 格式错误：%r（应形如 A->B,B->C）" % part)
        a, b = part.split("->", 1)
        out.add((a.strip(), b.strip()))
    return out


def compare(edges, expect):
    """与人工图对照：只比"产生顺序"的已证边（call + data），方向敏感。"""
    got = {(e["from"], e["to"])
           for e in edges if e["kind"] in ("call", "spawn", "data")}
    tp, fp, fn = got & expect, got - expect, expect - got

    def prf(a, b, c):
        p = a / (a + b) if a + b else 1.0
        r = a / (a + c) if a + c else 1.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        return p, r, f

    p, r, f = prf(len(tp), len(fp), len(fn))
    return {
        "expect": sorted(map(list, expect)),
        "got": sorted(map(list, got)),
        "missing": sorted(map(list, fn)),
        "extra": sorted(map(list, fp)),
        "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
        "match": not fp and not fn,
    }


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="从效果摘要生成任务 DAG")
    ap.add_argument("summary")
    ap.add_argument("-o", "--out", default=None, help="输出 dag.json")
    ap.add_argument("--dot", default=None, help="输出 Graphviz .dot")
    ap.add_argument("--stages", default=None, help="stages.json：折叠成阶段视图")
    ap.add_argument("--expect", default=None, help='人工图，如 "a->b,b->c"')
    ap.add_argument("--fail-on-mismatch", action="store_true",
                    help="与 --expect 不一致时非零退出（CI 用）")
    ap.add_argument("--fail-on-cycle", action="store_true",
                    help="检出**任意**环时非零退出（严格模式，CI 用）")
    ap.add_argument("--fail-on-algebraic", action="store_true",
                    help="仅当存在**拍内闭合**的非法环时非零退出（rc=5）；"
                         "延迟反馈环放行")
    ap.add_argument("--naive", action="store_true",
                    help="关掉全部冗余抑制（反证用，产物不可用于决策）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    summary = load_json(args.summary)
    stats = dict(concretized=0, multi_binding=0, unresolved=0,
                 explained_by_call=0)

    if args.stages:
        skeleton = load_json(args.stages)
        dag = build_stage_graph(summary, skeleton, stats, naive=args.naive)
        task, mode = skeleton.get("task") or {}, "stages"
    else:
        dag = build_function_graph(summary, stats, naive=args.naive)
        task, mode = {}, "functions"

    edges, cands, order, cycles, recs, algs, delayed = dag.finish(naive=args.naive)
    meta = {
        "mode": mode, "summary": args.summary, "task": task,
        "note": "proven=true 的边可作为约束；candidates 未经证明，不得当作依赖；"
                "conflict 无方向（只禁止重排）；recurrences 是相位反向边给出的"
                "真实反馈环（延迟 1 拍），algebraic_loops 是拍内闭合的非法环",
    }
    if args.naive:
        meta["naive"] = True
        meta["note"] += "；⚠ 本次用 --naive，抑制全关，产物不可用于决策"
    doc = to_json(dag, edges, cands, order, cycles, meta, stats,
                  recs, algs, delayed)

    if args.expect:
        doc["compare"] = compare(edges, parse_expect(args.expect))

    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
    if args.dot:
        with io.open(args.dot, "w", encoding="utf-8", newline="\n") as f:
            f.write(to_dot(dag, edges, cands, cycles, recs, algs))

    if not args.quiet:
        print("== 节点 %d（%s 视图） ==" % (len(dag.nodes), mode))
        for n in doc["nodes"]:
            print("  %-22s %s"
                  % (n["name"], ",".join(n.get("effects") or []) or "-"))
        print("== 边 ==")
        for e in edges:
            arrow = "<->" if e.get("undirected") else "->"
            tail = (" …(%d)" % e["evidence_count"]
                    if e["evidence_count"] > 4 else "")
            print("  %-20s %s %-20s [%-8s] %s%s"
                  % (e["from"], arrow, e["to"], e["kind"],
                     ", ".join(e["evidence"][:4]), tail))
        if cands:
            print("== 候选边（未证明，禁止当作依赖）==")
            for e in cands:
                print("  %-20s -> %-20s %s"
                      % (e["from"], e["to"], ", ".join(e["evidence"][:3])))
        print("== 拓扑序 ==")
        if order:
            print("  " + " -> ".join(order))
        elif doc["topo_order_delayed"]:
            print("  （严格拓扑序不可用：有环）")
            print("  摘掉延迟边后的相位序：" + " -> ".join(doc["topo_order_delayed"]))
        else:
            print("  （有环，不可用）")
        if recs:
            print("== ↻ 延迟反馈环 %d 个（合法：延迟由调度强制为 1 拍）==" % len(recs))
            for r in recs:
                print("  环：" + " -> ".join(r["ring"]))
                for c in r["closing_edges"]:
                    d = c.get("delay_us_unmerged")
                    print("    闭合边 %s→%s：相位 %.0f→%.0f µs，"
                          "未融合时间隔 %s" % (
                              c["from"], c["to"], c["phase_from"],
                              c["phase_to"],
                              ("%.0f µs" % d) if d is not None else "?（未给 period_us）"))
                    print("      证据：" + (", ".join(c["evidence"][:4]) or "-"))
        if algs:
            print("== ⛔ 代数环 %d 个（拍内闭合，无合法执行顺序）==" % len(algs))
            for r in algs:
                print("  环：" + " -> ".join(r["ring"]))
                print("    " + r["why"])
        if cycles:
            print("== 闭环路径（原始走向，供人工核对）==")
            for c in cycles:
                print("  " + " -> ".join(c))
        print("== 统计 ==")
        print("  边 %d（call %d / spawn %d / data %d / anti %d / conflict %d）"
              "· 候选 %d"
              % (len(edges), doc["stats"]["by_kind"]["call"],
                 doc["stats"]["by_kind"]["spawn"],
                 doc["stats"]["by_kind"]["data"],
                 doc["stats"]["by_kind"]["anti"],
                 doc["stats"]["by_kind"]["conflict"], len(cands)))
        print("  区域具体化：唯一绑定 %d · 多绑定 %d · 完全未解析 %d · 调用已解释 %d"
              % (stats["concretized"], stats["multi_binding"],
                 stats["unresolved"], stats["explained_by_call"]))
        flagged = [n for n in doc["nodes"] if n.get("interface_unresolved")]
        if flagged:
            print("  ⚠ 接口未闭合 %d 个节点"
                  "（其跨阶段依赖**不可判定**，不等于没有依赖）：" % len(flagged))
            for n in flagged:
                print("      %-20s %s"
                      % (n["name"], ", ".join(n["interface_unresolved"])))

    rc = 0
    if "compare" in doc:
        cmp = doc["compare"]
        print("== 与人工图对照 ==")
        hit = len(cmp["expect"]) - len(cmp["missing"])
        print("  人工 %d 条 / 编译器 %d 条 / 命中 %d"
              % (len(cmp["expect"]), len(cmp["got"]), hit))
        print("  precision %.3f  recall %.3f  f1 %.3f"
              % (cmp["precision"], cmp["recall"], cmp["f1"]))
        for a, b in cmp["missing"]:
            print("  ❌ 漏: %s -> %s" % (a, b))
        for a, b in cmp["extra"]:
            print("  ⚠ 多: %s -> %s" % (a, b))
        if cmp["match"]:
            print("  ✅ 与人工拓扑完全一致")
        elif args.fail_on_mismatch:
            rc = 3

    if cycles and args.fail_on_cycle:
        rc = rc or 4
    if algs and args.fail_on_algebraic:
        rc = rc or 5
    return rc


if __name__ == "__main__":
    sys.exit(main())
