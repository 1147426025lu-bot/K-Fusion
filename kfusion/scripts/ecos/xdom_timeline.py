# -*- coding: utf-8 -*-
"""xdom_timeline.py —— 多执行域事件时间线（PR11-C）

**为什么要另建一个模型**

    `temporal_model.schedule()` 是**单核串行**时间线：一个周期里若干组依次释放、
    依次完成。用它去判跨域交接，只能得到"一条跨域边固定加一拍"——而那正是
    PR10-F 之后被实测推翻过的东西（§14：取数语义不改变 age；§15：多速率下
    age 由速率比与相对相位决定）。

    跨域的真实量是**两个独立事件网格的交错**：
      生产域：t_p(m) = t0_p + φ_p + m·P_p        （样本在 exec_p 之后才可见）
      消费域：t_c(k) = t0_c + φ_c + k·P_c
    数据年龄 = t_c(k) − t_p(m*)，其中 m* 是"此刻已就绪的最新/最旧样本"。
    决定它的是**两个网格的相对偏移 ρ**（每次开机都不同），不是某一个域的相位。

**这个模型能回答什么**

    · 给定 (P_p, P_c, ρ, exec, drift)，age 会落在哪些值上（多速率下是**几个值交替**）
    · 给定额算与漂移上界，哪些 ρ 是安全的（→ 相位安全窗，LET 的真正内容）
    · 消费者比生产者慢时，**抽干取最新**与**每拍只取一条**的差别（只有后者会积压）

**这个模型不回答什么**

    它不是 `plan_verify` 的替代品，也不判断控制稳定性。它是"事件时间线"这一层的
    独立参考实现，用来**预测**板上观测量（age_us），预测不中就是模型错了。

单位一律 µs（浮点），漂移以 ppm 给。
"""

import argparse
import sys


# ---------------------------------------------------------------- 域与事件网格

class Domain:
    """一个执行域的事件网格：第 k 个控制周期的**起点**。"""

    def __init__(self, name, period_us, phase_us=0.0, t0_us=0.0,
                 drift_ppm=0.0, exec_us=0.0):
        self.name = name
        self.period_us = float(period_us)
        self.phase_us = float(phase_us)
        self.t0_us = float(t0_us)
        self.drift_ppm = float(drift_ppm)
        self.exec_us = float(exec_us)

    def cycle(self, k):
        """第 k 个周期的起点（µs）。漂移按"每个周期的实际长度"累加。"""
        return (self.t0_us + self.phase_us
                + k * self.period_us * (1.0 + self.drift_ppm * 1e-6))

    def sample_ready(self, k):
        """第 k 个周期产出的样本**可被对端看见**的时刻：组跑完（exec）之后。"""
        return self.cycle(k) + self.exec_us

    def first_ready_after(self, t):
        """第一个就绪时刻 >= t 的周期号（生产侧用）。"""
        k = 0
        while self.sample_ready(k) < t:
            k += 1
        return k

    def __repr__(self):
        return ("<Domain %s P=%.0f φ=%.0f t0=%.0f drift=%+.0fppm exec=%.2f>"
                % (self.name, self.period_us, self.phase_us, self.t0_us,
                   self.drift_ppm, self.exec_us))


# ---------------------------------------------------------------- 环模拟

def simulate(prod, cons, n_cons, pick="latest", cap=224):
    """逐消费周期模拟跨域环。

    pick:
      "latest" —— 每拍把环**抽干**，用最新一条（PR10-F 部署语义，也是默认）
      "oldest" —— 每拍抽干，用最旧一条（旧桥的选择顺序）
      "one"    —— 每拍**只取一条**，其余留在环里（旧桥的消费速率；会积压）

    ⚠ 环空时桥**不更新 dst**（`ecos_xdom_sub` 只在有数据时写），所以那一拍用的是
    上一次的值——年龄继续增长。模型必须照做：漏掉这些拍会把年龄**低估**，
    而"消费者比生产者快"的稳态恰恰大量是这种拍（生产 2 ms、消费 1 ms 时占一半）。

    返回 dict：
      ages     每个消费周期真正用到的数据年龄（µs），长度 = n_cons（除了开头的空窗）
      reused   其中"环空、沿用上一次取值"的拍数（复用率；多速率下天然很高）
      drops    环满而丢掉的生产次数
      backlog  结束时环里剩余的条数
    """
    queue = []           # [(ts_us, value_index)]
    ages = []
    drops = 0
    reused = 0
    last_ts = None       # 上一次用到的样本时刻（环空时沿用它）
    m = 0                # 下一个要发布的生产周期号
    for k in range(n_cons):
        t = cons.cycle(k)
        # 生产者：把**在 t 之前就绪**的样本全部投递（跑在另一个域，与消费者并发）
        while True:
            ts = prod.sample_ready(m)
            if ts > t:
                break
            if len(queue) >= cap:
                drops += 1          # 环满：生产被丢（xipc 的 drops 语义）
            else:
                queue.append((ts, m))
            m += 1
        if queue:
            if pick == "latest":
                ts, _ = queue[-1]
                queue.clear()
            elif pick == "oldest":
                ts, _ = queue[0]
                queue.clear()
            elif pick == "one":
                ts, _ = queue[0]
                queue.pop(0)        # 只消化一条，其余留在环里
            else:
                raise ValueError("pick 只认 latest/oldest/one：%r" % (pick,))
            if last_ts is not None and ts == last_ts:
                reused += 1
            last_ts = ts
        elif last_ts is None:
            continue                # 还没有任何数据：这一拍不计
        else:
            reused += 1             # 环空：沿用上一次的值
        ages.append(t - last_ts)
    return {"ages": ages, "reused": reused, "drops": drops, "backlog": len(queue)}


def age_band(prod, cons, n_cons=4000, pick="latest", tick_us=None, ndigits=0):
    """把年龄序列压成"区间 + 去重值集合"。

    `values` 是**去重后的年龄值**（多速率下通常只有几个）——这正是多速率下
    "只打最近一次会撞上奇偶混叠"的根源：集合有 2 个值，单次采样只看到 1 个。
    """
    r = simulate(prod, cons, n_cons, pick=pick)
    ages = r["ages"]
    if not ages:
        return {"n": 0, "min_us": None, "max_us": None, "values": [],
                "reused": r["reused"], "drops": r["drops"], "backlog": r["backlog"]}
    uniq = sorted({round(a, ndigits) for a in ages})
    return {
        "n": len(ages),
        "min_us": min(ages),
        "max_us": max(ages),
        "values": uniq,
        "ticks": ([a / tick_us for a in (min(ages), max(ages))] if tick_us else None),
        "reused": r["reused"],
        "drops": r["drops"],
        "backlog": r["backlog"],
    }


# ---------------------------------------------------------------- 相位安全窗

def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def safe_phase_window(prod_period_us, cons_period_us, budget_us,
                      exec_us=0.0, drift_ppm=0.0, steps=720, n_cons=2000,
                      pick="latest"):
    """扫相对偏移 ρ ∈ [0, lcm(P_p,P_c))，问"哪些 ρ 的最坏年龄 <= 预算"。

    这是"相位协调"真正的非平凡内容：不是问"某个相位好不好"，而是问
    **在给定漂移下，安全相位窗还有多宽**。窗宽度 ≈ 0 就意味着这个速率组合
    没有可用的相位安排，只能改速率或改预算。
    """
    Pp, Pc = float(prod_period_us), float(cons_period_us)
    lcm = Pp * Pc / _gcd(int(Pp), int(Pc)) if Pp and Pc else 0.0
    if lcm <= 0:
        return {"lcm": 0.0, "safe": [], "safe_width": 0.0, "tested": 0}
    safe_flags = []
    for i in range(steps):
        rho = lcm * i / float(steps)
        prod = Domain("p", Pp, phase_us=0.0, t0_us=0.0, exec_us=exec_us,
                      drift_ppm=drift_ppm)
        # ρ 就是消费网格相对生产网格的偏移；把它放在消费域相位上最直观
        cons = Domain("c", Pc, phase_us=rho, t0_us=0.0, drift_ppm=drift_ppm)
        b = age_band(prod, cons, n_cons=n_cons, pick=pick)
        safe_flags.append((rho, (b["max_us"] is not None and b["max_us"] <= budget_us)))

    # 合并成连续区间
    ranges, start, prev = [], None, None
    for rho, ok in safe_flags + [(lcm, False)]:
        if ok and start is None:
            start = rho
        elif not ok and start is not None:
            ranges.append((start, prev))
            start = None
        prev = rho
    width = sum(b - a for a, b in ranges)
    return {"lcm": lcm, "safe": ranges, "safe_width": width,
            "tested": steps, "budget_us": float(budget_us)}


# ---------------------------------------------------------------- CLI

def _fmt_band(b):
    if not b["values"]:
        return "无样本"
    vals = b["values"]
    body = "{" + ", ".join("%.0f" % v for v in vals[:6]) + \
           (", …" if len(vals) > 6 else "") + "} µs"
    return ("n=%d  min=%.0f max=%.0f µs  取值集合 %s  复用旧值 %d  drops=%d 积压=%d"
            % (b["n"], b["min_us"], b["max_us"], body,
               b["reused"], b["drops"], b["backlog"]))


def main():
    ap = argparse.ArgumentParser(description="多执行域事件时间线（PR11-C 参考模型）")
    ap.add_argument("--prod-period", type=float, required=True)
    ap.add_argument("--cons-period", type=float, required=True)
    ap.add_argument("--rho", type=float, default=0.0,
                    help="消费网格相对生产网格的偏移（µs）")
    ap.add_argument("--exec-us", type=float, default=0.0, help="生产组执行时间（样本就绪延迟）")
    ap.add_argument("--drift-ppm", type=float, default=0.0)
    ap.add_argument("--n-cons", type=int, default=4000)
    ap.add_argument("--pick", default="latest", choices=["latest", "oldest", "one"])
    ap.add_argument("--budget-us", type=float, default=None,
                    help="给了就顺手算相位安全窗")
    a = ap.parse_args()

    prod = Domain("prod", a.prod_period, exec_us=a.exec_us, drift_ppm=a.drift_ppm)
    cons = Domain("cons", a.cons_period, phase_us=a.rho, drift_ppm=a.drift_ppm)
    b = age_band(prod, cons, n_cons=a.n_cons, pick=a.pick,
                 tick_us=a.cons_period)
    print("生产域 %s" % prod)
    print("消费域 %s" % cons)
    print("  pick=%s → %s" % (a.pick, _fmt_band(b)))
    if b["ticks"]:
        print("  age（按消费域拍折算）= %.2f … %.2f 拍"
              % (b["ticks"][0], b["ticks"][1]))

    if a.budget_us is not None:
        w = safe_phase_window(a.prod_period, a.cons_period, a.budget_us,
                              exec_us=a.exec_us, drift_ppm=a.drift_ppm,
                              pick=a.pick)
        print("  相位安全窗（预算 %.0f µs，lcm=%.0f µs，扫 %d 点）："
              % (w["budget_us"], w["lcm"], w["tested"]))
        if not w["safe"]:
            print("    ❌ 没有任何安全相位：这个速率组合在预算内不可部署")
        else:
            for lo, hi in w["safe"]:
                print("    ρ ∈ [%.0f, %.0f] µs  宽 %.0f µs" % (lo, hi, hi - lo))
            print("    总宽 %.0f / %.0f µs（%.0f%%）"
                  % (w["safe_width"], w["lcm"], 100.0 * w["safe_width"] / w["lcm"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
