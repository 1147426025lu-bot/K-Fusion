/* ecos_xipc.h — ECoS 跨域 IPC 最小运行时：共享布局与无锁环操作
 *
 * 设计约束（为什么长这样）
 * -----------------------
 * 1. **一份头，两个宿主**。root cell（Linux 用户态）和 inmate（NuttX）编译同一份
 *    布局定义与同一组 inline 操作。任何"两端各写一遍"的做法迟早会出现
 *    "offset 差 4 字节、字段类型不一致"这类只有上板才暴露的错误。
 * 2. **按 peer 分片**。Jailhouse IVSHMEM v2 的 rw_section 是 3 peer × 12 KiB
 *    （0x9000）。旧 PoC 直接拿起始 16 字节当 mailbox，多一方向就要吵架。
 *    本协议：peer0 片 = root→inmate 环，peer1 片 = inmate→root 环，peer2 片空着。
 * 3. **SPSC 无锁环**，不用任何两端共享的锁（跨域自旋锁在 cache 不一致下是陷阱）。
 *    每个环有**唯一**写者与**唯一**读者，head/tail 各占一条 cache line。
 * 4. **提交靠 seq**：先写负载、再写 seq、最后推进 head。读者只有看到
 *    `slot.seq == tail+1` 才认为该槽有效——避免读到"正在写"的半个槽。
 * 5. **cache 必须自己管**：NuttX 侧该区域 cacheable，root 侧（uio mmap）非 cache。
 *    inmate 写后 clean、读前 invalidate；root 侧是 no-op。这一条不做就会
 *    "两边都正常跑但互相看不见"，而且看起来像逻辑 bug。
 *
 * 时间基准
 * --------
 * `xipc_counter()` 直接读 CNTPCT_EL0。Jailhouse 的 arm-common/arm64 代码里
 * 没有任何 cntvoff / CNTPCT 的陷获或偏移（已 grep 确认），所以**两个 cell 看到
 * 同一个物理计数器**，单程延迟可以直接算。这个前提在 `xipc_selftest` 里做了
 * 经验校验：单程值必须为正且小于 RTT，否则判为"时间基准不可信"并只报 RTT。
 */
#ifndef ECOS_XIPC_H
#define ECOS_XIPC_H

#include <stdint.h>

/* ---------------- 共享区几何 ---------------- */

#define XIPC_BASE        0x1faf1000u   /* rw_section 起始（root: /dev/uio0 map2） */
#define XIPC_MAP_SIZE    0x9000u       /* 36 KiB = 3 peer × 12 KiB */
#define XIPC_PEER_STRIDE 0x3000u       /* 每个 peer 的片大小 */

/* 各片用途（刻意写死，不用"约定俗成就好"） */
#define XIPC_RING_R2I    0x0000u       /* peer0 片：root → inmate */
#define XIPC_RING_I2R    0x3000u       /* peer1 片：inmate → root */
#define XIPC_STATS_OFF   0x6000u       /* peer2 片：双方只读的统计/探针 */

#define XIPC_MAGIC       0x4B465849u   /* "XIFK" 小端字面值，仅用于识别已初始化 */
#define XIPC_VERSION     3u   /* PR11-B：槽里发的是**µs 时间戳**（原 v2 是周期索引，多速率下不可比） */
#define XIPC_SLOT_N      224u          /* 224 × 20 B = 4480 B，加上 256 B 头 = 4736 B */
#define XIPC_SLOT_BYTES  20u

/* 消息类型。控制语义放在 kind 里，而不是靠"约定 a 的含义"。 */
#define XIPC_KIND_HELLO  1u   /* inmate 上线，a = 能力位图，b = cntfrq */
#define XIPC_KIND_ECHO   2u   /* 回环：b = 收到时的 counter，a = 原 a */
#define XIPC_KIND_TICK   3u   /* 周期数据（控制量）：a = 本侧 tick 计数（PING 节拍） */
#define XIPC_KIND_PING   4u   /* root 发的请求，a = 序号，b = 发送 counter 低 32 位 */
#define XIPC_KIND_STOP   5u   /* 让 inmate 收工 */
#define XIPC_KIND_MODE   6u   /* 切模式：a = tick_every（0=不推），b = period_us（0=忙等） */
#define XIPC_KIND_DATA   7u   /* 跨域数据：a = 通道号，b = int32 值，ts_us = 发布时刻（µs） */

struct xipc_slot {
    volatile uint32_t seq;    /* 提交标记：= 生产者在环中的序号 + 1；跨域可作 release_id */
    volatile uint32_t kind;
    volatile uint32_t a;
    volatile uint32_t b;
    /* 发布时刻（µs，共享计数器派生，32 位 ≈ 71 分钟回绕）。
     * ⚠ v2 这里存的是"周期索引"（counter/period_ticks）。多速率下两侧周期不同，
     *   索引不是同一个量纲、相减没有意义，所以 v3 改成**绝对时间戳**：
     *   age_us = 消费时刻 - 发布时刻，与两侧周期无关；要几拍再除以**本域**周期。 */
    volatile uint32_t ts_us;
};

struct xipc_ring {
    volatile uint32_t magic;       /* 0x00 */
    volatile uint32_t version;     /* 0x04 */
    volatile uint32_t cap;         /* 0x08 slot 数（必须 = XIPC_SLOT_N） */
    volatile uint32_t slot_bytes;  /* 0x0C */
    volatile uint32_t session;     /* 0x10 本次实验的会话号（见下） */
    volatile uint32_t pad0[11];    /* 0x14..0x3C */
    volatile uint32_t head;        /* 0x40 生产者推进（自由增长，不取模） */
    volatile uint32_t pad1[15];    /* 0x44..0x7F */
    volatile uint32_t tail;        /* 0x80 消费者推进 */
    volatile uint32_t pad2[15];    /* 0x84..0xBF */
    volatile uint32_t drops;       /* 0xC0 生产者丢弃计数（环满） */
    volatile uint32_t pad3[15];    /* 0xC4..0xFF */
    struct xipc_slot slot[XIPC_SLOT_N];  /* 0x100 */
};

/* 统计片：各字段归属固定，避免双方抢同一 cache line 上的不同字（伪共享会让
 * 测出来的延迟跟着对面写频率走）。每条 cache line 只给一个写者。 */
struct xipc_stats {
    volatile uint32_t magic;        /* 0x00 root 写 */
    volatile uint32_t mode;         /* 0x04 root 写：1=irq 门铃，2=poll */
    volatile uint32_t rtt_n;        /* 0x40 root 写：RTT 样本数 */
    volatile uint32_t rtt_min_ns;
    volatile uint32_t rtt_max_ns;
    volatile uint32_t rtt_last_ns;
    volatile uint32_t ow_min_ns;    /* 0x80 root 写：单程（root→inmate） */
    volatile uint32_t ow_max_ns;
    volatile uint32_t ow_last_ns;
    volatile uint32_t ow_bad;      /* 单程为负/异常（= 时间基准不可信）的次数 */
    volatile uint32_t i_tick;       /* 0xC0 inmate 写：周期计数 */
    volatile uint32_t i_rx;         /*         收到的 PING 数 */
    volatile uint32_t i_drop;       /*         环满丢弃数 */
    volatile uint32_t i_cnt_lo;     /*         最近一次读到的 counter 低 32 位 */
};

/* ---------------- 布局自检（编译期） ----------------
 * 两端的字段偏移必须逐字节一致，否则就是"两边都跑得动、数据就是不对"。
 * 用 typedef 技巧而不是 _Static_assert：C99 也编得过，内核/NuttX 都吃。 */
#define XIPC_JOIN2(a, b) a##b
#define XIPC_JOIN(a, b)  XIPC_JOIN2(a, b)
#define XIPC_ASSERT_EQ(cond) \
    typedef char XIPC_JOIN(xipc_assert_, __LINE__)[(cond) ? 1 : -1]

XIPC_ASSERT_EQ(sizeof(struct xipc_slot) == XIPC_SLOT_BYTES);
XIPC_ASSERT_EQ(__builtin_offsetof(struct xipc_ring, head)  == 0x40);
XIPC_ASSERT_EQ(__builtin_offsetof(struct xipc_ring, tail)  == 0x80);
XIPC_ASSERT_EQ(__builtin_offsetof(struct xipc_ring, drops) == 0xC0);
XIPC_ASSERT_EQ(__builtin_offsetof(struct xipc_ring, slot)  == 0x100);
XIPC_ASSERT_EQ(sizeof(struct xipc_ring) <= XIPC_PEER_STRIDE); /* 一个环必须装进一个 peer 片（0x3000） */

/* ---------------- 编译器/平台适配 ---------------- */

#if defined(XIPC_NUTTX)
#  include <nuttx/config.h>
extern void up_clean_dcache(uintptr_t start, uintptr_t end);
extern void up_invalidate_dcache(uintptr_t start, uintptr_t end);
#  define XIPC_SYNC_WRITE(p, n) up_clean_dcache((uintptr_t)(p), (uintptr_t)(p) + (n))
#  define XIPC_SYNC_READ(p, n)  up_invalidate_dcache((uintptr_t)(p), (uintptr_t)(p) + (n))
#else
/* root 侧 uio mmap 的是 device 内存（非 cache），无需维护 */
#  define XIPC_SYNC_WRITE(p, n) do { (void)(p); (void)(n); } while (0)
#  define XIPC_SYNC_READ(p, n)  do { (void)(p); (void)(n); } while (0)
#endif

#define XIPC_BARRIER() __sync_synchronize()

/* 计数器：用 CNTVCT_EL0，不用 CNTPCT_EL0。
 *
 * 踩过的坑：`mrs x, cntpct_el0` 在 **EL0 上默认是未定义指令**——只有内核把
 * CNTKCTL_EL1.EL0PCTEN 置 1 才放行，这块板子的内核没置，于是 root 侧用户态
 * 程序直接 SIGILL（而且是静默的：stdout 走管道是块缓冲，崩了什么都留不下）。
 * CNTVCT_EL0 反过来一定可用（Linux 的 VDSO 就靠它算时间）。
 *
 * 为什么两个 cell 能比：Jailhouse 的 arm-common/arm64 里没有任何 CNTVOFF 的
 * 写入或 CNTPCT/CNTVCT 的陷获（已 grep 确认），虚拟计数器 = 物理计数器，
 * 所以两侧读同一个数。这个前提由 root 侧的"时间基准异常样本"经验校验兜底。
 */
static inline uint64_t xipc_counter(void)
{
#if defined(__aarch64__)
    uint64_t v;
    __asm__ __volatile__("isb; mrs %0, cntvct_el0" : "=r"(v));
    return v;
#else
    return 0;
#endif
}

static inline uint32_t xipc_counter_hz(void)
{
#if defined(__aarch64__)
    uint64_t v;
    __asm__ __volatile__("mrs %0, cntfrq_el0" : "=r"(v));
    return (uint32_t)v;
#else
    return 0;
#endif
}

/* ---------------- 环操作（SPSC） ---------------- */

static inline void xipc_ring_init(struct xipc_ring *r, uint32_t session)
{
    /* ⚠ 先撤 magic 再填内容：cell 重建时 IVSHMEM 内存**不会被清零**，
     * 上一轮留下的 magic 会让后启动的一侧误判"环已就绪"，抢在 owner
     * 初始化之前发消息，然后被 init 抹掉。这个坑实测踩到过（表现为
     * root 30 s 收不到 HELLO，而 inmate 明明发过）。
     * session 更狠一层：每轮用 CNTPCT 生成，等 session 变化才算"新一轮"。 */
    r->magic   = 0;
    r->session = 0;
    XIPC_BARRIER();
    XIPC_SYNC_WRITE(r, 0x40);

    r->version    = XIPC_VERSION;
    r->cap        = XIPC_SLOT_N;
    r->slot_bytes = XIPC_SLOT_BYTES;
    r->head       = 0;
    r->tail       = 0;
    r->drops      = 0;
    {
        uint32_t i;
        for (i = 0; i < XIPC_SLOT_N; i++) {
            r->slot[i].seq  = 0;
            r->slot[i].kind = 0;
            r->slot[i].a    = 0;
            r->slot[i].b    = 0;
        }
    }
    XIPC_SYNC_WRITE(r, sizeof(*r));
    XIPC_BARRIER();
    r->session = session;
    XIPC_SYNC_WRITE(r, 0x40);
    XIPC_BARRIER();
    r->magic = XIPC_MAGIC;
    XIPC_SYNC_WRITE(r, 0x40);
    XIPC_BARRIER();
}

static inline int xipc_ring_ready(const struct xipc_ring *r)
{
    return r->magic == XIPC_MAGIC && r->cap == XIPC_SLOT_N
        && r->slot_bytes == XIPC_SLOT_BYTES;
}

/* 等"新一轮"：owner 每轮写不同的 session，看到它就说明这一轮的环已经清干净 */
static inline int xipc_wait_session(const struct xipc_ring *r, uint32_t prev,
                                    uint32_t *out_session)
{
    if (r->magic != XIPC_MAGIC)
        return 0;
    if (r->session == 0 || r->session == prev)
        return 0;
    if (out_session)
        *out_session = r->session;
    return 1;
}

/* 生产一条（带发布周期索引）。返回 0 成功，1 环满（已计 drops）。
 * ts_us = 发布时刻（µs）；管理消息传 0，表示"无时间戳"。 */
static inline int xipc_pub_stamped(struct xipc_ring *r, uint32_t kind,
                                   uint32_t a, uint32_t b, uint32_t ts_us)
{
    uint32_t h = r->head;
    uint32_t t = r->tail;
    struct xipc_slot *s;

    if ((uint32_t)(h - t) >= XIPC_SLOT_N) {
        r->drops++;
        XIPC_SYNC_WRITE(&r->drops, 4);
        return 1;
    }

    s = &r->slot[h % XIPC_SLOT_N];
    s->seq  = 0;              /* 先撤掉提交标记：消费者不会读到半个槽 */
    s->kind = kind;
    s->a    = a;
    s->b    = b;
    s->ts_us = ts_us;
    XIPC_SYNC_WRITE(s, XIPC_SLOT_BYTES);   /* 负载先落地 */
    XIPC_BARRIER();
    s->seq = h + 1;           /* 再提交 */
    XIPC_SYNC_WRITE(s, XIPC_SLOT_BYTES);
    XIPC_BARRIER();
    r->head = h + 1;
    XIPC_SYNC_WRITE(&r->head, 4);
    return 0;
}

/* 兼容旧管理协议（root_side.c / xipc_main.c 仍用 4 参，tick 填 0）。 */
static inline int xipc_pub(struct xipc_ring *r, uint32_t kind, uint32_t a, uint32_t b)
{
    return xipc_pub_stamped(r, kind, a, b, 0);
}

/* 看队头但不消费。返回 1 = 收到一条（*out 已填），0 = 空或该槽尚未提交。 */
static inline int xipc_peek(struct xipc_ring *r, struct xipc_slot *out)
{
    uint32_t t = r->tail;
    struct xipc_slot *s;

    XIPC_SYNC_READ(r, sizeof(*r));
    if (r->head == t)
        return 0;

    s = &r->slot[t % XIPC_SLOT_N];
    XIPC_SYNC_READ(s, XIPC_SLOT_BYTES);
    if (s->seq != t + 1)       /* 生产者已推进 head 但负载还没落地（极窄窗口） */
        return 0;

    out->seq  = s->seq;
    out->kind = s->kind;
    out->a    = s->a;
    out->b    = s->b;
    out->ts_us = s->ts_us;
    return 1;
}

static inline void xipc_consume(struct xipc_ring *r)
{
    r->tail = r->tail + 1;
    XIPC_SYNC_WRITE(&r->tail, 4);
    XIPC_BARRIER();
}

/* 便捷封装：取一条，成功就消费 */
static inline int xipc_pop(struct xipc_ring *r, struct xipc_slot *out)
{
    if (!xipc_peek(r, out))
        return 0;
    xipc_consume(r);
    return 1;
}

#endif /* ECOS_XIPC_H */
