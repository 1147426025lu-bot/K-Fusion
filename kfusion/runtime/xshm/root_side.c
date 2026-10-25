/* root_side.c — 跨域 IPC root cell 侧（Linux 用户态）
 *
 * 经 /dev/uio0 map2（Jailhouse IVSHMEM 的 rw_section，36 KiB）与 NuttX inmate 通信。
 * 布局与环操作全部来自 ecos_xipc.h —— 与 inmate 侧**同一份头**。
 *
 * 为什么两侧都是轮询（不是门铃）
 * ------------------------------
 * IVSHMEM 门铃是往寄存器区 0x0C 写 (target<<16 | vector)，由 hypervisor 陷获后向
 * 目标 cell 注入中断。但寄存器区要成为"可陷获的 MMIO"，前提是**本 cell 的 PCI 命令
 * 寄存器置了 MEM 位**——Linux 侧由 uio_ivshmem 驱动完成，NuttX 侧没有 PCI 枚举，
 * 这个位不会置。此时对寄存器区的写不是"没效果"，而是撞 cell 配置里的只读映射
 * （0x1faf0000 只给了 JAILHOUSE_MEM_READ）→ 故障。所以本轮两端都轮询。
 * 对周期性控制任务这不是妥协：实时侧本来就要在周期边界采样。
 *
 * 时间怎么比（这里踩过一个坑）
 * ----------------------------
 * 环槽只有 32 位字段放不下 64 位计数器，所以跨域传的是 **低 32 位**，两端都用
 * 32 位回绕差算增量：`(uint32_t)(b - a)`。@54 MHz 可表示 79 s 内的间隔，够用。
 * 直接把截断值和 64 位值比较会得到"单程恒为负"这种假象（第一版就错了）。
 * 另外计数器单位是 tick，不是 ns，必须按 CNTFRQ 换算。
 *
 * 四个量，别混
 * ------------
 *   rtt(busy)    inmate 忙等 → 通路延迟**下限**（两个 cell 都在空转时的最好情况）
 *   rtt(period)  inmate 按 1 ms 周期采样 → **控制回路里跨域边的真实代价**，
 *                含"等下一个周期边界"的时间，是 ECoS 该用的那个数
 *   visible      inmate 写 TICK 的时刻 → root 读到它的时刻，含消费侧轮询粒度
 *
 * 构建（Pi，root cell）：
 *   gcc -O2 -std=c99 -Wall -Wextra -I<xipc目录> -o root_side root_side.c
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "ecos_xipc.h"

#define DEV     "/dev/uio0"
#define PAGE    4096

static struct xipc_ring *g_r2i;
static struct xipc_ring *g_i2r;
static struct xipc_stats *g_st;
static uint32_t g_hz;

static const int VIS_SLEEP[3] = { 0, 250, 500 };

static uint64_t t2ns(uint64_t ticks)
{
    return g_hz ? ticks * 1000000000ull / g_hz : 0;
}

/* 记原始样本、最后排序算分位数。
 *
 * 为什么不用直方图：上一版用 100 ns 桶 × 8192 = 819 µs 量程，结果"周期采样"那一档
 * 所有样本都撞到最后一桶，p50 报出 819.10 —— 一个看起来很像数字的**桶顶假值**。
 * 分位数不能靠桶近似，尤其当被测值分布跨过桶顶时。样本量只有几千，存原始值便宜得很。 */
struct samples {
    unsigned int *v;
    size_t        n, cap;
};

static void s_push(struct samples *s, uint64_t ns)
{
    if (s->n == s->cap) {
        size_t cap = s->cap ? s->cap * 2 : 1024;
        unsigned int *p = realloc(s->v, cap * sizeof(*p));

        if (!p)
            return;                       /* 内存不足就丢样本，不崩 */
        s->v = p;
        s->cap = cap;
    }
    s->v[s->n++] = (unsigned int)(ns > 0xffffffffull ? 0xffffffffull : ns);
}

static int cmp_u32(const void *a, const void *b)
{
    unsigned int x = *(const unsigned int *)a, y = *(const unsigned int *)b;

    return (x > y) - (x < y);
}

static void s_sort(struct samples *s)
{
    if (s->n > 1)
        qsort(s->v, s->n, sizeof(*s->v), cmp_u32);
}

static uint64_t s_pct(const struct samples *s, double p)
{
    size_t i;

    if (s->n == 0)
        return 0;
    i = (size_t)(p * (double)(s->n - 1) + 0.5);
    if (i >= s->n)
        i = s->n - 1;
    return s->v[i];
}

static void s_report(const char *name, struct samples *s)
{
    uint64_t mn, mx;

    if (s->n == 0) {
        printf("  %-24s (无样本)\n", name);
        return;
    }
    s_sort(s);
    mn = s->v[0];
    mx = s->v[s->n - 1];
    printf("  %-24s n=%-5llu p50=%-8.2f p90=%-8.2f p99=%-8.2f "
           "p99.9=%-9.2f min=%-7.2f max=%-9.2f\n",
           name, (unsigned long long)s->n,
           s_pct(s, 0.50) / 1000.0, s_pct(s, 0.90) / 1000.0,
           s_pct(s, 0.99) / 1000.0, s_pct(s, 0.999) / 1000.0,
           (double)mn / 1000.0, (double)mx / 1000.0);
}

static struct samples g_vis[3];
static uint64_t       g_vis_bad;

static int wait_hello(int timeout_ms)
{
    int waited = 0;

    while (waited < timeout_ms) {
        struct xipc_slot s;

        while (xipc_pop(g_i2r, &s)) {
            if (s.kind == XIPC_KIND_HELLO) {
                printf("xipc-root: inmate 上线 ver=%u cntfrq=%u session=0x%08x\n",
                       s.a, s.b, g_r2i->session);
                return 1;
            }
        }
        usleep(1000);
        waited++;
    }
    return 0;
}

static void flush_i2r(void)
{
    struct xipc_slot s;

    while (xipc_pop(g_i2r, &s)) { }
}

/* 一问一答 n 次。超时窗口给 5 ms——按周期处理的 inmate 最坏要等一个整周期
 * （1 ms）再加调度抖动，5 ms 足够且不会把"真超时"掩盖掉。 */
struct ping_stat {
    struct samples ow, rtt;
    uint64_t n_timeout, n_bad;
};

static void ping_phase(int n, struct ping_stat *ps)
{
    uint32_t seq = 0;
    int i;

    memset(ps, 0, sizeof(*ps));

    for (i = 0; i < n; i++) {
        struct xipc_slot s;
        uint64_t t_send;
        uint32_t sent_lo;
        int got = 0;

        seq++;
        t_send  = xipc_counter();
        sent_lo = (uint32_t)t_send;
        if (xipc_pub(g_r2i, XIPC_KIND_PING, seq, sent_lo) != 0) {
            usleep(50);
            continue;
        }

        while (xipc_counter() < t_send + (uint64_t)g_hz / 200u) {
            uint64_t t_now, rtt_ns;
            uint32_t ow_ticks;

            if (!xipc_pop(g_i2r, &s))
                continue;
            if (s.kind != XIPC_KIND_ECHO || s.a != seq)
                continue;

            t_now    = xipc_counter();
            ow_ticks = s.b - sent_lo;          /* 32 位回绕差，单位 tick */
            rtt_ns   = t2ns(t_now - t_send);

            /* 单程不可能超过 RTT：超了就是时间基准/编码问题，单列，不进分布 */
            if (t2ns(ow_ticks) > rtt_ns)
                ps->n_bad++;
            else
                s_push(&ps->ow, t2ns(ow_ticks));
            s_push(&ps->rtt, rtt_ns);
            got = 1;
            break;
        }
        if (!got)
            ps->n_timeout++;
    }
}

static void ping_report(const char *tag, struct ping_stat *ps)
{
    char name[64];

    snprintf(name, sizeof(name), "单程 %s", tag);
    s_report(name, &ps->ow);
    snprintf(name, sizeof(name), "RTT %s", tag);
    s_report(name, &ps->rtt);
    printf("     超时 %llu ｜ 单程>RTT 的异常样本 %llu\n",
           (unsigned long long)ps->n_timeout, (unsigned long long)ps->n_bad);
}

int main(int argc, char **argv)
{
    int fd, i, iters = 3000, hello_ms = 30000, vis_n = 500, slow_n = 500;
    unsigned char *map;
    struct ping_stat fast, slow;
    int ph;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--iters") && i + 1 < argc)
            iters = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--slow-n") && i + 1 < argc)
            slow_n = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--visible-n") && i + 1 < argc)
            vis_n = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--hello-timeout-ms") && i + 1 < argc)
            hello_ms = atoi(argv[++i]);
        else {
            fprintf(stderr, "usage: %s [--iters N] [--slow-n N] [--visible-n N] "
                            "[--hello-timeout-ms N]\n", argv[0]);
            return 2;
        }
    }

    setvbuf(stdout, NULL, _IOLBF, 0);

    fd = open(DEV, O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "ERROR: 打不开 %s: %s\n"
                        "       需要 root cell 里 Jailhouse IVSHMEM 就绪（jailhouse enable）\n",
                DEV, strerror(errno));
        return 1;
    }

    map = mmap(NULL, XIPC_MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED,
               fd, 2 * PAGE);
    if (map == MAP_FAILED) {
        fprintf(stderr, "ERROR: mmap map2 失败: %s\n", strerror(errno));
        return 1;
    }

    g_r2i = (struct xipc_ring *)(map + XIPC_RING_R2I);
    g_i2r = (struct xipc_ring *)(map + XIPC_RING_I2R);
    g_st  = (struct xipc_stats *)(map + XIPC_STATS_OFF);
    g_hz  = xipc_counter_hz();

    printf("xipc-root: cntfrq=%u Hz\n", g_hz);

    /* root 是 owner。session 用计数器生成，每轮不同——IVSHMEM 内存不随 cell
     * 重建清零，没有它，后启动的一侧会拿上一轮的 magic 当"已就绪"。 */
    {
        uint32_t sess = (uint32_t)xipc_counter() | 1u;

        xipc_ring_init(g_r2i, sess);
        xipc_ring_init(g_i2r, sess);
        g_st->magic = XIPC_MAGIC;
        printf("xipc-root: 环已初始化 session=0x%08x，r2i@0x%04x i2r@0x%04x，"
               "每环 %u 槽 × %u B\n",
               sess, XIPC_RING_R2I, XIPC_RING_I2R, XIPC_SLOT_N, XIPC_SLOT_BYTES);
    }

    if (!wait_hello(hello_ms)) {
        fprintf(stderr, "xipc-root: ❌ %d ms 内没等到 inmate 上线\n", hello_ms);
        return 3;
    }

    /* ---- ① 通路下限：inmate 忙等 ---- */
    printf("\n[phase 1] RTT（inmate 忙等，%d 次）\n", iters);
    ping_phase(iters, &fast);

    /* ---- ② 控制回路真实代价：inmate 切到 1 ms 周期采样 ---- */
    printf("\n[phase 2] MODE(tick=0, period=1000us) → RTT（周期采样，%d 次）\n", slow_n);
    if (xipc_pub(g_r2i, XIPC_KIND_MODE, 0, 1000) != 0) {
        fprintf(stderr, "xipc-root: MODE 发送失败（r2i 环满）\n");
        return 5;
    }
    usleep(50000);
    flush_i2r();
    ping_phase(slow_n, &slow);

    /* ---- ③ 被动方向的可见延迟 ---- */
    printf("\n[phase 3] MODE(tick=1, period=1000us) → 量可见延迟\n");
    if (xipc_pub(g_r2i, XIPC_KIND_MODE, 1, 1000) != 0) {
        fprintf(stderr, "xipc-root: MODE 发送失败（r2i 环满）\n");
        return 5;
    }
    usleep(100000);
    flush_i2r();

    for (ph = 0; ph < 3; ph++) {
        int sleep_us = VIS_SLEEP[ph];

        while (g_vis[ph].n < (size_t)vis_n) {
            struct xipc_slot s;
            int popped = 0;

            while (xipc_pop(g_i2r, &s)) {
                popped = 1;
                if (s.kind == XIPC_KIND_TICK) {
                    uint32_t now_lo = (uint32_t)xipc_counter();
                    uint32_t d32    = now_lo - s.b;

                    if (t2ns(d32) > 1000000000ull) {   /* > 1 s 视为异常 */
                        g_vis_bad++;
                    } else {
                        s_push(&g_vis[ph], t2ns(d32));
                    }
                }
            }
            if (sleep_us > 0 && !popped)
                usleep(sleep_us);
        }
        printf("  消费侧 drain-sleep=%d µs → 收满 %d 个 TICK\n", sleep_us, vis_n);
    }

    printf("\n[phase 4] 发 STOP\n");
    (void)xipc_pub(g_r2i, XIPC_KIND_STOP, 0, 0);
    usleep(200000);
    flush_i2r();

    printf("\n=== 跨域 IPC 实测（Pi4 root cell ↔ NuttX inmate，IVSHMEM rw_section）===\n");
    printf("  时间单位 µs（原始样本排序后的精确分位数，不是桶近似）\n");
    ping_report("[忙等]", &fast);
    ping_report("[周期1ms]", &slow);
    for (ph = 0; ph < 3; ph++) {
        char name[48];

        snprintf(name, sizeof(name), "可见(drain-sleep=%d)", VIS_SLEEP[ph]);
        s_report(name, &g_vis[ph]);
    }
    printf("  可见方向异常=%llu\n", (unsigned long long)g_vis_bad);
    printf("  环状态 i2r(head=%u tail=%u drops=%u) r2i(head=%u tail=%u drops=%u)\n",
           g_i2r->head, g_i2r->tail, g_i2r->drops,
           g_r2i->head, g_r2i->tail, g_r2i->drops);
    printf("  inmate 侧统计: tick=%u rx=%u drop=%u\n",
           g_st->i_tick, g_st->i_rx, g_st->i_drop);

    munmap(map, XIPC_MAP_SIZE);
    close(fd);
    return (fast.ow.n || g_vis[0].n) ? 0 : 4;
}
