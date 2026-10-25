/****************************************************************************
 * apps/examples/xipc/xipc_main.c
 *
 * ECoS 跨域 IPC —— NuttX（Jailhouse 非根 cell）侧。
 *
 * 与 root cell 侧共用同一份布局头 ecos_xipc.h（部署时从
 * kfusion/runtime/xshm/ecos_xipc.h 复制，**不要手改**；同步脚本只有一个真源）。
 *
 * 为什么这侧不自带"模式"
 * ----------------------
 * 本轮实验的板子上串口只有 Pi→适配器一个方向（能看不能敲），所以没法在 NSH 里
 * 手输参数。改用 root 侧通过 MODE 消息在**运行时**切模式：
 *     XIPC_KIND_MODE(a=tick_every, b=period_us)
 * 好处不只是省一次交互——它把"控制面"也架在同一条环上，正好是要证明的东西：
 * 一条 SPSC 环 + 两类消息就够跑一个跨域控制回路。
 *
 * 两种模式回答两个不同的问题，混用会把结论说错一个数量级：
 *   period_us = 0   忙等：收到 PING 立刻回 ECHO。测的是**IPC 通路本身**的延迟下限。
 *   period_us > 0   周期边界处理：每 period 才看一次环。测的是**实时侧按控制周期
 *                   采样**时跨域请求的真实代价——此时延迟下限是一个周期。
 *
 * TICK（主动推送）默认关闭：不限速地推会在几百微秒内把 224 槽的环填满，随后连
 * ECHO 都发不出去，表现为"root 大量超时"。这不是协议 bug，是用法错。
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "ecos_xipc.h"

static struct xipc_ring  *g_r2i;
static struct xipc_ring  *g_i2r;
static struct xipc_stats *g_st;

int main(int argc, FAR char *argv[])
{
    int busy = 1, owner = 0, period_us = 0, tick_every = 0;
    long iters = -1;
    int timeout_ms = 180000;
    uint32_t hz;
    long n;
    uint32_t tick = 0, rx = 0, echo_ok = 0, echo_drop = 0, mode_rx = 0, tick_acc = 0;
    uint32_t prev_session = 0, cur_session = 0;
    uint64_t t0, t1, deadline;
    int stop = 0;
    int i;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-p")) {
            busy = 1;
        } else if (!strcmp(argv[i], "-o")) {
            owner = 1;
        } else if (!strcmp(argv[i], "-i") && i + 1 < argc) {
            period_us = atoi(argv[++i]);
            busy = period_us > 0 ? 0 : 1;
        } else if (!strcmp(argv[i], "-n") && i + 1 < argc) {
            iters = atol(argv[++i]);
        } else if (!strcmp(argv[i], "-T") && i + 1 < argc) {
            timeout_ms = atoi(argv[++i]);
        } else if (!strcmp(argv[i], "-t") && i + 1 < argc) {
            tick_every = atoi(argv[++i]);
        } else {
            /* 自启动时 NuttX 会把 INIT_ARGS 塞成 argv[1]（task_spawn 的行为），
             * 不能因为"不认识"就退出——那样开机只打一行 usage 就结束了。
             * 只告警并继续，所有参数都有缺省值。
             * 注意日志必须 **纯 ASCII**：中文经 mini-UART 出去是乱码。 */
            printf("xipc: ignore unknown arg argv[%d]=%s\n", i, argv[i]);
        }
    }

    g_r2i = (struct xipc_ring *)(uintptr_t)(XIPC_BASE + XIPC_RING_R2I);
    g_i2r = (struct xipc_ring *)(uintptr_t)(XIPC_BASE + XIPC_RING_I2R);
    g_st  = (struct xipc_stats *)(uintptr_t)(XIPC_BASE + XIPC_STATS_OFF);

    hz = xipc_counter_hz();

    if (owner) {
        xipc_ring_init(g_r2i, (uint32_t)xipc_counter() | 1u);
        xipc_ring_init(g_i2r, (uint32_t)xipc_counter() | 1u);
        g_st->magic = XIPC_MAGIC;
    }

    /* root 是 owner。它可能比本 cell 晚启动（人要先看 cell 起来才去跑它），
     * 所以等得久一点：60 s。等不到就打清楚原因退出，不静默空转。
     *
     * ⚠ 不能只等 magic：IVSHMEM 内存**不会随 cell 重建清零**，上一轮留下的
     * magic 会让这里立刻通过，于是 HELLO 抢在 root 初始化之前发出、随后被抹掉。
     * 实测症状是 root 等 30 s 也收不到 HELLO，而本侧日志显示"已发过"。
     * 正确做法是等 session 变化（owner 每轮写 CNTPCT 生成的新值）。 */
    prev_session = g_r2i->session;
    for (i = 0; i < 6000; i++) {
        if (xipc_wait_session(g_r2i, prev_session, &cur_session))
            break;
        usleep(10000);
    }
    if (!xipc_ring_ready(g_r2i) || cur_session == 0) {
        printf("xipc: rings not ready after 60s (root not running?) magic=0x%08x "
               "session=0x%08x\n", g_r2i->magic, g_r2i->session);
        return 2;
    }
    printf("xipc: session 0x%08x -> 0x%08x\n", prev_session, cur_session);

    printf("xipc START period=%dus tick_every=%d timeout=%dms cntfrq=%u base=0x%08x\n",
           period_us, tick_every, timeout_ms, hz, XIPC_BASE);

    /* 上线通知：让 root 知道实时侧已经在环上了 */
    xipc_pub(g_i2r, XIPC_KIND_HELLO, XIPC_VERSION, hz);

    /* ⚠ 退出条件必须是**时间**，不是迭代数。忙等一圈只要几十纳秒，
     * 2000 万次不到 0.5 秒就跑完了 —— 上一版就是"还没开始测就退出了"，
     * 表现为 root 侧 30 s 等不到 hello。 */
    t0 = xipc_counter();
    deadline = t0 + (uint64_t)(hz / 1000u) * (uint64_t)timeout_ms;

    for (n = 0; !stop; n++) {
        struct xipc_slot s;

        if (iters >= 0 && n >= iters)
            break;
        if ((n & 0x3ff) == 0 && xipc_counter() > deadline)
            break;

        while (xipc_pop(g_r2i, &s)) {
            switch (s.kind) {
            case XIPC_KIND_PING:
                rx++;
                if (xipc_pub(g_i2r, XIPC_KIND_ECHO, s.a,
                             (uint32_t)xipc_counter()) == 0)
                    echo_ok++;
                else
                    echo_drop++;
                break;
            case XIPC_KIND_MODE:
                period_us  = (int)s.b;
                busy       = (period_us == 0);
                tick_every = (int)s.a;
                tick_acc   = 0;
                mode_rx++;
                /* 模式切换要可见：串口是唯一能看到这一侧的窗口 */
                printf("xipc MODE period=%dus tick_every=%d\n", period_us, tick_every);
                break;
            case XIPC_KIND_STOP:
                stop = 1;
                break;
            default:
                break;
            }
        }

        if (tick_every > 0 && ++tick_acc >= (uint32_t)tick_every) {
            tick_acc = 0;
            tick++;
            (void)xipc_pub(g_i2r, XIPC_KIND_TICK, tick, (uint32_t)xipc_counter());
        }

        if (busy) {
            /* 忙等：什么都不做，下一圈继续看环 */
        } else if (period_us > 0) {
            usleep(period_us);
        }
    }

    t1 = xipc_counter();

    g_st->i_tick = tick;
    g_st->i_rx   = rx;
    g_st->i_drop = g_i2r->drops;
    XIPC_SYNC_WRITE(g_st, sizeof(*g_st));

    printf("xipc DONE tick=%u rx=%u echo=%u echo_drop=%u ringdrop=%u mode_rx=%u elapsed_ms=%u\n",
           tick, rx, echo_ok, echo_drop, g_i2r->drops, mode_rx,
           (unsigned)((t1 - t0) / (hz / 1000u)));
    printf("xipc: ring r2i(head=%u tail=%u) i2r(head=%u tail=%u)\n",
           g_r2i->head, g_r2i->tail, g_i2r->head, g_i2r->tail);
    return 0;
}
