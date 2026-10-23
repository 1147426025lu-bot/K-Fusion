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
3. **期限**（可选）：段内执行时间之和不得超过给定的预算。

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

退出码：0 = 有可行方案；2 = 输入不可规划（有环 / 未给平台）；3 = 无可行方案
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
    def __init__(self, dag, platform, release_cost_us, exec_us=None, budget_us=None):
        self.dag = dag
        self.plat = platform
        self.c = float(release_cost_us)
        self.exec_us = exec_us or {}
        self.budget = budget_us

        order = dag.get("topo_order") or []
        self.nodes = [n["name"] for n in dag.get("nodes") or []]
        if not order or set(order) != set(self.nodes):
            raise ValueError("拓扑序不可用（有环或缺节点），规划器不接受")
        self.order = order
        self.edges = dag.get("edges") or []
        self.node = {n["name"]: n for n in dag.get("nodes") or []}
        self.reach = build_reach(self.nodes, self.edges)
        self.reasons = {}    # (i, j) -> 拒绝原因（用于解释）

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

        # 3) 期限
        tot = sum(self.exec_us.get(m, 0.0) for m in members)
        if self.budget and tot > self.budget:
            return False, ("期限：段内执行 %.1f µs 超过预算 %.1f µs"
                           % (tot, self.budget))
        return True, "ok %.1f µs" % tot

    def segment_legal(self, i, j):
        """order[i..j]（连续段）能否并成一个周期体。"""
        return self.group_legal(self.order[i:j + 1])

    # -------- DP：最小化 G·c + ΣE（串行链上即 R̂） --------
    def solve(self):
        n = len(self.order)
        INF = float("inf")
        best = [INF] * (n + 1)
        prev = [-1] * (n + 1)
        best[0] = 0.0
        legal = {}
        for j in range(1, n + 1):
            for i in range(j):
                if best[i] == INF:
                    continue
                ok, why = self.segment_legal(i, j - 1)
                legal[(i, j - 1)] = (ok, why)
                if not ok:
                    continue
                # 每加一个组：多付一次释放代价 c；组内执行时间累加
                seg_exec = sum(self.exec_us.get(m, 0.0)
                               for m in self.order[i:j])
                val = best[i] + self.c + seg_exec
                if val < best[j]:
                    best[j] = val
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
        return groups

    # -------- 相邻两组合并的解释（论文里最有用的一段） --------
    def merge_report(self, groups):
        out = []
        for k in range(len(groups) - 1):
            i = groups[k][0]
            j = groups[k + 1][-1]
            ok, why = self.segment_legal(i, j)
            out.append({
                "merged": [self.order[x] for x in range(i, j + 1)],
                "allowed": ok,
                "reason": why,
                "saving_us": round(self.c, 3) if ok else 0.0,
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
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--verify", default=None, help="复检已有 plan.json 的合法性")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    dag = load_json(args.dag)
    if dag.get("cycles"):
        print("!! DAG 有环，规划器拒绝：先解决环（见 DAG 文档 §6）", file=sys.stderr)
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
    plan = {
        "version": 1,
        "meta": {
            "dag": args.dag,
            "platform": platform.get("name"),
            "release_cost_us": planner.c,
            "assumption": "串行链上 R̂ = G·c + ΣE；c 来自实测，不是拟合参数",
        },
        "order": planner.order,
        "groups": gs,
        "cost": {
            "groups": len(gs),
            "exec_total_us": round(total_exec, 3),
            "predicted_R_us": round(predicted, 3),
            "release_tax_us": round(len(gs) * planner.c, 3),
        },
        "merge_opportunities": planner.merge_report(groups),
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
        print("== 拓扑序 ==\n  " + " -> ".join(planner.order))
        print("== 方案：%d 组 ==" % len(gs))
        for g in gs:
            print("  %-4s %-50s exec≈%6.1f µs  ctx=%s"
                  % (g["id"], " + ".join(g["members"]), g["exec_us"],
                     ",".join(g["contexts"]) or "-"))
        print("== 代价 ==")
        print("  释放税 %d×%.1f = %.1f µs ｜ 执行 %.1f µs ｜ **R̂ ≈ %.1f µs**"
              % (len(gs), planner.c, len(gs) * planner.c, total_exec, predicted))
        if len(gs) > 1:
            print("== 还能再合吗 ==")
            for m in planner.merge_report(groups):
                mark = "可合并" if m["allowed"] else "不可合并"
                extra = ("省 %.1f µs" % m["saving_us"]) if m["allowed"] else m["reason"]
                print("  %s：%s（%s）" % (mark, " + ".join(m["merged"]), extra))
        rej = plan["rejected_segments"]
        if rej:
            print("== 被判非法的段 %d 个（前 6）==" % len(rej))
            for r in rej[:6]:
                print("  %-46s %s" % (" + ".join(r["segment"]), r["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
