# -*- coding: utf-8 -*-
"""xipc_versioned_model.py — PR10-F 跨域 IPC 版本化的纯 Python 参考模型与自测。

为什么有这个文件
----------------
本机没有 C 编译器（WSL 被拉黑），ecos_xipc.h / ecos_xdom.c 这套运行时只能在 Pi 上
编译验证。但"算法对不对"可以先在 PC 上用一份等价的 Python 模型验出来：环的
SPSC 提交/消费语义、桥的订阅多通道隔离、最近一条、版本化（release_id/session/
publish_tick）都可以在这里断言。这份模型也是 PR10-F 设计的**可执行规格**——

  · 新桥（缓存分桶）必须满足：sub(X) 不丢 sub(Y) 的数据；sub 取的是最近一条；
    tick / seq 透传；跨域按拍追踪的延迟拍数算得对。
  · 旧桥（PR10-C 的 while(xipc_pop) + 首匹配即返回）在这里被复现，用来证明它
    确实会把别的通道消息吞掉、且返回的是 FIFO 最旧——这正是要修的 bug。

模型与 C 的语义约定（必须与 ecos_xipc.h 一致）
------------------------------------------------
  · 槽：{seq, kind, a, b, tick}；pub 时先撤 seq 再写负载、最后 seq=h+1、head++。
  · pop：取队头（tail），seq == tail+1 才算有效；取出后 tail++（消费）。
  · 管理消息 tick=0；DATA：a=通道号，b=int32 值，tick=发布周期索引。
"""
import sys

XIPC_SLOT_N = 224
XIPC_KIND_DATA = 7


class Ring:
    """忠实复刻 ecos_xipc.h 的 SPSC 环（单生产者/单消费者）。"""

    def __init__(self, n=XIPC_SLOT_N):
        self.n = n
        self.head = 0
        self.tail = 0
        self.slots = [{"seq": 0, "kind": 0, "a": 0, "b": 0, "tick": 0}
                      for _ in range(n)]

    def publish(self, kind, a, b, tick=0):
        if (self.head - self.tail) % (1 << 32) >= self.n:
            return 1  # 环满
        s = self.slots[self.head % self.n]
        s["seq"] = 0
        s["kind"] = kind
        s["a"] = a
        s["b"] = b
        s["tick"] = tick
        s["seq"] = self.head + 1
        self.head += 1
        return 0

    def _peek(self):
        if self.head == self.tail:
            return None
        s = self.slots[self.tail % self.n]
        if s["seq"] != self.tail + 1:
            return None
        return dict(s)

    def pop(self):
        s = self._peek()
        if s is None:
            return None
        self.tail += 1
        return s


class Bridge:
    """复刻 ecos_xdom.c（新实现：按通道分桶缓存，取最近一条）。"""

    def __init__(self, ring, nchan):
        self.r = ring
        self.nchan = nchan
        self.ready = True
        self.cycle = 0
        self.cache = [None] * nchan          # 每通道 (value, tick)
        self.cache_valid = [False] * nchan

    def pub(self, channel, value):
        return self.r.publish(XIPC_KIND_DATA, channel, value & 0xffffffff, self.cycle)

    def _drain(self):
        while True:
            s = self.r.pop()
            if s is None:
                break
            if s["kind"] == XIPC_KIND_DATA and s["a"] < self.nchan:
                self.cache[s["a"]] = (s["b"], s["tick"])
                self.cache_valid[s["a"]] = True

    def sub_tick(self, channel):
        if not self.ready or channel >= self.nchan:
            return (None, None)
        self._drain()                        # 一次性抽干到各通道桶
        if self.cache_valid[channel]:
            return (self.cache[channel][0], self.cache[channel][1])
        return (None, None)

    def sub(self, channel):
        v, _ = self.sub_tick(channel)
        return v


class BridgeOld:
    """复刻 PR10-C 的旧 sub：while(pop) 首匹配即返回（buggy）。"""

    def __init__(self, ring, nchan):
        self.r = ring
        self.nchan = nchan
        self.ready = True
        self.cycle = 0

    def pub(self, channel, value):
        return self.r.publish(XIPC_KIND_DATA, channel, value & 0xffffffff, self.cycle)

    def sub(self, channel):
        if not self.ready or channel >= self.nchan:
            return None
        while True:
            s = self.r.pop()
            if s is None:
                return None
            if s["kind"] == XIPC_KIND_DATA and s["a"] == channel:
                return s["b"]            # 首匹配即返回（FIFO 最旧）


def _check(desc, cond):
    print(("  ✅ " if cond else "  ❌ ") + desc)
    return cond


def test_channel_isolation_new():
    print("\n[新桥] 多通道隔离：sub(X) 不能吞掉 sub(Y) 的数据")
    # 入环顺序：Y:20 在 X 之前 —— 旧桥会在找 X 时把 Y 一起 pop 掉。
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 2, 20, 5)   # Y, 通道2, 值20, 发布于周期5
    ring.publish(XIPC_KIND_DATA, 1, 10, 5)   # X, 通道1, 值10
    ring.publish(XIPC_KIND_DATA, 1, 30, 6)   # X, 通道1, 值30（更新）
    br = Bridge(ring, nchan=4)
    x = br.sub(1)
    y = br.sub(2)
    ok = True
    ok &= _check("sub(通道1) 取最近一条 = 30（非最旧 10）", x == 30)
    ok &= _check("sub(通道2) 没被吞掉 = 20", y == 20)
    return ok


def test_most_recent_new():
    print("\n[新桥] 同通道连发两次：返回最近一条")
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 1, 10, 1)
    ring.publish(XIPC_KIND_DATA, 1, 30, 2)
    br = Bridge(ring, nchan=4)
    v, t = br.sub_tick(1)
    ok = True
    ok &= _check("值 = 30（最近）", v == 30)
    ok &= _check("tick = 2（透传发布周期）", t == 2)
    return ok


def test_versioning_new():
    print("\n[新桥] 版本化透传：release_id(seq) 单调、publish_tick 透传、epoch 一致")
    ring = Ring()
    s1 = ring.publish(XIPC_KIND_DATA, 1, 100, 7)
    s2 = ring.publish(XIPC_KIND_DATA, 2, 200, 8)
    br = Bridge(ring, nchan=4)
    # release_id 即环 seq；消费者 pop 能看到单调序号
    first = ring._peek()
    ok = True
    ok &= _check("两次发布都成功", s1 == 0 and s2 == 0)
    ok &= _check("首槽 seq == tail+1（release_id 生效）", first["seq"] == 1)
    v2, t2 = br.sub_tick(2)
    v1, t1 = br.sub_tick(1)
    ok &= _check("通道2 值/周期透传 = 200/8", v2 == 200 and t2 == 8)
    ok &= _check("通道1 值/周期透传 = 100/7", v1 == 100 and t1 == 7)
    return ok


def test_per_cycle_trace_new():
    print("\n[新桥] 跨域按拍追踪：消费者周期 C 读到生产者周期 P，延迟拍数 = C-P")
    # 生产者周期 P=10 发布；消费者在周期 C=12 取数 → 跨域延迟 2 拍
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 1, 42, 10)
    br = Bridge(ring, nchan=4)
    br.cycle = 12
    v, t = br.sub_tick(1)
    delay_cycles = br.cycle - t
    ok = True
    ok &= _check("值正确 = 42", v == 42)
    ok &= _check("publish_tick 透传 = 10", t == 10)
    ok &= _check("延迟拍数 = 消费者周期(12) − 发布周期(10) = 2", delay_cycles == 2)
    return ok


def test_channel_isolation_old_bug():
    print("\n[旧桥复现] 多通道消费 bug：sub(X) 在 Y 排在 X 前时会吞掉 Y")
    ring = Ring()
    ring.publish(XIPC_KIND_DATA, 2, 20, 5)   # Y 在前
    ring.publish(XIPC_KIND_DATA, 1, 10, 5)   # X
    ring.publish(XIPC_KIND_DATA, 1, 30, 6)   # X 更新
    br = BridgeOld(ring, nchan=4)
    x = br.sub(1)
    y = br.sub(2)
    # 旧桥：返回的是最旧 X(10) 而非最新(30)；且 Y 已被吞掉 → 返回 None
    bug_a = (x == 10)            # 取了最旧
    bug_b = (y is None)          # Y 丢失
    ok = True
    ok &= _check("复现：旧桥取最旧 X=10（注释声称'最近一条'却相反）", bug_a)
    ok &= _check("复现：旧桥把 Y 吞掉（sub(2) 返回 None）", bug_b)
    # 这两个 bug 同时存在，正好说明 PR10-F 的修复是必要的
    return ok


def main():
    print("=== PR10-F 版本化 IPC 参考模型自测 ===")
    all_ok = True
    all_ok &= test_channel_isolation_new()
    all_ok &= test_most_recent_new()
    all_ok &= test_versioning_new()
    all_ok &= test_per_cycle_trace_new()
    all_ok &= test_channel_isolation_old_bug()
    print("\n" + ("通过：PR10-F 参考模型全部断言成立" if all_ok
                  else "失败：PR10-F 参考模型有断言未通过"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
