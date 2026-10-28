"""domain_model.py —— 跨域语义的唯一实现（规划器与验证器共用）。

为什么要有这个文件
------------------
跨域边不是"慢一点的普通边"，它跟 z⁻¹ 闭合边是**同一类东西**：生产者在一个 cell
里跑完，消费者在另一个 cell 里，两者之间没有共享的"这一拍"——消费者只能在下一个
周期边界看到它。所以：

    跨域边 = 只能跨拍满足（不约束同拍顺序）+ 每条多消耗一整拍

这个结论不是拍脑袋估的，PR10 在板上量过：1 kHz 下跨域请求-响应 p50 = 1005.26 µs
（≈1.005 拍），而同一块内存的纯通路往返只要 1.24 µs。差三个数量级，所以
**跨域边的代价必须按"拍"计，不能按 µs 计**。

两条硬规则
----------
1. **一组只能有一个域**。融合的定义是"一次释放里连续跑几个阶段"，而两个 cell 各有
   自己的内核与调度器，物理上不存在"同一次释放"。所以跨域融合不是"不划算"，
   是**不可能**——按硬约束拒绝，不给开关。
2. **每条跨域边给端到端加一拍**。链上 K 条跨域边 ⇒ 端到端至少 K 拍（不是 K·µs）。
   环上 C 条跨域边 ⇒ 死区至少 C 拍。这两条都要能被独立复核。

用法
----
    dm = DomainModel(dag, period_us=1000)
    dm.mixed(["a", "b"])          # 这一组是否跨域
    dm.extra_periods              # 端到端至少多几拍
    dm.cycle_periods([(a,b),...]) # 这个环上几条跨域边（= 至少几拍死区）
"""
import collections


DEFAULT_DOMAIN = "default"


class DomainModel(object):
    def __init__(self, dag, period_us=None, topo=None, domain_period_us=None):
        dag = dag or {}
        nodes = dag.get("nodes") or []
        self.nodes = [n.get("name") for n in nodes]
        self.node = {n.get("name"): n for n in nodes}
        self.topo = list(topo or dag.get("topo_order")
                         or dag.get("topo_order_delayed") or [])

        # 域归属：节点级 domain 优先，其次 meta.domains 的批量声明，最后 default
        meta_dom = ((dag.get("meta") or {}).get("domains") or {})
        self.domain = {}
        for n in nodes:
            name = n.get("name")
            d = n.get("domain")
            if d is None:
                d = meta_dom.get(name)
            self.domain[name] = d or DEFAULT_DOMAIN
        self.domains = sorted(set(self.domain.values()))
        self.multi = len(self.domains) > 1

        self.period_us = period_us
        # 各域周期（多速率，PR11-D）。没声明就全退回 period_us ⇒ 与旧版一致。
        self.domain_period_us = {}
        for k, v in (domain_period_us or {}).items():
            try:
                if float(v) > 0:
                    self.domain_period_us[str(k)] = float(v)
            except (TypeError, ValueError):
                pass

        # 闭合边（z⁻¹）：算"跨域跳数"时要先把它们摘掉，否则有环的图没法做最长路
        self.closing = set()
        for r in (dag.get("recurrences") or []):
            for c in (r.get("closing_edges") or []):
                self.closing.add((c.get("from"), c.get("to")))

        edges = [e for e in (dag.get("edges") or [])
                 if e.get("kind") != "conflict"]
        self.edges = edges
        self.xdom = [(e.get("from"), e.get("to")) for e in edges
                     if self.domain.get(e.get("from"))
                     != self.domain.get(e.get("to"))]
        self.xdom_set = set(self.xdom)

        self.hops = self._hops(edges)

    # ---------------- 基本判定 ----------------
    def domain_of(self, name):
        return self.domain.get(name, DEFAULT_DOMAIN)

    def is_cross(self, a, b):
        return self.domain_of(a) != self.domain_of(b)

    def mixed(self, members):
        """这一组成员是否跨了多个域。空集不算。"""
        ds = set(self.domain_of(m) for m in members)
        return len(ds) > 1

    def domains_of(self, members):
        return sorted(set(self.domain_of(m) for m in members))

    # ---------------- 跳数：端到端至少几拍 ----------------
    def _hops(self, edges):
        """每个节点到"源"的跨域边条数（最长路，按拓扑序松弛）。

        用 DAG 的拓扑序；图里若仍有环（代数环由上层拒绝），这里最多松弛
        len(order)+1 轮，保证不死循环——宁可少报也不挂住。
        """
        order = [n for n in self.topo if n in self.domain] or list(self.nodes)

        preds = collections.defaultdict(list)
        for e in edges:
            key = (e.get("from"), e.get("to"))
            if key in self.closing:
                continue
            a, b = key
            if a in self.domain and b in self.domain:
                preds[b].append(a)

        hops = {n: 0 for n in self.nodes}
        for _round in range(len(order) + 1):
            changed = False
            for n in order:
                best = hops.get(n, 0)
                for p in preds.get(n, ()):
                    cand = hops.get(p, 0) + (1 if self.is_cross(p, n) else 0)
                    if cand > best:
                        best = cand
                if best != hops.get(n, 0):
                    hops[n] = best
                    changed = True
            if not changed:
                break
        return hops

    @property
    def max_hops(self):
        return max(self.hops.values()) if self.hops else 0

    @property
    def extra_periods(self):
        """端到端至少多几拍 = 关键路径上跨域边的条数。"""
        return self.max_hops

    @property
    def extra_latency_us(self):
        """端到端多出来的**时间**（µs）。

        一拍是**消费者所在域**的周期：跨域边 a→b 多消耗的是 b 的一拍。
        1:1 时等价于 `hops × period_us`（逐位不变）；多速率时按"最贵的那个
        消费者周期"估，是**偏保守**的上界（不知道关键路径具体走哪几条边，
        取所有跨域边里最大的消费者周期）。
        """
        if not self.max_hops:
            return 0.0
        return float(self.max_hops) * float(self.hop_period_us or 0.0)

    @property
    def hop_period_us(self):
        """跨域一拍的定价（µs）：各跨域边消费者周期的最大值。"""
        cands = []
        for a, b in self.xdom:
            db = self.domain.get(b)
            p = self.domain_period_us.get(db) if db else None
            if p:
                cands.append(float(p))
        if cands:
            return max(cands)
        return float(self.period_us) if self.period_us else 0.0

    # ---------------- 环 ----------------
    def cycle_periods(self, cycle_edges):
        """给定一个环的边集合，返回其中跨域边条数（= 死区至少几拍）。"""
        n = 0
        for e in cycle_edges or []:
            a, b = (e if isinstance(e, (list, tuple)) else (e.get("from"), e.get("to")))
            if self.is_cross(a, b):
                n += 1
        return n

    # ---------------- 报告 ----------------
    def report(self):
        return {
            "domains": self.domains,
            "multi_domain": self.multi,
            "cross_edges": ["%s->%s" % e for e in self.xdom],
            "cross_edge_count": len(self.xdom),
            "hops": dict(self.hops),
            "max_hops": self.max_hops,
            "extra_periods": self.extra_periods,
            "extra_latency_us": round(self.extra_latency_us, 3),
            "hop_period_us": round(self.hop_period_us, 3),
            "domain_period_us": dict(self.domain_period_us),
            "assumption": ("跨域边只能跨拍满足：每条多消耗一整拍。"
                           "实测依据 PR10：1 kHz 下跨域 RTT p50 = 1005.26 µs（≈1.005 拍），"
                           "而同一共享内存的纯通路往返仅 1.24 µs。"),
        }


def load(dag, period_us=None, topo=None, domain_period_us=None):
    """便捷构造。"""
    return DomainModel(dag, period_us=period_us, topo=topo,
                       domain_period_us=domain_period_us)
