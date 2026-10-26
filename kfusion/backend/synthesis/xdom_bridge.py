"""xdom_bridge.py —— 生成跨域数据桥（PR10-C / PR10-F）。

拆分成两个域之后，同一个全局量（例如 `g_act`）不再是两边的共享内存：一边写、
一边读会各自读到自己的副本，跑起来不报错、结果全错。所以要把这些量改成
**过环的消息**——这就是本模块生成的桥。

原则：桥要小、要显式。生成出来的 `ecos_xdom.c` 只做三件事：init 时把两个环的
地址定下来、pub 时把 int32 塞进槽、sub 时把本通道最近一条取回。没有线程、没有锁、
没有定时器——调度归宿主，桥只负责搬字。

跨域语义（必须写进注释，否则半年后没人记得为什么这样写）：
  · pub 放在产生它的阶段**之后**：晚发一个周期，可用时刻就晚一个周期。
  · sub 放在消费它的阶段**之前**：跨域本来就是"上一拍的值"，多出来的旧消息
    直接丢弃，不要攒着——攒着只会让读到更旧的数据。

PR10-F 版本化（三个标识必须分清，别混）
--------------------------------------
  · **消息序号** = 环槽 `seq`（每提交一条 +1）。它只回答"这是第几条消息"，
    **不是**任务释放编号：一个周期发两条、或某周期不发，seq 就对不上拍。
  · **生产周期** = `publish_tick`（消息的 `tick` 字段）。由**共享物理计数器**派生：
    `tick = xipc_counter() / period_ticks`。两侧用同一个计数器、同一个周期长度
    ⇒ 同一物理时刻算出的 tick 相同，**与各自启动相位无关**。
  · **消费周期** = 消费者调 `ecos_xdom_cycle_now()` 得到的同一口径周期号。
  数据年龄（拍）= `consumer_cycle − publish_tick`，这才是真实的时间差，
  不含"两边各自从自己 init 起算"的启动偏移。

消费侧还会为**每个通道**记录最近一次的 producer/consumer/age 与累计统计，
供上层打印逐拍证据（`ecos_xdom_evidence` / `ecos_xdom_report_evidence`）。
"""
import io


_H = """/* ecos_xdom.h — 跨域数据桥（由 ECoS 拆分器生成，勿手改）
 *
 * 本文件只服务**一个域**：它知道自己该往哪个环发、从哪个环收。
 * 方向来自拆分器写的 peer_ring，不是猜的。
 *
 * PR10-F 版本化：publish_tick 由**共享物理计数器**派生（不是本域 periods、不是环 seq），
 * 所以两侧同一物理时刻的周期号相同；消费侧 age = consumer_cycle - publish_tick
 * 是真实时间差（拍），不受启动相位影响。
 */
#ifndef ECOS_XDOM_H
#define ECOS_XDOM_H

#include <stdint.h>

/* 逐拍数据版本证据（消费侧记录）：每通道最近一次消费的
 * 生产周期 / 消费周期 / 年龄（拍），以及累计与过期统计。 */
struct ecos_xdom_evid {
	uint32_t prod;      /* 这条数据由生产者在第几个（绝对）控制周期产生 */
	uint32_t cons;      /* 本域消费它时所在的（绝对）周期 */
	uint32_t age;       /* cons - prod（拍数） */
	uint32_t n;         /* 累计消费次数 */
	uint32_t n_stale;   /* 年龄超过上限的次数（仅当 ECOS_XDOM_MAX_AGE > 0） */
	uint32_t max_age;   /* 见过的最大年龄（拍） */
};

void ecos_xdom_init(void);
void ecos_xdom_pub(unsigned channel, int32_t value);
void ecos_xdom_sub(unsigned channel, int32_t *dst);
void ecos_xdom_sub_tick(unsigned channel, int32_t *dst, uint32_t *out_tick);
uint32_t ecos_xdom_cycle_now(void);                 /* 本域当前绝对周期号（共享计数器派生） */
int ecos_xdom_evidence(unsigned channel, struct ecos_xdom_evid *out);
void ecos_xdom_report_evidence(void);               /* 逐通道打印最近一次的 prod/cons/age */
uint32_t ecos_xdom_epoch(void);                     /* 统一 epoch：本域所见 session */

#endif /* ECOS_XDOM_H */
"""

_C = """/* ecos_xdom.c — 跨域数据桥（由 ECoS 拆分器生成，勿手改）
 *
 * 布局与环操作全部来自 ecos_xipc.h —— 与对端**同一份头**。
 * 基址与两个环的偏移由拆分器按本域角色写死：
 *   %(domain)s 域 → out 环 0x%(out)04x，in 环 0x%(in)04x
 *
 * 谁是环的 owner
 * -------------
 * %(owner_note)s
 * owner 负责把两个环初始化（含 session）；非 owner 只是等它就绪。
 * 这条分工不是随便定的：两侧都初始化就会互相覆盖，两侧都不初始化就永远等不到。
 */
%(kernel_include)s#include "ecos_xdom.h"
#include "ecos_xipc.h"
%(stdio_include)s%(wait_include)s
#define ECOS_XDOM_BASE  0x%(base)08xu
#define ECOS_XDOM_OUT   0x%(out)04xu
#define ECOS_XDOM_IN    0x%(in)04xu
#define ECOS_XDOM_OWNER %(owner)d
#define ECOS_XDOM_NCHAN %(nchan)d
#define ECOS_XDOM_PERIOD_US %(period_us)d
#define ECOS_XDOM_MAX_AGE %(max_age)d   /* 0 = 不判过期；>0 = 超过即拒绝该版本 */
/* A/B 注入延迟用：消费者取"LAG 拍前"的值。LAG=0 = 取最新（等价原行为）；
 * LAG=N = 取 N 拍前的值（人为注入跨域延迟，做受控 A/B：同一代码只切这一项）。 */
#define ECOS_XDOM_LAG %(lag)d
#define ECOS_XDOM_HIST (ECOS_XDOM_LAG + 1)

/* 取数语义（对照臂）。为什么要有它：PR10-D 当年"发散到 ±90000"被怀疑是旧桥的
 * 取数方式造成的，但那是**推断**。要变成**实测**，就得让同一份代码只切这一个开关。
 *
 *   0 = latest：每拍把 in 环**抽干**，每通道留最新一条（PR10-F 修正后的行为，默认）
 *   1 = oldest：每拍把 in 环抽干，每通道留**最旧**一条（复刻旧桥的 FIFO 选择顺序）
 *   2 = one   ：每拍**只 pop 一条**，其余留在环里（复刻旧桥的**消费速率**）
 *
 * 0 与 1 的差别只是"同一批里挑哪条"，而 2 的差别是"这拍到底消化多少"——
 * 旧桥 `while(pop){ if(match){...; break;} }` 的 break 之后剩下的消息确实还躺在环里，
 * 于是生产者稍快/相位漂移时环会**累积积压**，age 单调变大。这两件事必须分开测。 */
#define ECOS_XDOM_PICK %(pick)d

/* 为什么有两套地址解析
 * -------------------
 * NuttX（inmate）侧：cell 把同一物理地址映射到**相同虚拟地址**，直接强转就行。
 * Linux（ring-0）侧：IVSHMEM 那块内存被 Jailhouse 从内核线性映射里摘掉了
 *   （用户态能碰是因为 uio_ivshmem 自己 remap），内核直接写会 **SIGSEGV**
 *   ——实测过，insmod 立刻 el0t_64_sync。必须 ioremap 拿到自己的映射。
 */
#ifdef ECOS_XDOM_KERNEL
static void __iomem *xbase;
#else
#define xbase ((void __iomem *)0)
#endif

static struct xipc_ring *xr_out;
static struct xipc_ring *xr_in;
static int xr_ready;

/* 一个控制周期 = 多少物理计数器 tick（init 时按 ECOS_XDOM_PERIOD_US × CNTFRQ 算）。 */
static uint64_t xdom_period_ticks;

/* PR10-F：每通道"最近一条"缓存。
 * 旧实现（PR10-C）的 ecos_xdom_sub 用 while(xipc_pop()) 把 in 环里**所有**消息都
 * pop 消费掉，于是 sub(通道X) 会把通道Y 的消息也吞掉——另一个通道的 sub 后来读到
 * 的是空的；而且它返回的是 FIFO 里**最旧**的匹配消息，与注释"只认本通道最近一条"
 * 正好相反。这里改成：一次性把 in 环抽干到按通道分桶的缓存，sub 只从自己的桶取
 * 最近一条。这样 sub(X) 不会饿死 sub(Y)，且取到的一定是最新的。 */
static int32_t  xdom_cache[ECOS_XDOM_NCHAN];
static uint32_t xdom_cache_tick[ECOS_XDOM_NCHAN];
static uint8_t  xdom_cache_valid[ECOS_XDOM_NCHAN];

/* 逐拍证据：每通道最近一次消费的 prod/cons/age + 累计统计。 */
static struct ecos_xdom_evid xdom_evid[ECOS_XDOM_NCHAN];

/* A/B 用的每通道移位寄存器：存最近 ECOS_XDOM_HIST 个值，取 LAG 拍前那个。
 * LAG=0 时 HIST=1，永远返回最新，与原行为等价。 */
static int32_t  xdom_hist_val[ECOS_XDOM_NCHAN][ECOS_XDOM_HIST];
static uint32_t xdom_hist_tick[ECOS_XDOM_NCHAN][ECOS_XDOM_HIST];
static uint8_t  xdom_hist_pos[ECOS_XDOM_NCHAN];
static uint8_t  xdom_hist_n[ECOS_XDOM_NCHAN];

/* 绝对周期号：共享物理计数器 / 一个周期的 tick 数。
 * 两侧用同一个计数器、同一个周期长度 ⇒ 同一物理时刻算出的值相同，
 * **与各自启动相位/init 时刻无关**。这是 age 可信的前提。 */
static uint32_t xdom_cycle_from_counter(void)
{
	if (xdom_period_ticks == 0)
		return 0;
	return (uint32_t)(xipc_counter() / xdom_period_ticks);
}

static struct xipc_ring *xdom_ring(unsigned int off)
{
#ifdef ECOS_XDOM_KERNEL
	return (struct xipc_ring *)((char __iomem *)xbase + off);
#else
	return (struct xipc_ring *)(uintptr_t)(ECOS_XDOM_BASE + off);
#endif
}

void ecos_xdom_init(void)
{
#ifdef ECOS_XDOM_KERNEL
	/* 必须是 **cacheable** 映射，不能用 ioremap()（device/nGnRE）。
	 * 踩过的坑：NuttX 侧的 up_clean_dcache 只清到 PoU（L2），数据不一定落到
	 * DRAM。用 ioremap() 的话内核绕过 cache 直读内存，于是"用户态看得见、
	 * 内核看不见"——实测模块的 xr_in->head 恒为 0，而对端明明在发。
	 * ioremap_cache() 让内核也走 cache，与对端（MT_NORMAL）内存类型一致才相干。 */
	xbase = ioremap_cache(ECOS_XDOM_BASE, 0x9000);
	if (xbase == NULL)
		return;
#endif
	xr_out = xdom_ring(ECOS_XDOM_OUT);
	xr_in  = xdom_ring(ECOS_XDOM_IN);

	/* 周期长度换算要在等环之前算好：pub/sub 的 tick 全靠它。 */
	{
		uint32_t hz = xipc_counter_hz();

		if (hz != 0 && ECOS_XDOM_PERIOD_US != 0)
			xdom_period_ticks = (uint64_t)ECOS_XDOM_PERIOD_US
					    * (uint64_t)hz / 1000000ull;
	}

%(init_body)s}

void ecos_xdom_pub(unsigned channel, int32_t value)
{
	if (!xr_ready)
		return;
	/* publish_tick = 共享物理计数器派生的**绝对周期号**（不是本域 periods、不是环 seq）。
	 * 对端用 ecos_xdom_cycle_now() 得到同口径的消费周期，相减才是真实年龄。 */
	(void)xipc_pub_ticked(xr_out, XIPC_KIND_DATA, channel, (uint32_t)value,
	                      xdom_cycle_from_counter());
}

void ecos_xdom_sub(unsigned channel, int32_t *dst)
{
	uint32_t tick_dummy;

	ecos_xdom_sub_tick(channel, dst, &tick_dummy);
}

void ecos_xdom_sub_tick(unsigned channel, int32_t *dst, uint32_t *out_tick)
{
	struct xipc_slot s;
#if ECOS_XDOM_PICK == 1
	/* 对照臂 oldest：本批里每通道只收**第一条**（=最旧）。旧桥的选择顺序就是这样
	 * ——它在环上 while(pop) 找到第一条匹配就 break，同一批有多条时拿到最旧的。 */
	uint8_t batch_seen[ECOS_XDOM_NCHAN] = { 0 };
#endif

	if (!xr_ready || dst == 0 || channel >= ECOS_XDOM_NCHAN)
		return;
#if ECOS_XDOM_PICK == 2
	/* 对照臂 one：复刻旧桥的**消费速率**——每拍只消化一条，剩下的留在环里。
	 * 旧代码 `while (xipc_pop(xr_in, &s)) { if (匹配) { *dst = s.b; break; } }`
	 * 那个 break 就是"只取一条"；环里若已有多条，余下的不会消失，下一拍取到的
	 * 是更旧的那条 ⇒ 积压滚起来、age 单调变大。这一步与"挑哪条"是两件事。 */
	if (!xipc_pop(xr_in, &s))
		return;                       /* 环空：旧桥此时也不更新 dst */
	if (s.kind != XIPC_KIND_DATA || s.a != channel)
		return;                       /* 不是本通道：旧桥会把它耗掉，再继续找 */
	xdom_cache[channel]       = (int32_t)s.b;
	xdom_cache_tick[channel]  = s.tick;
	xdom_cache_valid[channel] = 1;
#else
	/* 把 in 环抽干到每通道缓存（最近一条覆盖旧的）。这一抽干对**所有**通道共享
	 * 一次，所以 sub(X) 不会把 Y 的消息丢掉——它们各进各的桶。返回的是本通道
	 * 最近一条，不是 FIFO 最旧（修掉了 PR10-C 的 bug）。 */
	while (xipc_pop(xr_in, &s)) {
		if (s.kind == XIPC_KIND_DATA && s.a < ECOS_XDOM_NCHAN) {
#if ECOS_XDOM_PICK == 1
			if (batch_seen[s.a])
				continue;   /* 本批已有更旧的一条，更新的直接丢 */
			batch_seen[s.a] = 1;
#endif
			xdom_cache[s.a]      = (int32_t)s.b;
			xdom_cache_tick[s.a] = s.tick;
			xdom_cache_valid[s.a] = 1;
		}
	}
#if ECOS_XDOM_PICK == 1
	/* 旧桥在"本批没有本通道消息"时**不更新** dst（break 都没走到）。
	 * 复刻这一点，否则会把上一批的缓存值当成本批数据，对照就失真了。 */
	if (!batch_seen[channel])
		return;
#endif
#endif
	if (xdom_cache_valid[channel]) {
		struct ecos_xdom_evid *e = &xdom_evid[channel];
		unsigned int idx;
		int32_t v;
		uint32_t tick, cons, age;

		/* A/B：把最新值压进每通道移位寄存器，取 LAG 拍前那个（LAG=0 即最新）。 */
		xdom_hist_val[channel][xdom_hist_pos[channel]]  = xdom_cache[channel];
		xdom_hist_tick[channel][xdom_hist_pos[channel]] = xdom_cache_tick[channel];
		xdom_hist_pos[channel] = (uint8_t)((xdom_hist_pos[channel] + 1u) %% ECOS_XDOM_HIST);
		if (xdom_hist_n[channel] < ECOS_XDOM_HIST)
			xdom_hist_n[channel]++;

		if (xdom_hist_n[channel] < ECOS_XDOM_HIST)
			idx = (unsigned int)((xdom_hist_pos[channel] + ECOS_XDOM_HIST - 1u)
					     %% ECOS_XDOM_HIST);
		else
			idx = xdom_hist_pos[channel];   /* 满：最旧 = LAG 拍前 */

		v    = xdom_hist_val[channel][idx];
		tick = xdom_hist_tick[channel][idx];
		cons = xdom_cycle_from_counter();
		age  = cons - tick;   /* 无符号回绕差：正常小，过期则大 */

		/* 记录逐拍证据：这一拍消费到的数据是哪个周期产生的、现在第几拍、差几拍。 */
		e->prod = tick;
		e->cons = cons;
		e->age  = age;
		e->n++;
		if (age > e->max_age)
			e->max_age = age;

		if (out_tick)
			*out_tick = tick;

		/* 过期版本强制拒绝：age 超上限就不更新 dst（阶段沿用上一次/默认值），
		 * 但**仍然计数并保留证据**——拒绝要看得见，不能静默。默认 MAX_AGE=0 不启用。 */
		if (ECOS_XDOM_MAX_AGE != 0 && age > (uint32_t)ECOS_XDOM_MAX_AGE) {
			e->n_stale++;
			return;
		}
		*dst = v;
	}
}

uint32_t ecos_xdom_cycle_now(void)
{
	return xdom_cycle_from_counter();
}

int ecos_xdom_evidence(unsigned channel, struct ecos_xdom_evid *out)
{
	if (out == 0 || channel >= ECOS_XDOM_NCHAN)
		return 0;
	*out = xdom_evid[channel];
	return 1;
}

/* 逐通道打印最近一次消费的 prod/cons/age/n/stale/max。宿主周期性调用即可。 */
void ecos_xdom_report_evidence(void)
{
	unsigned int ch;

	for (ch = 0; ch < ECOS_XDOM_NCHAN; ch++) {
		struct ecos_xdom_evid *e = &xdom_evid[ch];

		if (e->n == 0)
			continue;
#ifdef ECOS_XDOM_KERNEL
		pr_info("xdom ch%%u prod=%%u cons=%%u age=%%u n=%%u stale=%%u max=%%u\\n",
			ch, e->prod, e->cons, e->age, e->n, e->n_stale, e->max_age);
#else
		printf("xdom ch%%u prod=%%u cons=%%u age=%%u n=%%u stale=%%u max=%%u\\n",
		       ch, e->prod, e->cons, e->age, e->n, e->n_stale, e->max_age);
#endif
	}
}

/* 统一 epoch：两侧都读同一个物理计数器生成 session（见 ecos_xipc.h），
 * 所以 in 环的 session 就是本域所见、且与对端一致的 epoch 标识。
 * 注意：epoch 相同只说明"同一轮实验"，**不**代表两边第 N 拍在物理时间上对齐
 * ——对齐由上面的绝对周期号（共享计数器派生）保证，与 session 无关。 */
uint32_t ecos_xdom_epoch(void)
{
	return xr_in ? xr_in->session : 0;
}
"""

BASE = 0x1faf1000


def emit(domain_meta, period_us=1000, max_age=0, lag=0, mode="latest"):
    """domain_meta: 拆分器写的 per-domain meta.json（含 peer_ring 与 pub/sub）。
    period_us: 控制周期（µs），用于把物理计数器换算成周期号。
    max_age:   过期阈值（拍），0 = 不启用过期拒收（只记录证据）。
    lag:       消费者取"LAG 拍前"的值（A/B 注入延迟）；0 = 取最新（默认）。
    mode:      "latest"（默认，抽干取最新）/"oldest"（抽干取最旧）/
               "one"（每拍只取一条、其余留在环里：复刻旧桥的消费速率，
               用来看"积压累积"会不会真的把 age 推上去）→ 生成 ECOS_XDOM_PICK 0/1/2。
    """
    _m = str(mode or "latest").lower()
    pick = {"latest": 0, "oldest": 1, "one": 2}.get(_m)
    if pick is None:
        pick = 1 if _m.startswith("old") else (2 if _m.startswith("one") else 0)
    ring = domain_meta.get("peer_ring") or {}
    out_off = int(ring.get("out", 0))
    # owner = peer0（out 环在 0x0000）。与 PR10 的分片约定一致：
    # peer0 = root，它负责初始化；peer1 = nuttx，只等就绪。
    owner = 1 if out_off == 0 else 0
    owner_note = (
        "本域是 owner（peer0）：启动时初始化两个环并写新 session。"
        if owner else
        "本域不是 owner（peer1）：只等 owner 把环建好，最多等 30 s。")
    if owner:
        init_body = """	/* owner：先撤掉上一轮的残留（IVSHMEM 内存不随 cell 重建清零），
	 * 再用一个新的 session 初始化。session 变化是对端判断"新一轮"的唯一依据。
	 * 这一支**只**生成在 owner 的产物里——peer 侧那份没有这些代码。 */
	{
		uint32_t sess = (uint32_t)xipc_counter() | 1u;

		xipc_ring_init(xr_out, sess);
		xipc_ring_init(xr_in, sess);
	}
	xr_ready = 1;
"""
    else:
        init_body = """	/* peer：等 owner 把环建好，最多 30 s。等不到就一直"未就绪"，
	 * 此时 pub/sub 什么都不做——宁可这个域不出数，
	 * 也不要往一块没初始化的共享内存里写。 */
	{
		unsigned int i;

		for (i = 0; i < 3000; i++) {
			/* 每轮都先失效再读：这一侧的映射是 cacheable，而 owner 是**另一个
			 * cell**。第一次读如果发生在 owner 初始化之前，那一行就带着旧值
			 * 常驻缓存，之后永远看到"未就绪"——实测就是这个症状（等满 30 s 放弃）。
			 * pub/sub 内部本来就带 clean/invalidate，只有这个等待循环漏了。 */
			XIPC_SYNC_READ(xr_out, 0x40);
			XIPC_SYNC_READ(xr_in, 0x40);
			if (xipc_ring_ready(xr_out) && xipc_ring_ready(xr_in)) {
				xr_ready = 1;
				return;
			}
			usleep(10000);
		}
	}
"""
    # 通道数：取 pub/sub 里出现的最大通道号 +1（缓存数组按它定长）
    _chans = [p.get("channel") for p in (domain_meta.get("publish") or [])] + \
             [s.get("channel") for s in (domain_meta.get("subscribe") or [])]
    nchan = (max(c for c in _chans if isinstance(c, int)) + 1) if _chans else 1

    kernel = 1 if domain_meta.get("kernel") else 0
    return {
        "ecos_xdom.h": _H,
        "ecos_xdom.c": _C % {
            "kernel": kernel,
            "init_body": init_body,
            "domain": domain_meta.get("domain", "?"),
            "base": BASE,
            "out": out_off,
            "in": int(ring.get("in", 0)),
            "owner": owner,
            "nchan": nchan,
            "period_us": int(period_us or 0),
            "max_age": int(max_age or 0),
            "lag": int(lag or 0),
            "pick": pick,
            "owner_note": owner_note,
            # kernel 侧要 pr_info（report_evidence 用），故带 kernel.h
            "kernel_include": ("#define ECOS_XDOM_KERNEL 1\t/* ring-0 宿主 */\n"
                               "#include <linux/io.h>\t/* ioremap */\n"
                               "#include <linux/kernel.h>\t/* pr_info */\n"
                               if kernel else ""),
            # 非 kernel 侧用 printf（report_evidence 用）
            "stdio_include": ("" if kernel else "#include <stdio.h>\t/* printf：证据打印 */\n"),
            "wait_include": ("" if owner else
                             "#include <unistd.h>\t/* usleep：等 owner 建环 */\n"),
        },
    }


def summary(domain_meta):
    """给日志/自测用的一行摘要。"""
    pub = domain_meta.get("publish") or []
    sub = domain_meta.get("subscribe") or []
    ring = domain_meta.get("peer_ring") or {}
    who = "owner（负责初始化环）" if int(ring.get("out", -1)) == 0 else "peer（只等就绪）"
    return "域 %s（%s）：发布 %d 项 %s；订阅 %d 项 %s" % (
        domain_meta.get("domain"), who,
        len(pub), ",".join("%s→%s" % (p.get("global"), p.get("to")) for p in pub),
        len(sub), ",".join("%s←%s" % (s.get("global"), s.get("from")) for s in sub))
