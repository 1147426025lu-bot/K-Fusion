"""xdom_bridge.py —— 生成跨域数据桥（PR10-C）。

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
"""
import io


_H = """/* ecos_xdom.h — 跨域数据桥（由 ECoS 拆分器生成，勿手改）
 *
 * 本文件只服务**一个域**：它知道自己该往哪个环发、从哪个环收。
 * 方向来自拆分器写的 peer_ring，不是猜的。
 */
#ifndef ECOS_XDOM_H
#define ECOS_XDOM_H

#include <stdint.h>

void ecos_xdom_init(void);
void ecos_xdom_pub(unsigned channel, int32_t value);
void ecos_xdom_sub(unsigned channel, int32_t *dst);

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
%(wait_include)s
#define ECOS_XDOM_BASE  0x%(base)08xu
#define ECOS_XDOM_OUT   0x%(out)04xu
#define ECOS_XDOM_IN    0x%(in)04xu
#define ECOS_XDOM_OWNER %(owner)d

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

%(init_body)s}

void ecos_xdom_pub(unsigned channel, int32_t value)
{
	if (!xr_ready)
		return;
	(void)xipc_pub(xr_out, XIPC_KIND_DATA, channel, (uint32_t)value);
}

void ecos_xdom_sub(unsigned channel, int32_t *dst)
{
	struct xipc_slot s;

	if (!xr_ready || dst == 0)
		return;
	/* 只认本通道的**最近一条**。跨域读的本来就是上一拍的值，
	 * 积压的旧消息继续留着只会让读到更旧的数据。 */
	while (xipc_pop(xr_in, &s)) {
		if (s.kind == XIPC_KIND_DATA && s.a == channel) {
			*dst = (int32_t)s.b;
			break;
		}
	}
}
"""

BASE = 0x1faf1000


def emit(domain_meta):
    """domain_meta: 拆分器写的 per-domain meta.json（含 peer_ring 与 pub/sub）。"""
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
    return {
        "ecos_xdom.h": _H,
        "ecos_xdom.c": _C % {
            "kernel": 1 if domain_meta.get("kernel") else 0,
            "init_body": init_body,
            "domain": domain_meta.get("domain", "?"),
            "base": BASE,
            "out": out_off,
            "in": int(ring.get("in", 0)),
            "owner": owner,
            "owner_note": owner_note,
            "kernel_include": ("#define ECOS_XDOM_KERNEL 1\t/* ring-0 宿主 */\n"
                               "#include <linux/io.h>\t/* ioremap */\n"
                               if domain_meta.get("kernel") else ""),
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
