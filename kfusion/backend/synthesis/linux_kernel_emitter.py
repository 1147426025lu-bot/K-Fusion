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
KBUILD = "Kbuild"
README = "README.build.txt"

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

#include "ecos_fused_body.h"

#define ECOS_GROUPS %(ngroups)d

static unsigned int period_us = %(period)d;
module_param(period_us, uint, 0444);
MODULE_PARM_DESC(period_us, "控制周期（µs），默认取方案里的周期");

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
}

static int ecos_runner(void *data)
{
	struct ecos_group *g = data;

	while (!kthread_should_stop()) {
		wait_event_interruptible(g->wait, g->pending || kthread_should_stop());
		if (kthread_should_stop())
			break;
		g->pending = false;
		ecos_run_body(g->id);
	}
	return 0;
}

static int __init ecos_init(void)
{
	int i;

	for (i = 0; i < ECOS_GROUPS; i++) {
		struct ecos_group *g = &ecos_groups[i];
		struct sched_param sp = { .sched_priority = 99 };
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
		/* FIFO 99 + 绑定单核：与用户态实验口径一致（BM2 的做法） */
		sched_setscheduler_nocheck(g->task, SCHED_FIFO, &sp);
		wake_up_process(g->task);

		/* 首次到期 = 当前时刻 + 组相位（相位是周期内的绝对偏移） */
		start = ktime_get() + ecos_phase_ns[i];
		hrtimer_start(&g->timer, start, HRTIMER_MODE_ABS_PINNED);
	}

	pr_info("ecos: %%d 组已启动，周期 %%u µs（证据级别 %(evidence)s）\\n",
		ECOS_GROUPS, period_us);
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

    host = _LINUX_C % {
        "name": HOST_C, "ngroups": ngroups, "nstages": len(ir.order),
        "period": int(ir.period_us or 0), "evidence": ir.evidence,
        "layout": layout, "group_init": group_init,
        "phase_ns": phase_ns, "body_cases": body_cases,
    }
    return {
        HOST_C: host,
        KBUILD: _KBUILD % {"mod": module_name},
        README: _README % {"name": module_name, "mod": module_name,
                           "period": int(ir.period_us or 0)},
    }
