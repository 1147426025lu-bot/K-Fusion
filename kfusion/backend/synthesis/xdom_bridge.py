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
 */
#include "ecos_xdom.h"
#include "ecos_xipc.h"

#define ECOS_XDOM_BASE  0x%(base)08xu
#define ECOS_XDOM_OUT   0x%(out)04xu
#define ECOS_XDOM_IN    0x%(in)04xu

static struct xipc_ring *xr_out;
static struct xipc_ring *xr_in;
static int xr_ready;

void ecos_xdom_init(void)
{
    xr_out = (struct xipc_ring *)(uintptr_t)(ECOS_XDOM_BASE + ECOS_XDOM_OUT);
    xr_in  = (struct xipc_ring *)(uintptr_t)(ECOS_XDOM_BASE + ECOS_XDOM_IN);
    /* 环由 root 侧初始化（owner）。没就绪就什么都不做——
     * 宁可这个域不出数，也不要往一块没初始化的共享内存里写。 */
    xr_ready = xipc_ring_ready(xr_out) && xipc_ring_ready(xr_in);
}

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
    return {
        "ecos_xdom.h": _H,
        "ecos_xdom.c": _C % {
            "domain": domain_meta.get("domain", "?"),
            "base": BASE,
            "out": ring.get("out", 0),
            "in": ring.get("in", 0),
        },
    }


def summary(domain_meta):
    """给日志/自测用的一行摘要。"""
    pub = domain_meta.get("publish") or []
    sub = domain_meta.get("subscribe") or []
    return "域 %s：发布 %d 项 %s；订阅 %d 项 %s" % (
        domain_meta.get("domain"),
        len(pub), ",".join("%s→%s" % (p.get("global"), p.get("to")) for p in pub),
        len(sub), ",".join("%s←%s" % (s.get("global"), s.get("from")) for s in sub))
