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
                      exec_us=0.0, drift_ppm=0.0, cons_drift_ppm=None,
                      steps=720, n_cons=2000, pick="latest"):
    """扫相对偏移 ρ ∈ [0, lcm(P_p,P_c))，问"哪些 ρ 的最坏年龄 <= 预算"。

    这是"相位协调"真正的非平凡内容：不是问"某个相位好不好"，而是问
    **在给定漂移下，安全相位窗还有多宽**。窗宽度 ≈ 0 就意味着这个速率组合
    没有可用的相位安排，只能改速率或改预算。

    PR11-E.2：生产者/消费者可分别设漂移（`cons_drift_ppm`，默认 = `drift_ppm`）。
    相对漂移速率 = (cons_drift − prod_drift)；1:1 下也照样产生相对相位行走
    （相对相位 = ρ + k·P·(dc−dp)），这正是板上实测 ≈15 ppm 的建模量。
    """
    if cons_drift_ppm is None:
        cons_drift_ppm = drift_ppm
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
        cons = Domain("c", Pc, phase_us=rho, t0_us=0.0, drift_ppm=cons_drift_ppm)
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


# ---------------------------------------------------------------- 双向重同步
#
# 为什么这个也必须进模型：重同步是**运行时**动作，但它改动的是**时间网格**，
# 所以它能不能挡住穿越、会不会自己振荡，跟 (门限, 步长, 漂移方向) 的关系
# 完全是确定性的——不该靠上板撞运气去发现。
#
# 与 C 实现的同构点（错一处结论就不可信）：
#   · 平移对**后续所有**拍生效（C: `base += _d` 改的是绝对基准）
#   · 挂起的平移在**下一拍**才应用（C: take() 在 `base += PERIOD` 之前，sub 在本拍内）
#   · 冷却按 `cons` µs 判，首次没有"上一次"就允许
#   · 环空沿用旧值（与 simulate 同一语义，否则年龄被低估）

def simulate_resync(prod, cons, n_cons, budget_us=None, lo_us=0.0, hi_us=0.0,
                    step_us=0.0, cooldown_us=0.0, pick="latest", cap=224,
                    jitter_us=0.0):
    """带双向重同步的逐拍模拟。

    lo_us: 下边界（age < lo ⇒ 后移 +step，把年龄抬大）；0 = 关闭
    hi_us: 上边界（age > hi ⇒ 前移 −step，把年龄压小）；0 = 关闭
    方向由**门限**决定，与漂移符号无关——这正是"漂移反向"时仍成立的原因。

    jitter_us: 年龄抖动幅度（±，固定种子）。**不能省**：
        理想网格下 1:1 的 age 恰好走满 [0, P_c]，永远不越过 P_c 的预算，
        于是"穿越会不会掉样本"这件事在模型里根本不存在。实测之所以每秒掉几十个，
        正是抖动（200 µs）把紧贴边界的年龄顶了出去。给定种子 ⇒ 结果可复现。

    返回 dict：ages / stale / shifts_pos / shifts_neg / min_us / max_us /
               reused / drops / backlog / over（被平移的拍数 = 非等周期数）
    """
    queue = []
    ages = []
    drops = reused = 0
    last_ts = None
    m = 0
    offset = 0.0            # 消费者绝对基准的累积平移（µs）
    pending = 0.0           # 上一拍挂起、本拍应用的平移
    last_resync = None      # 上次触发时刻（µs）；None = 还没触发过
    n_pos = n_neg = 0
    stale = 0
    _seed = 12345           # 固定种子：同一配置跑两次必须同结果（否则没法当判据）
    for k in range(n_cons):
        if pending:
            offset += pending          # C: base += _d（>0）或 base -= -_d（<0）
            pending = 0.0
        t = cons.cycle(k) + offset
        while True:
            ts = prod.sample_ready(m)
            if ts > t:
                break
            if len(queue) >= cap:
                drops += 1
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
                queue.pop(0)
            else:
                raise ValueError("pick 只认 latest/oldest/one：%r" % (pick,))
            if last_ts is not None and ts == last_ts:
                reused += 1
            last_ts = ts
        elif last_ts is None:
            continue
        else:
            reused += 1
        age = t - last_ts
        if jitter_us:
            _seed = (_seed * 1103515245 + 12345) & 0x7fffffff
            # 单侧：只让年龄**变大**。物理上"消费者早到"并不是 age 变小——
            # 早到意味着新样本还没就绪，取到的是更旧的那条，age 反而更大。
            # 所以两侧建模会把 age 压到负数（不可能的值），单侧才与板上的无符号差一致。
            age += jitter_us * (_seed / 2147483648.0)
        ages.append(age)
        if budget_us is not None and age > budget_us:
            stale += 1
        # ---- 触发（挂起到下一拍）----
        if step_us and (last_resync is None or (t - last_resync) > cooldown_us):
            if lo_us and age < lo_us:
                pending = +step_us
                last_resync = t
                n_pos += 1
            elif hi_us and age > hi_us:
                pending = -step_us
                last_resync = t
                n_neg += 1
    return {
        "ages": ages, "stale": stale, "n": len(ages),
        "shifts_pos": n_pos, "shifts_neg": n_neg,
        "min_us": min(ages) if ages else None,
        "max_us": max(ages) if ages else None,
        "reused": reused, "drops": drops, "backlog": len(queue),
        "over": n_pos + n_neg,
    }


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
    # ---- 双向重同步（PR11-D §26/§27）----
    ap.add_argument("--cons-drift-ppm", type=float, default=None,
                    help="消费域自己的漂移（不给则与 --drift-ppm 相同）。"
                         "两者不等才表示 ρ 在走：正 = age 递增，负 = age 递减")
    ap.add_argument("--resync-step", type=float, default=0.0, help="平移步长 µs（0=不开）")
    ap.add_argument("--resync-lo", type=float, default=0.0, help="下边界门限 µs")
    ap.add_argument("--resync-hi", type=float, default=0.0, help="上边界门限 µs")
    ap.add_argument("--resync-cooldown", type=float, default=0.0, help="冷却 µs")
    ap.add_argument("--simulate", action="store_true",
                    help="即使 step=0 也跑逐拍模拟（看拒收/穿越的基线）")
    ap.add_argument("--jitter-us", type=float, default=0.0,
                    help="年龄抖动（±µs，固定种子伪随机）。0 = 理想网格。"
                         "不给抖动，1:1 下 age 恰好走满 [0,P] 而永不越界，"
                         "与实测（§24 有 141 次拒收）对不上")
    a = ap.parse_args()

    prod = Domain("prod", a.prod_period, exec_us=a.exec_us, drift_ppm=a.drift_ppm)
    _cdrift = a.drift_ppm if a.cons_drift_ppm is None else a.cons_drift_ppm
    cons = Domain("cons", a.cons_period, phase_us=a.rho, drift_ppm=_cdrift)
    b = age_band(prod, cons, n_cons=a.n_cons, pick=a.pick,
                 tick_us=a.cons_period)
    print("生产域 %s" % prod)
    print("消费域 %s" % cons)
    print("  pick=%s → %s" % (a.pick, _fmt_band(b)))
    if b["ticks"]:
        print("  age（按消费域拍折算）= %.2f … %.2f 拍"
              % (b["ticks"][0], b["ticks"][1]))

    if a.simulate or a.resync_step:
        r = simulate_resync(prod, cons, a.n_cons, budget_us=a.budget_us,
                            lo_us=a.resync_lo, hi_us=a.resync_hi,
                            step_us=a.resync_step, cooldown_us=a.resync_cooldown,
                            pick=a.pick, jitter_us=a.jitter_us)
        print("  双向重同步 step=%.0f lo=%.0f hi=%.0f 冷却=%.0f → "
              "后移 %d 次 / 前移 %d 次，受平移的拍 %d"
              % (a.resync_step, a.resync_lo, a.resync_hi, a.resync_cooldown,
                 r["shifts_pos"], r["shifts_neg"], r["over"]))
        print("    age %.0f…%.0f µs（%d 拍）%s"
              % (r["min_us"] or 0, r["max_us"] or 0, r["n"],
                 ("，拒收 %d（%.3f%%）" % (r["stale"], 100.0 * r["stale"] / r["n"]))
                 if a.budget_us is not None else ""))

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


# ---------------------------------------------------------------- 编译期判据
#
# 到这里为止都是"模型"。下面把它接成**编译期能不能生成**的判据：
# 契约里声明了各域周期（多速率）就必须存在安全相位窗，否则这个部署在
# 数据年龄预算内**不可实现**——应当拒绝生成，而不是生成出来再靠运行时拒收兜底
# （§14 已实证：拒收 = 保持旧值 = 又一种延迟，救不回回路）。

_WIN_CACHE = {}


def check_edge(prod_period_us, cons_period_us, budget_us,
               exec_us=0.0, drift_ppm=0.0, cons_drift_ppm=None, jitter_us=0.0,
               steps=360, n_cons=1200, pick="latest"):
    """单条跨域边的可行性。返回 dict（含 ok / reason / 窗宽占比 / ranges / lcm）。

    判据（任一成立即判不可行）：
      · safe 为空集        → 没有任何相位可用
      · safe_width == 0    → 只有孤立点（测度零），任何抖动都会破坏
      · 预算 ≤ 抖动余量    → 预算比不确定性还小，判不了

    `jitter_us` 是**实测残差的上界**（模型没建模抖动）：判据要的是
    「预算 − 抖动」这段还能不能排出安全相位。板上实测残差最大 78 µs
    （§16.3/§17.4），声明取 100 µs。

    PR11-E.2：`cons_drift_ppm` 让消费者侧可独立设漂移（默认 = `drift_ppm`）。
    """
    if cons_drift_ppm is None:
        cons_drift_ppm = drift_ppm
    budget_eff = float(budget_us) - float(jitter_us)
    key = (round(float(prod_period_us), 3), round(float(cons_period_us), 3),
           round(float(budget_us), 3), round(float(exec_us), 3),
           round(float(drift_ppm), 3), round(float(cons_drift_ppm), 3),
           round(float(jitter_us), 3),
           int(steps), int(n_cons), pick)
    if key in _WIN_CACHE:
        return _WIN_CACHE[key]
    if budget_eff <= 0:
        res = {"ok": False, "reason": ("预算 %.0f µs 不比抖动余量 %.0f µs 大："
                                      "这份契约本身就不成立" % (budget_us, jitter_us)),
               "frac": 0.0, "lcm": 0.0, "width": 0.0, "ranges": [],
               "budget_us": float(budget_us), "drift_ppm": float(drift_ppm),
               "cons_drift_ppm": float(cons_drift_ppm),
               "jitter_us": float(jitter_us)}
        _WIN_CACHE[key] = res
        return res
    w = safe_phase_window(prod_period_us, cons_period_us, budget_eff,
                          exec_us=exec_us, drift_ppm=drift_ppm,
                          cons_drift_ppm=cons_drift_ppm,
                          steps=steps, n_cons=n_cons, pick=pick)
    lcm = w["lcm"] or 1.0
    frac = w["safe_width"] / lcm
    if not w["safe"]:
        reason = "无任何安全相位（预算 %.0f µs − 抖动 %.0f µs = %.0f µs 内排不出）" \
                 % (float(budget_us), float(jitter_us), budget_eff)
        ok = False
    elif w["safe_width"] == 0:
        reason = ("只有孤立相位点（总宽 0/%.0f µs）：测度零，"
                  "抖动/漂移一进来就破" % lcm)
        ok = False
    else:
        reason = "安全相位窗 %.0f/%.0f µs（%.0f%%）" % (w["safe_width"], lcm, 100 * frac)
        ok = True
    res = {"ok": ok, "reason": reason, "frac": frac, "lcm": lcm, "width": w["safe_width"],
           "ranges": w["safe"], "budget_us": float(budget_us),
           "budget_eff_us": budget_eff, "drift_ppm": float(drift_ppm),
           "cons_drift_ppm": float(cons_drift_ppm),
           "jitter_us": float(jitter_us)}
    _WIN_CACHE[key] = res
    return res


def _domain_periods(dag, contract):
    """契约里的各域周期；没声明的域退回任务周期（= 1:1，向后兼容）。"""
    ctr = (contract or {}).get("timing_constraints") or {}
    task = (contract or {}).get("task") or {}
    base = task.get("period_us") or ctr.get("period_us") \
        or ((dag.get("phase") or {}).get("period_us"))
    per = {}
    d = ctr.get("domain_period_us") or {}
    if isinstance(d, dict):
        for k, v in d.items():
            try:
                per[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
    return per, (float(base) if base else None)


# ---------------------------------------------------------------- 联合可行性
#
# 为什么需要它（PR11-E.1）：check_contract_coded 早期是「每条边各自安全就放行」，
# 但「每条边各自存在安全相位窗」≠「存在一个共同相对相位 ρ 让所有边同时安全」。
# 例：边 A 在 ρ∈[100,300] 安全、边 B 在 ρ∈[500,700] 安全，各自都 ok，但无共同 ρ，
# 整份方案必须拒绝。这里在公共超周期 L = lcm(各边 lcm) 上做周期性区间交集。
#
# 仅在「所有边共享同一 (Pp,Pc)（ρ 语义一致）」时由 check_contract_coded 调用；
# 混合 (Pp,Pc) 组合（含双向边）的 ρ 镜像语义不在第一版范围内，退化为逐边 OR。
# 每条边先用 xdom_edge_phase_us 基线把安全窗平移到公共坐标，再求交——
# 这是「同 (Pp,Pc) 的两条边有矛盾相位要求时必须拒绝」能端到端成立的前提。

def _interval_and(a, b):
    """两个已按起点排序、互不重叠的区间集求交。区间 (lo,hi) 半开。"""
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _periodic_intersection(ranges_per_edge, lcms):
    """每条边的 safe ranges 在其 [0, lcm_e) 上周期重复；求公共超周期 L 上的交集。

    返回 [0,L) 上的交集区间列表；空列表 = 无共同安全相位（联合不可行）。
    """
    L = 1.0
    for le in lcms:
        if le > 0:
            L = L * le / float(_gcd(int(round(L)), int(round(le))))
    if L <= 0:
        return []
    feasible = [(0.0, L)]
    for ranges, le in zip(ranges_per_edge, lcms):
        if not ranges or le <= 0:
            continue
        cover = []
        max_len = max((e - s for s, e in ranges), default=0.0)
        k = 0
        while True:
            hit = False
            for s, e in ranges:
                ss, ee = s + k * le, e + k * le
                lo, hi = max(ss, 0.0), min(ee, L)
                if hi > lo:
                    cover.append((lo, hi))
                    hit = True
            if not hit or k * le > L + max_len:
                break
            k += 1
        feasible = _interval_and(feasible, cover)
        if not feasible:
            return []
    return feasible


def _normalize_ranges(ranges, le):
    """把（带相位基线偏移、可能越界或跨边界的）区间集规整到 [0, le) 内、半开、互不重叠。

    每条跨域边的安全窗 S_e 本来定义在 [0, lcm_e) 上；加上该边的相位基线 ρ0_e 后，
    要放回公共超周期的 [0, le) 坐标就得做 (S_e − ρ0_e) 并 mod le。跨上边界的区间
    拆成两段（(s1, le) 与 (0, e1−le)），下游 _periodic_intersection 才能正确在 L 上
    铺开求交、把「两条同 (Pp,Pc) 边相位矛盾」真正判成联合不可行。
    """
    out = []
    if not le or le <= 0:
        return out
    for s, e in ranges:
        if e <= s:
            continue
        k = int(s // le)
        s1 = s - k * le
        e1 = e - k * le
        if e1 <= le:
            out.append((s1, e1))
        else:
            out.append((s1, le))
            out.append((0.0, e1 - le))
    return out


def cross_edges(dag):
    """跨域边：[(from, to, from_domain, to_domain)]，**保留每一条实际数据依赖**。

    早期版本按 (域A→域B) 去重，会把同一方向的多条跨域边（多通道 / 多消费者）
    漏掉只查第一条 —— 真实正确性 bug（PR11-E.1）。现在保留全部；重复计算由
    check_edge 的 _WIN_CACHE 按参数签名去重，不影响结果也不浪费。
    """
    dom = {n["name"]: n.get("domain") for n in (dag.get("nodes") or [])}
    out = []
    for e in (dag.get("edges") or []):
        a, b = e.get("from"), e.get("to")
        da, db = dom.get(a), dom.get(b)
        if not da or not db or da == db:
            continue
        out.append((a, b, da, db))
    return out


def check_contract_coded(dag, contract, exec_us=None):
    """契约级判据（带诊断码）：跨域部署必须存在安全相位窗。

    返回 (errs, warns)，元素是 `(code, msg)`。

    为什么要有带码版本：`plan_verify` 的错误必须是 `{code, msg}`（报告、CI、
    以及"哪些判据红过"的统计都按 code 归属）。而 `contract_validate` 只需要
    打印文本。同一份判据被两处消费，就让它**只此一份**、两处各自取需要的部分，
    不要各写一份判据——那正是 P0-1 那类"两边语义分叉"的来源。

    只在 DAG 真有跨域边时生效；单域方案一个字节都不受影响。

    xdom_edge_phase_us（可选，{"from->to": µs}）给每条跨域边独立相位基线，
    让同 (Pp,Pc) 的两条边也能有矛盾的相位要求、被联合判据真正拒绝
    （PR11-E 后续：之前 ρ 是全局变量，同 (Pp,Pc) 边恒共享窗、联合不可行永不触发）。
    """
    errs, warns = [], []
    edges = cross_edges(dag)
    if not edges:
        return errs, warns
    ctr = (contract or {}).get("timing_constraints") or {}
    edge_phase = ctr.get("xdom_edge_phase_us") or {}
    per, base = _domain_periods(dag, contract)

    budget_ticks = ctr.get("xdom_max_age_ticks")
    if budget_ticks is None:
        # fail-closed：有跨域边却没声明年龄预算，"预算"就是无穷大，无从判起。
        errs.append((
            "XDOM_BUDGET_MISSING",
            "跨域：有跨域边但没有 timing_constraints.xdom_max_age_ticks —— "
            "没有预算就无法判断该速率组合是否可部署；"
            "请显式声明（1 拍=不允许跨一拍的数据，实测该闭环 1–2 拍已发散）"))
        return errs, warns
    try:
        budget_ticks = float(budget_ticks)
    except (TypeError, ValueError):
        return ([("XDOM_BUDGET_INVALID",
                  "跨域：xdom_max_age_ticks 不是数字：%r" % (budget_ticks,))], warns)

    drift = ctr.get("xdom_drift_ppm")
    if drift is None:
        drift = 0.0
        warns.append((
            "XDOM_DRIFT_UNDECLARED",
            "跨域：没有声明 xdom_drift_ppm，按 0 处理——"
            "实测两宿主节拍相对漂移 ≈14 ppm（PR11-C §16.3），"
            "声明 0 会让安全相位窗看起来比实际宽"))
    try:
        drift = float(drift)
    except (TypeError, ValueError):
        errs.append(("XDOM_DRIFT_INVALID",
                     "跨域：xdom_drift_ppm 不是数字：%r" % (drift,)))
        drift = 0.0

    # PR11-E.2：消费者侧相对漂移（默认与生产者相同 ⇒ 退回单漂移语义）。
    cons_drift = ctr.get("xdom_cons_drift_ppm")
    if cons_drift is None:
        cons_drift = drift
    else:
        try:
            cons_drift = float(cons_drift)
        except (TypeError, ValueError):
            errs.append(("XDOM_DRIFT_INVALID",
                         "跨域：xdom_cons_drift_ppm 不是数字：%r" % (cons_drift,)))
            cons_drift = drift

    # 抖动余量：模型是确定性的，实测残差最大 78 µs ⇒ 判据要把这段扣掉。
    jitter = ctr.get("xdom_jitter_us")
    if jitter is None:
        jitter = 0.0
        warns.append((
            "XDOM_JITTER_UNDECLARED",
            "跨域：没有声明 xdom_jitter_us，按 0 处理——模型不含抖动，"
            "实测残差最大 78 µs（§16.3/§17.4）；按 0 算会让安全相位窗偏乐观"))
    try:
        jitter = float(jitter)
        if jitter < 0:
            errs.append(("XDOM_JITTER_INVALID",
                         "跨域：xdom_jitter_us 不能为负：%r" % (jitter,)))
            jitter = 0.0
    except (TypeError, ValueError):
        errs.append(("XDOM_JITTER_INVALID",
                     "跨域：xdom_jitter_us 不是数字：%r" % (jitter,)))
        jitter = 0.0

    # 逐边计算；PR11-E.3：exec_us 可为 dict（按生产者阶段名查）或标量。
    edge_results = []
    for a, b, da, db in edges:
        Pp = per.get(da, base)
        Pc = per.get(db, base)
        if not Pp or not Pc:
            errs.append(("XDOM_PERIOD_UNKNOWN",
                         "跨域：跨域边 %s→%s 的域周期未知（既没 domain_period_us，"
                         "也没有 period_us）" % (a, b)))
            continue
        budget_us = budget_ticks * Pc      # 预算按**消费者**的拍折算
        if isinstance(exec_us, dict):
            ee = float(exec_us.get(a, 0.0))   # a = 生产者节点
        else:
            ee = float(exec_us or 0.0)
        r = check_edge(Pp, Pc, budget_us, exec_us=ee, drift_ppm=drift,
                       cons_drift_ppm=cons_drift, jitter_us=jitter)
        # PR11-E 后续：每条跨域边可声明独立相位基线 ρ0（xdom_edge_phase_us，
        # 键 "from->to"，µs），把该边安全窗平移到公共坐标后再做联合求交——
        # 否则同 (Pp,Pc) 的两条边恒共享同一窗、XDOM_JOINT_INFEASIBLE 永不会触发。
        rho0 = 0.0
        if isinstance(edge_phase, dict):
            _ep = edge_phase.get("%s->%s" % (a, b))
            if _ep is not None:
                try:
                    rho0 = float(_ep)
                except (TypeError, ValueError):
                    rho0 = 0.0
        shifted = _normalize_ranges([(s - rho0, e - rho0)
                                     for s, e in r["ranges"]], r["lcm"])
        who = "%s(%s %.0f µs) → %s(%s %.0f µs)" % (a, da, Pp, b, db, Pc)
        edge_results.append((a, b, da, db, Pp, Pc, budget_us, r, who, shifted))
        if not r["ok"]:
            errs.append((
                "XDOM_INFEASIBLE",
                "跨域部署不可行：%s，预算 %.0f 拍 = %.0f µs（扣抖动 %.0f µs），"
                "漂移 %.0f/%.0f ppm ⇒ %s。\n"
                "    这不是「跑起来才知道」的事：事件时间线在生成期就能判定。\n"
                "    出路只有三条：① 放宽 xdom_max_age_ticks ② 让两域同速率 "
                "③ 改相位没用在窗为空时（窗宽 0 也是不可用）"
                % (who, budget_ticks, budget_us, jitter, drift, cons_drift,
                   r["reason"])))
        elif r["frac"] < 0.10:
            warns.append((
                "XDOM_NARROW_WINDOW",
                "跨域部署相位窗很窄：%s，预算 %.0f 拍，漂移 %.0f/%.0f ppm ⇒ %s；"
                "漂移会让可用相位慢慢走掉，建议同时做周期性重同步"
                % (who, budget_ticks, drift, cons_drift, r["reason"])))

    # PR11-E.1：联合可行性 —— 各边各自安全 ≠ 整份方案存在共同 ρ。
    if edge_results and all(r["ok"] for *_x, r, _who, _sh in edge_results):
        if len(edge_results) >= 2:
            pair0 = (edge_results[0][4], edge_results[0][5])
            same_pair = all((e[4], e[5]) == pair0 for e in edge_results)
            if same_pair:
                joint = _periodic_intersection(
                    [e[9] for e in edge_results],
                    [e[7]["lcm"] for e in edge_results])
                if not joint:
                    names = ", ".join("%s→%s" % (e[0], e[1]) for e in edge_results)
                    errs.append((
                        "XDOM_JOINT_INFEASIBLE",
                        "跨域部署联合不可行：各边单独都有安全相位窗，但找不到一个共同 "
                        "相对相位 ρ 让全部 %d 条跨域边同时安全（交集为空）。边：%s。\n"
                        "    这正是「每条边各自可行 ≠ 整份方案可行」：编译器必须对整份"
                        "部署做联合判断，而非逐边 OR。"
                        % (len(edge_results), names)))
            else:
                warns.append((
                    "XDOM_JOINT_UNHANDLED",
                    "跨域边涉及多种 (生产者周期, 消费者周期) 组合，当前联合可行性只在"
                    "同一 (Pp,Pc) 下做 ρ 交集；混合组合退化为逐边 OR（可能漏判），"
                    "建议统一速率或显式声明相位。"))

    return errs, warns


def check_contract(dag, contract, exec_us=None):
    """同上的**纯文本**版本（供 contract_validate 直接打印）。

    判据只此一份：这里只做 `(code, msg) → msg` 的降维，不另写一套逻辑。
    """
    e, w = check_contract_coded(dag, contract, exec_us=exec_us)
    return ([m for _c, m in e], [m for _c, m in w])
