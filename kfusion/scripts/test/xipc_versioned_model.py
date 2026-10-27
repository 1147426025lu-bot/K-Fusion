# -*- coding: utf-8 -*-
"""xipc_versioned_model.py — 跨域 IPC 运行时的纯 Python 参考模型与自测。

为什么有这个文件
----------------
本机没有 C 编译器（WSL 被拉黑），ecos_xipc.h / ecos_xdom.c 这套运行时只能在 Pi 上
编译验证。但"算法对不对"可以先在 PC 上用一份等价的 Python 模型验出来：环的
SPSC 提交/消费语义、桥的订阅多通道隔离、最近一条、版本化（seq / session / ts_us）
都可以在这里断言。这份模型是设计的**可执行规格**——

  · 新桥（缓存分桶）必须满足：sub(X) 不丢 sub(Y) 的数据；sub 取的是最近一条；
    时间戳透传；跨域 age 用 µs 算、与两侧速率无关。
  · 旧桥（PR10-C 的 while(xipc_pop) + 首匹配即返回）在这里被复现，用来证明它
    确实会把别的通道消息吞掉、且返回的是 FIFO 最旧——这正是要修的 bug。

⚠ **协议版本 v3（PR11-B）**：槽里不再存"周期索引"，改存**发布时刻 ts_us**。
    v2 的 `tick = counter / period_ticks` 在多速率下两侧量纲不同，相减得到的
    "拍数"会随周期比缩放，根本不是时间。v3 用 µs 时间戳，`age_us` 与速率无关，
    要"几拍"再除以**本域**周期。

模型与 C 的语义约定（必须与 ecos_xdom.c 逐条一致）
--------------------------------------------------
  · 槽：{seq, kind, a, b, ts_us}；pub 时先撤 seq 再写负载、最后 seq=h+1、head++。
  · pop：取队头（tail），seq == tail+1 才算有效；取出后 tail++（消费）。
  · 管理消息 ts_us=0；DATA：a=通道号，b=int32 值，ts_us=发布时刻（µs）。
  · PICK 三臂：0=抽干取最新 / 1=抽干取最旧 / 2=每拍只 pop 一条（其余留环里）。
  · LAG：每通道移位寄存器，取 LAG 拍前的值；LAG=0 等价取最新。
  · age_us = now_us − ts_us（32 位无符号回绕）；age(拍) = **向上取整** age_us / P。
  · 过期拒收：`age_us > MAX_AGE × P` 才拒（**µs 口径**，不是整数拍）。
"""
import sys

XIPC_SLOT_N = 224
XIPC_KIND_DATA = 7
U32 = 0xFFFFFFFF

PICK_LATEST, PICK_OLDEST, PICK_ONE = 0, 1, 2


class Ring:
    """忠实复刻 ecos_xipc.h 的 SPSC 环（单生产者/单消费者）。"""

    def __init__(self, n=XIPC_SLOT_N):
        self.n = n
        self.head = 0
        self.tail = 0
        self.slots = [{"seq": 0, "kind": 0, "a": 0, "b": 0, "ts_us": 0}
                      for _ in range(n)]

    def publish(self, kind, a, b, ts_us=0):
        if (self.head - self.tail) % (1 << 32) >= self.n:
            return 1  # 环满
        s = self.slots[self.head % self.n]
        s["seq"] = 0
        s["kind"] = kind
        s["a"] = a
        s["b"] = b
        s["ts_us"] = ts_us
        s["seq"] = self.head + 1
        self.head += 1
        return 0

    def peek(self):
        if self.head == self.tail:
            return None
        s = self.slots[self.tail % self.n]
        if s["seq"] != self.tail + 1:
            return None
        return dict(s)

    def pop(self):
        s = self.peek()
        if s is None:
            return None
        self.tail += 1
        return s

    def depth(self):
        return self.head - self.tail


class Bridge:
    """复刻 ecos_xdom.c：抽干/单条取数、LAG 移位寄存器、µs 年龄、过期拒收。"""

    def __init__(self, ring, nchan, period_us=1000, pick=PICK_LATEST,
                 lag=0, max_age=0):
        self.r = ring
        self.nchan = nchan
        self.p = int(period_us)          # 本域周期（只用于"折成几拍"）
        self.pick = pick
        self.lag = int(lag)
        self.hist = self.lag + 1
        self.max_age = int(max_age)      # 拍；0 = 不拒收
        self.ready = True
        self.now = 0                     # 本域当前时刻（µs）
        self.cache = [None] * nchan      # 每通道 (value, ts_us)
        self.valid = [False] * nchan
        self.hval = [[0] * self.hist for _ in range(nchan)]
        self.hts = [[0] * self.hist for _ in range(nchan)]
        self.hpos = [0] * nchan
        self.hn = [0] * nchan
        self.ev = [self._new_ev() for _ in range(nchan)]

    @staticmethod
    def _new_ev():
        return {"prod": 0, "cons": 0, "age_us": 0, "age": 0,
                "n": 0, "stale": 0, "max_age": 0,
                "min_us": 0, "max_us": 0}

    # ---- 生产 ----
    def pub(self, channel, value):
        """发布时刻用本域 now（µs）——注意不是"第几拍"。"""
        return self.r.publish(XIPC_KIND_DATA, channel, value & U32, self.now & U32)

    def reset_window(self, channel):
        """复刻 C 里"打完即重置 min/max"：窗口统计只描述一个上报窗口。"""
        e = self.ev[channel]
        e["min_us"] = e["age_us"]
        e["max_us"] = e["age_us"]

    # ---- 消费 ----
    def sub_tick(self, channel):
        """返回 (值, 发布时刻 µs)；无数据/被拒时返回 (None, None)。"""
        if not self.ready or channel >= self.nchan:
            return (None, None)

        if self.pick == PICK_ONE:
            # 只消化一条；不是本通道就耗掉（旧桥行为）
            s = self.r.pop()
            if s is None:
                return (None, None)
            if s["kind"] != XIPC_KIND_DATA or s["a"] != channel:
                return (None, None)
            self.cache[channel] = (s["b"], s["ts_us"])
            self.valid[channel] = True
        else:
            seen = set()
            while True:
                s = self.r.pop()
                if s is None:
                    break
                if s["kind"] == XIPC_KIND_DATA and s["a"] < self.nchan:
                    ch = s["a"]
                    if self.pick == PICK_OLDEST:
                        if ch in seen:
                            continue        # 本批已有更旧的一条
                        seen.add(ch)
                    self.cache[ch] = (s["b"], s["ts_us"])
                    self.valid[ch] = True
            if self.pick == PICK_OLDEST and channel not in seen:
                return (None, None)          # 旧桥：本批没本通道就不更新

        if not self.valid[channel]:
            return (None, None)

        e = self.ev[channel]
        # LAG 移位寄存器
        pos = self.hpos[channel]
        self.hval[channel][pos] = self.cache[channel][0]
        self.hts[channel][pos] = self.cache[channel][1]
        self.hpos[channel] = (pos + 1) % self.hist
        if self.hn[channel] < self.hist:
            self.hn[channel] += 1
        if self.hn[channel] < self.hist:
            idx = (self.hpos[channel] + self.hist - 1) % self.hist
        else:
            idx = self.hpos[channel]

        v = self.hval[channel][idx]
        ts = self.hts[channel][idx]
        cons = self.now & U32
        age = (cons - ts) & U32

        e["prod"] = ts
        e["cons"] = cons
        e["age_us"] = age
        e["age"] = ((age + self.p - 1) // self.p) if self.p else 0   # 向上取整
        e["n"] += 1
        if e["n"] == 1 or age < e["min_us"]:
            e["min_us"] = age
        if age > e["max_us"]:
            e["max_us"] = age
        if e["age"] > e["max_age"]:
            e["max_age"] = e["age"]

        if self.max_age and age > self.max_age * self.p:
            e["stale"] += 1
            return (None, None)              # 拒收：dst 不更新
        return (v, ts)

    def sub(self, channel):
        return self.sub_tick(channel)[0]


class BridgeOld:
    """复刻 PR10-C 的旧 sub：while(pop) 首匹配即返回（buggy）。"""

    def __init__(self, ring, nchan):
        self.r = ring
        self.nchan = nchan
        self.ready = True
        self.now = 0

    def pub(self, channel, value):
        return self.r.publish(XIPC_KIND_DATA, channel, value & U32, self.now & U32)

    def sub(self, channel):
        if not self.ready or channel >= self.nchan:
            return None
        while True:
            s = self.r.pop()
            if s is None:
                return None
            if s["kind"] == XIPC_KIND_DATA and s["a"] == channel:
                return s["b"]            # 首匹配即返回（FIFO 最旧）


def _check(desc, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + desc
          + (("  " + extra) if extra else ""))
    return bool(cond)


# ---------------------------------------------------------------- 断言组

def test_channel_isolation_new():
    print("\n[新桥] 多通道隔离：sub(X) 不能吞掉 sub(Y) 的数据")
    # 入环顺序：Y 在 X 之前 —— 旧桥会在找 X 时把 Y 一起 pop 掉。
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 2, 20, 5000)   # Y, 通道2, 值20
    ring.publish(XIPC_KIND_DATA, 1, 10, 5000)   # X
    ring.publish(XIPC_KIND_DATA, 1, 30, 6000)   # X 更新
    br = Bridge(ring, nchan=4)
    x = br.sub(1)
    y = br.sub(2)
    ok = True
    ok &= _check("sub(通道1) 取最近一条 = 30（非最旧 10）", x == 30)
    ok &= _check("sub(通道2) 没被吞掉 = 20", y == 20)
    return ok


def test_timestamp_passthrough():
    print("\n[新桥 v3] 时间戳透传：sub_tick 返回的是**发布时刻 µs**，不是周期索引")
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 1, 100, 7000)
    ring.publish(XIPC_KIND_DATA, 2, 200, 8000)
    br = Bridge(ring, nchan=4)
    v2, t2 = br.sub_tick(2)
    v1, t1 = br.sub_tick(1)
    ok = True
    ok &= _check("首槽 seq == tail+1（消息序号生效）", ring.slots[0]["seq"] == 1)
    ok &= _check("通道2 值/时刻 = 200/8000", v2 == 200 and t2 == 8000)
    ok &= _check("通道1 值/时刻 = 100/7000", v1 == 100 and t1 == 7000)
    return ok


def test_age_is_microseconds():
    print("\n[新桥 v3] 年龄用 µs：与两侧周期无关（2 ms 生产 / 1 ms 消费）")
    # 生产者在 2000 µs 处发布；消费者在 3 个不同时刻读同一条数据，
    # age_us 必须依次是 0 / 500 / 1000 —— 若还按"周期索引"存，这里算不出来。
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 1, 42, 2000)
    br = Bridge(ring, nchan=4, period_us=1000)
    br.now = 2000
    v, ts = br.sub_tick(1)
    a1 = br.ev[1]["age_us"]
    br.now = 2500
    br.sub_tick(1)
    a2 = br.ev[1]["age_us"]
    br.now = 3000
    br.sub_tick(1)
    a3 = br.ev[1]["age_us"]
    ok = True
    ok &= _check("值正确 = 42", v == 42)
    ok &= _check("发布时刻透传 = 2000 µs", ts == 2000)
    ok &= _check("age_us 依次 0 / 500 / 1000", (a1, a2, a3) == (0, 500, 1000),
                 str((a1, a2, a3)))
    ok &= _check("age(拍) 向上取整：500 µs → 1 拍、1000 µs → 1 拍",
                 (a2 + 999) // 1000 == 1 and (a3 + 999) // 1000 == 1)
    return ok


def test_ceil_ticks_and_budget_units():
    print("\n[新桥 v3] 拍数向上取整 + 预算按 µs（与编译期同口径）")
    # 坑：以前写 age_us / P（向下取整），1797 µs 被算成"1 拍"、预算 1 拍时放行，
    # 而编译期的安全相位窗判据是 age_us <= 预算×P（严格 µs）⇒ 两边结论相反。
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 1, 7, 0)
    br = Bridge(ring, nchan=4, period_us=1000, max_age=1)
    br.now = 1797
    v, _ = br.sub_tick(1)
    e = br.ev[1]
    ok = True
    ok &= _check("1797 µs 记为 2 拍（不是 1 拍）", e["age"] == 2, str(e["age"]))
    ok &= _check("预算 1 拍 = 1000 µs ⇒ 1797 > 1000 拒收（dst 不更新）",
                 v is None and e["stale"] == 1)
    ok &= _check("拒收也保留证据（age_us 仍记 1797）", e["age_us"] == 1797)

    # 预算 2 拍 ⇒ 1797 <= 2000，放行；这正是板上"合法部署"那一跑的情形。
    ring2 = Ring()
    ring2.publish(XIPC_KIND_DATA, 1, 7, 0)
    br2 = Bridge(ring2, nchan=4, period_us=1000, max_age=2)
    br2.now = 1797
    v2, _ = br2.sub_tick(2 - 1)
    ok &= _check("预算 2 拍 ⇒ 1797 <= 2000 放行、stale=0",
                 v2 == 7 and br2.ev[1]["stale"] == 0)
    return ok


def test_lag_injection():
    print("\n[新桥] LAG：取 N 拍前的值（受控 A/B 的注入手段）")
    ring = Ring()
    br = Bridge(ring, nchan=4, period_us=1000, lag=2)
    # 逐拍发布 1..5，时刻 1000..5000
    v = None
    for k in range(1, 6):
        br.now = k * 1000
        br.pub(1, k * 10)
        v, _ts = br.sub_tick(1)
    ok = True
    ok &= _check("LAG=2 ⇒ 消费到的是 2 拍前的值 30（最新是 50）",
                 br.cache[1][0] == 50 and v == 30 and br.ev[1]["age_us"] == 2000,
                 "v=%s 最新=%s age_us=%d" % (v, br.cache[1][0], br.ev[1]["age_us"]))
    br0 = Bridge(Ring(), nchan=4, period_us=1000, lag=0)
    v0 = None
    for k in range(1, 6):
        br0.now = k * 1000
        br0.pub(1, k * 10)
        v0, _ts = br0.sub_tick(1)
    ok &= _check("LAG=0 ⇒ 取最新 50、age_us = 0（同一拍）",
                 v0 == 50 and br0.ev[1]["age_us"] == 0,
                 "v=%s age_us=%d" % (v0, br0.ev[1]["age_us"]))
    return ok


def test_pick_one_backlog():
    print("\n[新桥] PICK=2（每拍只取一条）：生产者更快 ⇒ 环积压")
    ring = Ring(n=8)
    br = Bridge(ring, nchan=4, period_us=1000, pick=PICK_ONE)
    for k in range(1, 6):
        br.pub(1, k)          # 同拍入 5 条（生产者更快/一拍多条）
    br.now = 6000
    br.sub_tick(1)
    ok = True
    ok &= _check("每拍只消化一条 ⇒ 环里还剩 4 条（=积压）", ring.depth() == 4,
                 "depth=%d" % ring.depth())
    # 抽干臂同样的输入会一次吃干净
    ring2 = Ring(n=8)
    br2 = Bridge(ring2, nchan=4, period_us=1000, pick=PICK_LATEST)
    for k in range(1, 6):
        br2.pub(1, k)
    br2.now = 6000
    br2.sub_tick(1)
    ok &= _check("抽干臂吃干净 ⇒ depth=0、取到最新 5",
                 ring2.depth() == 0 and br2.cache[1][0] == 5,
                 "depth=%d v=%s" % (ring2.depth(), br2.cache[1][0]))
    return ok


def test_channel_isolation_old_bug():
    print("\n[旧桥复现] 多通道消费 bug：sub(X) 在 Y 排在 X 前时会吞掉 Y")
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 2, 20, 5000)   # Y 在前
    ring.publish(XIPC_KIND_DATA, 1, 10, 5000)   # X
    ring.publish(XIPC_KIND_DATA, 1, 30, 6000)   # X 更新
    br = BridgeOld(ring, nchan=4)
    x = br.sub(1)
    y = br.sub(2)
    ok = True
    ok &= _check("复现：旧桥取最旧 X=10（注释声称'最近一条'却相反）", x == 10)
    ok &= _check("复现：旧桥把 Y 吞掉（sub(2) 返回 None）", y is None)
    return ok


def main():
    print("=== 跨域 IPC 参考模型自测（协议 v3：µs 时间戳）===")
    all_ok = True
    all_ok &= test_channel_isolation_new()
    all_ok &= test_timestamp_passthrough()
    all_ok &= test_age_is_microseconds()
    all_ok &= test_ceil_ticks_and_budget_units()
    all_ok &= test_lag_injection()
    all_ok &= test_pick_one_backlog()
    all_ok &= test_channel_isolation_old_bug()
    print("\n" + ("通过：参考模型全部断言成立" if all_ok
                  else "失败：参考模型有断言未通过"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
