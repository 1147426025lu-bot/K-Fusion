# -*- coding: utf-8 -*-
"""闭环五阶段参考模型（板上正确性判据）。

为什么需要它
------------
"模块加载成功、periods 在涨"只证明调度在跑，**不证明算得对**。这个脚本用
与 `five_stage_loop__闭环五阶段示例.c` 逐位相同的整数语义复算 g_out / g_plant，
于是板上读数可以拿来精确比对，而不是"看着合理"。

用法：

    # 只算：第 1007 拍时的期望值
    python3 ref_model_five_stage.py 1007

    # 比对板上快照（冻结后读到的一组值）
    python3 ref_model_five_stage.py --compare 1007 630 717

退出码：0 一致｜1 不一致｜2 参数问题

注意：必须是**同拍**快照。sysfs 分几次 cat 之间会跨周期，所以要先
`echo 1 > /sys/module/<mod>/parameters/frozen`，读完再解冻。
"""

import sys

M32 = 0xFFFFFFFF

# 与 C 侧同一组常量
SETPOINT = 1000
SEED0 = 12345


def _s32(x):
    """模拟 int32_t 环绕（C 里 err << 1 之类会溢出，gcc 实际按 2 的补码回绕）。"""
    x &= M32
    return x - (1 << 32) if x >= (1 << 31) else x


def simulate(n, setpoint=SETPOINT, seed0=SEED0):
    """跑 n 拍，返回 (g_out, g_plant, g_raw, g_fir_out, g_integ)。"""
    plant = 0
    hist = [0] * 8
    integ = 0
    out = 0
    raw = 0
    fir = 0
    seed = seed0

    for _ in range(n):
        # stage_sample
        seed = (seed * 1103515245 + 12345) & M32
        dist = ((seed >> 16) & 0x3FF) - 512
        raw = _s32(plant + dist)
        # stage_fir（8 点滑动平均 + 窗口右移）
        acc = sum(hist)
        for i in range(7, 0, -1):
            hist[i] = hist[i - 1]
        hist[0] = raw
        fir = _s32(acc) >> 3
        # stage_pid（Kp=2, Ki=1/8，积分限幅 ±100000）
        err = _s32(setpoint - fir)
        integ = _s32(integ + err)
        if integ > 100000:
            integ = 100000
        elif integ < -100000:
            integ = -100000
        act = _s32((err << 1) + (integ >> 3))
        # stage_clamp（±60000）
        out = 60000 if act > 60000 else (-60000 if act < -60000 else act)
        # stage_plant（一阶对象 y ← (3y + u)/4）
        plant = _s32((plant * 3 + out) >> 2)

    return out, plant, raw, fir, integ


def main(argv):
    if len(argv) >= 5 and argv[1] == "--compare":
        n = int(argv[2])
        got = (int(argv[3]), int(argv[4]))
        out, plant = simulate(n)[:2]
        ok = (out, plant) == got
        print("n=%d 参考 g_out=%d g_plant=%d ｜ 板上 %d/%d → %s"
              % (n, out, plant, got[0], got[1], "一致 ✓" if ok else "不一致 ✗"))
        if not ok:
            # 邻域也打出来，便于判断是不是"差一拍"
            for k in range(max(0, n - 2), n + 3):
                o, p = simulate(k)[:2]
                mark = "  <- 与板上相同" if (o, p) == got else ""
                print("   n=%-6d %-7d %-7d%s" % (k, o, p, mark))
        return 0 if ok else 1

    if len(argv) != 2:
        print(__doc__)
        return 2
    n = int(argv[1])
    out, plant, raw, fir, integ = simulate(n)
    print("n=%d  g_out=%d  g_plant=%d  (g_raw=%d g_fir_out=%d g_integ=%d)"
          % (n, out, plant, raw, fir, integ))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
