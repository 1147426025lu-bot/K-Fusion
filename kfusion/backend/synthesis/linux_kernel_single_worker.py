# -*- coding: utf-8 -*-
"""单工作线程内核宿主（U-5 / PR-U7）。

一个 PLC 任务 → 一个 hrtimer + 一个 FIFO kthread + 有界释放事件队列。
多相位仍按 plan 的 phase 表绝对时间入队；同任务阶段不会被第二个本任务线程并发执行。

测量语义（审查 P0-2/P0-3）：
  · response 只在末组与首组 **cycle_id 一致** 时入账；
  · periods 只在配对成功后 +1（完整周期）；
  · 积压用 queue_backlog_max / release_overflow，禁止 missed+=depth。

旧版「每组一线程」仍由 linux_kernel_emitter._LINUX_C 生成，作对照，不删除。
"""
from __future__ import print_function

_HOST = r'''/* %(name)s —— 单工作线程 ring-0 宿主（U-5/PR-U7）；不要手改。
 *
 * 方案：%(ngroups)d 组 / %(nstages)d 阶段，周期 %(period)s µs，证据级别 %(evidence)s
 * 组落位：%(layout)s
 *
 * 与「每组一线程」对照版的差别：
 *   · 只有 1 个 hrtimer、1 个 SCHED_FIFO 工作线程
 *   · 定时器按绝对相位表入队 kf_release_event，工作线程按序执行
 *   · 队列满记 release_overflow（不静默覆盖）
 *   · r/s/f 按 cycle_id 配对，禁止跨周期拼样本
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/hrtimer.h>
#include <linux/kthread.h>
#include <linux/wait.h>
#include <linux/ktime.h>
#include <linux/sched.h>
#include <linux/sched/rt.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/smp.h>
#include <uapi/linux/sched/types.h>
%(probe_includes)s

#include "ecos_fused_body.h"
%(xdom_include)s

#define ECOS_GROUPS %(ngroups)d
#define ECOS_NSTAGES %(nstages)d
#define ECOS_FIRST_GROUP %(first_group)d
#define ECOS_LAST_GROUP %(last_group)d
#define KF_Q_DEPTH 64

static unsigned int period_us = %(period)d;
module_param(period_us, uint, 0444);
MODULE_PARM_DESC(period_us, "控制周期（µs）");

static unsigned int rt_priority = %(rtprio)d;
module_param(rt_priority, uint, 0444);
MODULE_PARM_DESC(rt_priority, "工作线程 SCHED_FIFO 优先级");

static int cpu = -1;
module_param(cpu, int, 0444);
MODULE_PARM_DESC(cpu, "绑定的 CPU（-1 = 不绑）");

static void ecos_set_fifo(struct task_struct *t, unsigned int prio)
{
	struct sched_attr attr;

	memset(&attr, 0, sizeof(attr));
	attr.size = sizeof(attr);
	attr.sched_policy = SCHED_FIFO;
	attr.sched_priority = prio;
	if (sched_setattr_nocheck(t, &attr))
		sched_set_fifo(t);
}

struct kf_release_event {
	u64 cycle_id;
	u32 group_id;
	s64 nominal_release_ns;
};

/* 打开中的周期：首组登记，末组按 cycle_id 配对后才记账。 */
struct kf_cycle_record {
	u64 cycle_id;
	s64 release_ns;
	s64 start_ns;
	bool started;
};

static const s64 ecos_phase_ns[ECOS_GROUPS] = {
%(phase_ns)s
};

%(digest_decls)s

/* completed_cycles：末组与首组 cycle_id 配对成功后才 +1 */
static unsigned long periods;
module_param(periods, ulong, 0444);
MODULE_PARM_DESC(periods, "已完整完成的周期数（末组配对成功后 +1）");

static unsigned long queue_backlog_max;
module_param(queue_backlog_max, ulong, 0444);
MODULE_PARM_DESC(queue_backlog_max, "观测到的最大队列积压深度（事件数）");

static unsigned long release_overflow;
module_param(release_overflow, ulong, 0444);
MODULE_PARM_DESC(release_overflow, "因队列满丢弃的释放事件数（必须为 0）");

/* 兼容旧参数名 */
static unsigned long overflow;
module_param(overflow, ulong, 0444);
MODULE_PARM_DESC(overflow, "同 release_overflow（兼容旧名）");

static unsigned long late_start;
module_param(late_start, ulong, 0444);
MODULE_PARM_DESC(late_start, "开始时刻晚于名义释放的事件数");

static unsigned long deadline_miss;
module_param(deadline_miss, ulong, 0444);
MODULE_PARM_DESC(deadline_miss, "同周期完成时刻超过名义首释放+period 的次数");

static unsigned long unpaired_finish;
module_param(unpaired_finish, ulong, 0444);
MODULE_PARM_DESC(unpaired_finish, "末组完成时与首组 cycle_id 不一致而丢弃的样本");

static unsigned long incomplete_cycle;
module_param(incomplete_cycle, ulong, 0444);
MODULE_PARM_DESC(incomplete_cycle, "新周期首组到来时上一拍尚未配对完成的次数");

/* 废弃：旧 missed+=depth 会重复累计；恒为 0 */
static unsigned long missed;
module_param(missed, ulong, 0444);
MODULE_PARM_DESC(missed, "已废弃（恒 0）；看 queue_backlog_max / release_overflow / deadline_miss");

static bool frozen;
module_param(frozen, bool, 0644);
MODULE_PARM_DESC(frozen, "写 1 暂停周期体（取一致快照）");

%(probe_decls)s
%(rsf_probe)s
%(stage_profile)s
%(xdom_shift_decls)s

static struct kf_cycle_record kf_open;
static struct hrtimer kf_timer;
static struct task_struct *kf_worker;
static wait_queue_head_t kf_wait;
static DEFINE_SPINLOCK(kf_lock);
static struct kf_release_event kf_q[KF_Q_DEPTH];
static unsigned int kf_head;
static unsigned int kf_tail;
static s64 kf_base_ns;
static u64 kf_cycle;
static int kf_next_g;

static bool kf_q_empty(void)
{
	return kf_head == kf_tail;
}

static bool kf_q_full(void)
{
	return ((kf_tail + 1) %% KF_Q_DEPTH) == kf_head;
}

static unsigned int kf_q_depth(void)
{
	return (kf_tail + KF_Q_DEPTH - kf_head) %% KF_Q_DEPTH;
}

static void kf_note_depth(void)
{
	unsigned int d = kf_q_depth();

	if (d > queue_backlog_max)
		queue_backlog_max = d;
}

static void kf_arm_next(void)
{
	s64 at;

	at = kf_base_ns
	   + (s64)kf_cycle * (s64)period_us * 1000LL
	   + ecos_phase_ns[kf_next_g];
	hrtimer_start(&kf_timer, ns_to_ktime(at), HRTIMER_MODE_ABS_PINNED);
}

static void kf_advance_slot(void)
{
	kf_next_g++;
	if (kf_next_g >= ECOS_GROUPS) {
		kf_next_g = 0;
		kf_cycle++;
	}
}

static enum hrtimer_restart kf_tick(struct hrtimer *t)
{
	unsigned long flags;
	struct kf_release_event ev;
	s64 expires = ktime_to_ns(hrtimer_get_expires(t));

	ev.cycle_id = kf_cycle;
	ev.group_id = (u32)kf_next_g;
	ev.nominal_release_ns = expires;

	spin_lock_irqsave(&kf_lock, flags);
	if (kf_q_full()) {
		release_overflow++;
		overflow = release_overflow;
	} else {
		kf_q[kf_tail] = ev;
		kf_tail = (kf_tail + 1) %% KF_Q_DEPTH;
		kf_note_depth();
	}
	kf_advance_slot();
	spin_unlock_irqrestore(&kf_lock, flags);

	wake_up_interruptible(&kf_wait);

%(xdom_tick_shift_single)s
	kf_arm_next();
	return HRTIMER_NORESTART;
}

static void ecos_run_body(int id)
{
	switch (id) {
%(body_cases)s
	}
%(digest_update)s%(xdom_report)s
}

static int kf_worker_fn(void *data)
{
	(void)data;
	while (!kthread_should_stop()) {
		wait_event_interruptible(kf_wait,
					 !kf_q_empty() || kthread_should_stop());
		if (kthread_should_stop())
			break;
		for (;;) {
			struct kf_release_event ev;
			unsigned long flags;
			s64 s_ns, f_ns, T_ns;

			spin_lock_irqsave(&kf_lock, flags);
			if (kf_q_empty()) {
				spin_unlock_irqrestore(&kf_lock, flags);
				break;
			}
			ev = kf_q[kf_head];
			kf_head = (kf_head + 1) %% KF_Q_DEPTH;
			kf_note_depth();
			spin_unlock_irqrestore(&kf_lock, flags);

			if (unlikely(frozen))
				continue;
%(xdom_anchor)s%(probe_before_sw)s
			s_ns = ktime_get();
			if (s_ns > ev.nominal_release_ns)
				late_start++;

			if (ev.group_id == ECOS_FIRST_GROUP) {
				if (kf_open.started)
					incomplete_cycle++;
				kf_open.cycle_id = ev.cycle_id;
				kf_open.release_ns = ev.nominal_release_ns;
				kf_open.start_ns = s_ns;
				kf_open.started = true;
			}

			ecos_run_body((int)ev.group_id);
			f_ns = ktime_get();

			if (ev.group_id == ECOS_LAST_GROUP) {
				T_ns = (s64)period_us * 1000LL;
				if (kf_open.started && kf_open.cycle_id == ev.cycle_id) {
					ecos_rsf_add(kf_open.release_ns, kf_open.start_ns, f_ns);
					if (f_ns > kf_open.release_ns + T_ns)
						deadline_miss++;
					periods++;
				} else {
					unpaired_finish++;
				}
				kf_open.started = false;
			}
%(probe_after_sw)s
		}
	}
	return 0;
}

struct ecos_start_arg {
	ktime_t at;
};

static void kf_start_on_cpu(void *info)
{
	struct ecos_start_arg *a = info;

	hrtimer_start(&kf_timer, a->at, HRTIMER_MODE_ABS_PINNED);
}

static int __init ecos_init(void)
{
	s64 t0;
%(xdom_init)s
	if (cpu >= 0 && !cpu_online(cpu)) {
		pr_err("ecos-sw: cpu %%d 不在线\n", cpu);
		return -EINVAL;
	}

	init_waitqueue_head(&kf_wait);
	hrtimer_init(&kf_timer, CLOCK_MONOTONIC, HRTIMER_MODE_ABS_PINNED);
	kf_timer.function = kf_tick;
	kf_head = kf_tail = 0;
	kf_cycle = 0;
	kf_next_g = 0;
	memset(&kf_open, 0, sizeof(kf_open));

	kf_worker = kthread_create(kf_worker_fn, NULL, "ecos_sw");
	if (IS_ERR(kf_worker)) {
		pr_err("ecos-sw: kthread_create failed\n");
		return PTR_ERR(kf_worker);
	}
	ecos_set_fifo(kf_worker, rt_priority);
	if (cpu >= 0)
		kthread_bind(kf_worker, cpu);
	wake_up_process(kf_worker);

	t0 = ktime_get();
	kf_base_ns = t0;
	{
		ktime_t first = ns_to_ktime(kf_base_ns + ecos_phase_ns[0]);

		if (cpu >= 0) {
			struct ecos_start_arg a = { .at = first };

			smp_call_function_single(cpu, kf_start_on_cpu, &a, 1);
		} else {
			hrtimer_start(&kf_timer, first, HRTIMER_MODE_ABS_PINNED);
		}
	}

	pr_info("ecos-sw: single-worker %%d 组，周期 %%u µs，FIFO %%u，cpu %%d（证据 %(evidence)s）\n",
		ECOS_GROUPS, period_us, rt_priority, cpu);
	return 0;
}

static void __exit ecos_exit(void)
{
	hrtimer_cancel(&kf_timer);
	if (kf_worker)
		kthread_stop(kf_worker);
	ecos_stage_report();
	pr_info("ecos-sw: 已停止 periods=%%lu overflow=%%lu backlog_max=%%lu late=%%lu deadline_miss=%%lu unpaired=%%lu\n",
		periods, release_overflow, queue_backlog_max, late_start,
		deadline_miss, unpaired_finish);
}

module_init(ecos_init);
module_exit(ecos_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ECoS single-worker host (%(ngroups)d groups)");
'''


def render(ir, module_name, shared):
    """shared: dict of already-computed placeholders from linux_kernel_emitter.emit."""
    xdom_tick = shared.get("xdom_tick_shift_single")
    if not xdom_tick:
        xdom_tick = "\t/* 下一相位由 kf_arm_next() 按 base+cycle+phase 绝对计算 */\n"

    probe_before = shared.get("probe_before_sw", "")
    probe_after = shared.get("probe_after_sw", "")

    host = _HOST % {
        "name": "ecos_fused_linux.c",
        "ngroups": shared["ngroups"],
        "nstages": shared["nstages"],
        "first_group": shared["first_group"],
        "last_group": shared["last_group"],
        "period": shared["period"],
        "rtprio": shared["rtprio"],
        "evidence": shared["evidence"],
        "layout": shared["layout"],
        "phase_ns": shared["phase_ns"],
        "body_cases": shared["body_cases"],
        "digest_decls": shared["digest_decls"],
        "digest_update": shared["digest_update"],
        "probe_includes": shared["probe_includes"],
        "probe_decls": shared["probe_decls"],
        "probe_before_sw": probe_before,
        "probe_after_sw": probe_after,
        "rsf_probe": shared["rsf_probe"],
        "stage_profile": shared["stage_profile"],
        "xdom_init": shared["xdom_init"],
        "xdom_include": shared["xdom_include"],
        "xdom_report": shared["xdom_report"],
        "xdom_anchor": shared["xdom_anchor"].replace("g->id", "ev.group_id")
            if shared.get("xdom_anchor") else "",
        "xdom_shift_decls": shared["xdom_shift_decls"],
        "xdom_tick_shift_single": xdom_tick,
    }
    return host
