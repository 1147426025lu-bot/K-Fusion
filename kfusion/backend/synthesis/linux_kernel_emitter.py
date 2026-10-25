# -*- coding: utf-8 -*-
"""Linux 内核后端（ring-0）生成器。

生成两样东西：

    ecos_fused_body.{h,c}   纯计算周期体（两个后端共用）
    ecos_fused_linux.c      ring-0 宿主：hrtimer 定时 + 每组的实时内核线程

边界的理由（审查明确要求，也是踩过的坑）：

    · 周期体**不能**自带 `clock_nanosleep()` / `main()`。用户态驱动那套
      （`fusion_codegen` 生成的实验驱动）原样塞进内核会在 insmod 后睡死整个核。
    · 释放由宿主负责：每组一个 hrtimer（`HRTIMER_MODE_ABS_PINNED`，钉在指定核）
      + 一个 SCHED_FIFO 内核线程；定时器只置位并唤醒，不执行业务逻辑。
    · 阶段实现（可能用了 printf/malloc 之类）**不在本文件里重写**：交给现有
      LLVM 内核化 Pass（`PLCFusionPass__内核化Pass.cpp`）把 POSIX 调用重映射后
      编成 `stage_impl.o`，本模块只链接它。这就是"复用现有内核化链路"。

本机没有编译器，所以这里只生成源码；构建必须在目标侧（见生成的 README.build.txt）。
"""

BODY_H = "ecos_fused_body.h"
BODY_C = "ecos_fused_body.c"
HOST_C = "ecos_fused_linux.c"
COMPAT_H = "compat/stdint.h"
KBUILD = "Kbuild"
README = "README.build.txt"

# 阶段源码通常是用户态 C，第一行就是 #include <stdint.h>；内核里没有这个头，
# 也没有 int32_t。这里只补**定宽整数 typedef**，不补 libc——用到 printf/malloc/
# pthread 的阶段必须走 plc_kernelize 的 LLVM Pass 重映射，不能靠这层糊过去。
_COMPAT_STDINT = """/* 由 ECoS 生成：仅提供定宽整数 typedef，供内核态编译阶段源码用。
 * 不提供任何 libc 函数。需要 libc 的阶段必须走 LLVM 内核化 Pass。
 */
#ifndef _ECOS_COMPAT_STDINT_H
#define _ECOS_COMPAT_STDINT_H
#include <linux/types.h>

typedef __s8  int8_t;
typedef __u8  uint8_t;
typedef __s16 int16_t;
typedef __u16 uint16_t;
typedef __s32 int32_t;
typedef __u32 uint32_t;
typedef __s64 int64_t;
typedef __u64 uint64_t;

#endif /* _ECOS_COMPAT_STDINT_H */
"""

_LINUX_C = """/* %(name)s —— 由 ECoS 融合方案生成的 ring-0 宿主；不要手改。
 *
 * 方案：%(ngroups)d 组 / %(nstages)d 阶段，周期 %(period)s µs，证据级别 %(evidence)s
 * 组落位：%(layout)s
 *
 * 分工：
 *   ecos_fused_body.c  一拍的计算（无调度原语）
 *   本文件             hrtimer 定时 + 每组一个 FIFO 内核线程
 *   stage_impl.o       阶段实现的**内核化产物**（由现有 LLVM Pass 生成，不在本文件重写）
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/hrtimer.h>
#include <linux/kthread.h>
#include <linux/wait.h>
#include <linux/ktime.h>
#include <linux/sched.h>
#include <linux/sched/rt.h>
#include <linux/string.h>
#include <uapi/linux/sched/types.h>	/* struct sched_attr（uapi；内部 sched/types.h 里没有） */

#include "ecos_fused_body.h"

#define ECOS_GROUPS %(ngroups)d

static unsigned int period_us = %(period)d;
module_param(period_us, uint, 0444);
MODULE_PARM_DESC(period_us, "控制周期（µs），默认取方案里的周期");

/* 实时优先级：默认 99，与用户态 BM2 实验的 FIFO 99 对齐。
 * 注意 5.15 只导出 sched_setattr_nocheck / sched_set_fifo；
 * sched_setscheduler_nocheck 在 kallsyms 里有符号但**没有 EXPORT**，链接/加载会失败。 */
static unsigned int rt_priority = %(rtprio)d;
module_param(rt_priority, uint, 0444);
MODULE_PARM_DESC(rt_priority, "内核线程 SCHED_FIFO 优先级（默认 99，与用户态口径一致）");

static void ecos_set_fifo(struct task_struct *t, unsigned int prio)
{
	struct sched_attr attr;

	memset(&attr, 0, sizeof(attr));
	attr.size = sizeof(attr);
	attr.sched_policy = SCHED_FIFO;
	attr.sched_priority = prio;
	if (sched_setattr_nocheck(t, &attr))
		sched_set_fifo(t);	/* 退回导出接口（优先级 MAX_RT_PRIO/2） */
}

/* 1 组 1 个定时器 + 1 个线程 + 1 个唤醒点 */
struct ecos_group {
	int id;
	struct hrtimer timer;
	struct task_struct *task;
	wait_queue_head_t wait;
	bool pending;
};

static struct ecos_group ecos_groups[ECOS_GROUPS] = {
%(group_init)s
};

static const s64 ecos_phase_ns[ECOS_GROUPS] = {
%(phase_ns)s
};

/* 观察变量：把指定全局量发布成只读 sysfs 参数，加载后可 cat 出来与
 * 用户态参考实现比对。没有观察点就"加载成功"也证明不了算得对。
 * 限制：只支持 int32_t 全局量（其余类型请自己在上层转成 int32_t）。 */
%(digest_decls)s

static unsigned long periods;      /* 已完成周期数（由组 0 计数） */
module_param(periods, ulong, 0444);
MODULE_PARM_DESC(periods, "已完成周期数");

/* 冻结开关：sysfs 上分几次 cat 参数之间会跨周期（10 ms 周期下必然发生），
 * 读出来的 periods / 观察变量就不属于同一拍。写 1 暂停周期体，取完快照再写 0。
 * 冻结期间不执行、也不计数——所以快照里的 periods 与各变量严格同拍。 */
static bool frozen;
module_param(frozen, bool, 0644);
MODULE_PARM_DESC(frozen, "写 1 暂停周期体（取一致快照），写 0 恢复");

static enum hrtimer_restart ecos_tick(struct hrtimer *t)
{
	struct ecos_group *g = container_of(t, struct ecos_group, timer);

	/* 定时器里只置位 + 唤醒；业务逻辑留给线程，避免在硬中断上下文跑控制律 */
	g->pending = true;
	wake_up_interruptible(&g->wait);
	hrtimer_forward_now(&g->timer, ktime_set(0, (s64)period_us * 1000));
	return HRTIMER_RESTART;
}

static void ecos_run_body(int id)
{
	switch (id) {
%(body_cases)s
	}
	if (id == 0)
		periods++;
%(digest_update)s
}

static int ecos_runner(void *data)
{
	struct ecos_group *g = data;

	while (!kthread_should_stop()) {
		wait_event_interruptible(g->wait, g->pending || kthread_should_stop());
		if (kthread_should_stop())
			break;
		g->pending = false;
		/* 冻结：不执行、不计数 —— 保证 sysfs 快照同拍 */
		if (unlikely(frozen))
			continue;
		ecos_run_body(g->id);
	}
	return 0;
}

static int __init ecos_init(void)
{
	int i;

	for (i = 0; i < ECOS_GROUPS; i++) {
		struct ecos_group *g = &ecos_groups[i];
		s64 start;

		init_waitqueue_head(&g->wait);
		g->pending = false;

		hrtimer_init(&g->timer, CLOCK_MONOTONIC, HRTIMER_MODE_ABS_PINNED);
		g->timer.function = ecos_tick;

		g->task = kthread_create(ecos_runner, g, "ecos_g%%d", g->id);
		if (IS_ERR(g->task)) {
			pr_err("ecos: kthread_create(%%d) failed\\n", g->id);
			return PTR_ERR(g->task);
		}
		/* 注意：sched_setscheduler_nocheck 在 5.15 **没有导出**（insmod 会报
		 * Unknown symbol），只有 sched_set_fifo / sched_setattr_nocheck 导出。
		 * 这里优先用 sched_setattr_nocheck 把优先级设成 rt_priority（默认 99，
		 * 与用户态 BM2 的 FIFO 99 对齐），失败再退回 sched_set_fifo。
		 */
		ecos_set_fifo(g->task, rt_priority);
		wake_up_process(g->task);

		/* 首次到期 = 当前时刻 + 组相位（相位是周期内的绝对偏移） */
		start = ktime_get() + ecos_phase_ns[i];
		hrtimer_start(&g->timer, start, HRTIMER_MODE_ABS_PINNED);
	}

	pr_info("ecos: %%d 组已启动，周期 %%u µs，FIFO 优先级 %%u（证据级别 %(evidence)s）\\n",
		ECOS_GROUPS, period_us, rt_priority);
	return 0;
}

static void __exit ecos_exit(void)
{
	int i;

	for (i = 0; i < ECOS_GROUPS; i++) {
		hrtimer_cancel(&ecos_groups[i].timer);
		if (ecos_groups[i].task)
			kthread_stop(ecos_groups[i].task);
	}
	pr_info("ecos: 已停止\\n");
}

module_init(ecos_init);
module_exit(ecos_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ECoS fused periodic control (%(ngroups)d groups)");
"""

_KBUILD = """# 由 ECoS 方案生成；在目标侧（内核树）执行：
#   make -C <KDIR> M=$PWD modules
obj-m := %(mod)s.o
%(mod)s-y := ecos_fused_linux.o ecos_fused_body.o stage_impl.o

# 阶段源码若是用户态 C（#include <stdint.h>），用 compat/ 补定宽整数 typedef。
# 只补 typedef，不补 libc——用到 libc 的阶段要先过 LLVM 内核化 Pass。
ccflags-y += -I$(src)/compat
"""

_README = """%(name)s —— 构建说明（必须在内核侧构建）

1) 先产阶段实现的内核化对象（复用现有 LLVM 内核化链路，不重写 POSIX 映射）：

   bash kfusion/scripts/plc_kernelize__内核化.sh <manifest>
   # 产物形如 kfusion/test/<FUSE_NAME>_kernel.o
   cp kfusion/test/<FUSE_NAME>_kernel.o stage_impl.o

2) 构建模块（需要目标侧的 KDIR，且 vermagic 必须与运行内核一致）：

   make -C /lib/modules/$(uname -r)/build M=$(pwd) modules
   sudo insmod %(mod)s.ko period_us=%(period)d
   dmesg | tail
   cat /sys/module/%(mod)s/parameters/periods
   # 取“同拍”快照：先冻结，再读，读完解冻（sysfs 分开读会跨周期）
   echo 1 | sudo tee /sys/module/%(mod)s/parameters/frozen >/dev/null
%(digest_hint)s
   echo 0 | sudo tee /sys/module/%(mod)s/parameters/frozen >/dev/null
   sudo rmmod %(mod)s

注意：
  · 本机（Windows）没有编译器/内核头，只生成了源码；不要在步骤 1/2 之外
    声称"已经构建"。
  · 阶段若含 sleep/blocking_io，生成器已拒绝进 ring-0（内核态会睡死整核）。
  · 相位由 hrtimer 的首次到期时间给出，之后按 period_us 周期前推；
    组间相位差保留在 ecos_phase_ns[] 里，与方案一致。
"""


def _ns(us):
    return "%.0f" % (float(us) * 1000.0) if us is not None else "0"


def _ident(s):
    out = []
    for ch in s:
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out)
    return s if not s[:1].isdigit() else "v_" + s


def emit(ir, module_name, phases_known=True):
    ngroups = len(ir.groups)
    layout = " | ".join(
        "%s@%sµs[%s]" % (g.gid,
                        ("%.0f" % g.phase_us) if g.phase_us is not None else "?",
                        " + ".join(s.name for s in g.stages))
        for g in ir.groups)
    body_cases = "\n".join(
        "\tcase %d:\n\t\t%s();\n\t\tbreak;" % (i, g.body)
        for i, g in enumerate(ir.groups))
    group_init = ",\n".join(
        "\t{ .id = %d }" % i for i in range(ngroups))
    phase_ns = ", ".join(_ns(g.phase_us) for g in ir.groups)

    # 观察变量：extern 声明 + 只读 sysfs 参数 + 每周期快照
    decls, updates = [], []
    for i, v in enumerate(getattr(ir, "digest_vars", []) or []):
        ident = _ident(v)
        decls.append("extern int32_t %s;" % v)
        decls.append("static int32_t ecos_obs_%d;" % i)
        decls.append('module_param_named(%s, ecos_obs_%d, int, 0444);'
                     % (ident, i))
        updates.append("\tecos_obs_%d = %s;" % (i, v))
    if not decls:
        decls = ["/* 未指定 --digest-var：没有观察点，只能证明『能加载/能卸载』 */"]

    if getattr(ir, "digest_vars", None):
        lines = ["   # 观察变量（int32_t 全局量）:"]
        for v in ir.digest_vars:
            lines.append("   cat /sys/module/%s/parameters/%s"
                         % (module_name, _ident(v)))
        digest_hint = "\n".join(lines)
    else:
        digest_hint = ("   # 编译时没给 --digest-var：没有观察点。\n"
                       "   # 此时只能证明『能加载/能卸载』，**不能**证明算得对。")

    host = _LINUX_C % {
        "name": HOST_C, "ngroups": ngroups, "nstages": len(ir.order),
        "period": int(ir.period_us or 0), "rtprio": 99, "evidence": ir.evidence,
        "layout": layout, "group_init": group_init,
        "phase_ns": phase_ns, "body_cases": body_cases,
        "digest_decls": "\n".join(decls),
        "digest_update": ("\n".join(updates) if updates
                          else "\t/* 无观察变量 */"),
    }
    return {
        HOST_C: host,
        COMPAT_H: _COMPAT_STDINT,
        KBUILD: _KBUILD % {"mod": module_name},
        README: _README % {"name": module_name, "mod": module_name,
                           "period": int(ir.period_us or 0),
                           "digest_hint": digest_hint},
    }
