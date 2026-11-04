# -*- coding: utf-8 -*-
"""单工作线程内核宿主（U-5 / PR-U7）。

一个 PLC 任务 → 一个 hrtimer + 一个 FIFO kthread + 有界释放事件队列。
多相位仍按 plan 的 phase 表绝对时间入队；同任务阶段不会被第二个本任务线程并发执行。

测量语义（审查 P0-2/P0-3 / 第二轮）：
  · response 只在末组与首组 **cycle_id 一致** 且 **全部组位图齐全** 时入账；
  · periods 只在完整周期（组掩码 == ALL）配对成功后 +1；
  · deadline_miss 用 plan.deadline_us（不是 period）；
  · late_start 需超过 late_start_slack_ns（默认 50µs）；
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
 *   · LET：kf_slots 合并组释放与逻辑读写钉扎；path=irq 时在定时器回调跑桩
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
#define KF_ALL_GROUPS_MASK ((1U << ECOS_GROUPS) - 1U)
#define KF_N_SLOTS %(nslots)d
#define KF_KIND_GROUP 0u
#define KF_KIND_LET   1u

%(let_externs)s

static unsigned int period_us = %(period)d;
module_param(period_us, uint, 0444);
MODULE_PARM_DESC(period_us, "控制周期（µs）");

static unsigned int deadline_us = %(deadline)d;
module_param(deadline_us, uint, 0444);
MODULE_PARM_DESC(deadline_us, "相对首释放的期限（µs；来自 plan.deadline_us）");

static unsigned int rt_priority = %(rtprio)d;
module_param(rt_priority, uint, 0444);
MODULE_PARM_DESC(rt_priority, "工作线程 SCHED_FIFO 优先级（来自 plan.runtime.priority）");

static int cpu = %(cpu)d;
module_param(cpu, int, 0444);
MODULE_PARM_DESC(cpu, "绑定的 CPU（-1 = 不绑；来自 plan.runtime.cpu）");

static int use_fifo = %(use_fifo)d;
module_param(use_fifo, int, 0444);
MODULE_PARM_DESC(use_fifo, "1=SCHED_FIFO（plan.runtime.sched=fifo）；0=CFS");

static unsigned int late_start_slack_ns = %(late_slack_ns)d;
module_param(late_start_slack_ns, uint, 0644);
MODULE_PARM_DESC(late_start_slack_ns, "开始晚于名义释放超过此松弛才记 late_start（ns）");

#if ECOS_GROUPS > 31
#error "single_worker groups_seen 位图最多支持 31 组"
#endif

static int ecos_set_sched(struct task_struct *t, unsigned int prio)
{
	struct sched_attr attr;
	int ret;

	memset(&attr, 0, sizeof(attr));
	attr.size = sizeof(attr);
	if (use_fifo) {
		attr.sched_policy = SCHED_FIFO;
		attr.sched_priority = prio;
	} else {
		attr.sched_policy = SCHED_NORMAL;
		attr.sched_priority = 0;
	}
	ret = sched_setattr_nocheck(t, &attr);
	if (ret) {
		if (use_fifo)
			sched_set_fifo(t);
		pr_err("ecos-sw: sched_setattr 失败 ret=%%d use_fifo=%%d\n",
		       ret, use_fifo);
	}
	return ret;
}

struct kf_release_event {
	u64 cycle_id;
	u32 kind;	/* KF_KIND_GROUP / KF_KIND_LET */
	u32 id;		/* group_id 或 let 事件下标 */
	s64 nominal_release_ns;
};

struct kf_slot {
	s64 phase_ns;
	u32 kind;
	u32 id;
	u8 irq_path;	/* LET 且 path=irq 时在 hrtimer 回调执行 */
};

/* 打开中的周期：首组登记；末组须 cycle_id 一致且 groups_seen 齐全才记账。 */
struct kf_cycle_record {
	u64 cycle_id;
	s64 release_ns;
	s64 start_ns;
	u32 groups_seen;
	bool started;
};

static const struct kf_slot kf_slots[KF_N_SLOTS] = {
%(slot_inits)s
};

%(digest_decls)s

/* completed_cycles：末组 cycle_id 配对且组掩码齐全后才 +1 */
static unsigned long periods;
module_param(periods, ulong, 0444);
MODULE_PARM_DESC(periods, "已完整完成的周期数（全部组到位且配对成功后 +1）");

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
MODULE_PARM_DESC(late_start, "开始晚于名义释放+slack 的事件数（非期限违约）");

static unsigned long deadline_miss;
module_param(deadline_miss, ulong, 0444);
MODULE_PARM_DESC(deadline_miss, "完成时刻超过名义首释放+deadline_us 的次数");

static unsigned long unpaired_finish;
module_param(unpaired_finish, ulong, 0444);
MODULE_PARM_DESC(unpaired_finish, "末组完成时与首组 cycle_id 不一致而丢弃的样本");

static unsigned long incomplete_cycle;
module_param(incomplete_cycle, ulong, 0444);
MODULE_PARM_DESC(incomplete_cycle, "新周期首组到来时上一拍尚未完整关闭的次数");

static unsigned long missing_groups;
module_param(missing_groups, ulong, 0444);
MODULE_PARM_DESC(missing_groups, "末组到达但本周期组掩码不齐全（中间阶段丢失）的次数");

static unsigned long let_rw_fires;
module_param(let_rw_fires, ulong, 0444);
MODULE_PARM_DESC(let_rw_fires, "LET 逻辑读写槽位执行次数");

static unsigned long let_rw_irq;
module_param(let_rw_irq, ulong, 0444);
MODULE_PARM_DESC(let_rw_irq, "其中在 hrtimer 回调（irq 路径）执行的次数");

static long let_rw_lat_max_ns;
module_param(let_rw_lat_max_ns, long, 0444);
MODULE_PARM_DESC(let_rw_lat_max_ns, "LET 读写实际时刻相对名义相位的最大正偏差（ns）");

static unsigned long let_skip_commit;
module_param(let_skip_commit, ulong, 0444);
MODULE_PARM_DESC(let_skip_commit, "计算未就绪时跳过的 output_commit 次数（on_timeout=skip_commit）");

static unsigned long let_hold_last;
module_param(let_hold_last, ulong, 0444);
MODULE_PARM_DESC(let_hold_last, "计算未就绪时保持上拍输出的次数（on_timeout=hold_last）");

/* 本拍计算体已完成、可供逻辑写入消费；IRQ 与 kthread 同 CPU 绑定下用 WRITE_ONCE/READ_ONCE */
static bool kf_let_ready;

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
static int kf_next_slot;

%(let_run_fn)s

static void kf_note_let_lat(s64 nominal_ns, s64 now_ns)
{
	s64 d = now_ns - nominal_ns;

	if (d > let_rw_lat_max_ns)
		let_rw_lat_max_ns = d;
}

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
	   + kf_slots[kf_next_slot].phase_ns;
	hrtimer_start(&kf_timer, ns_to_ktime(at), HRTIMER_MODE_ABS_PINNED);
}

static void kf_advance_slot(void)
{
	kf_next_slot++;
	if (kf_next_slot >= KF_N_SLOTS) {
		kf_next_slot = 0;
		kf_cycle++;
	}
}

static enum hrtimer_restart kf_tick(struct hrtimer *t)
{
	unsigned long flags;
	struct kf_release_event ev;
	const struct kf_slot *slot = &kf_slots[kf_next_slot];
	s64 expires = ktime_to_ns(hrtimer_get_expires(t));
	s64 now;

	/* LET + irq：在定时器回调直接跑受限读写桩（不得做重计算） */
	if (slot->kind == KF_KIND_LET && slot->irq_path) {
		now = ktime_get();
		let_run(slot->id);
		let_rw_fires++;
		let_rw_irq++;
		kf_note_let_lat(expires, now);
		kf_advance_slot();
%(xdom_tick_shift_single)s
		kf_arm_next();
		return HRTIMER_NORESTART;
	}

	ev.cycle_id = kf_cycle;
	ev.kind = slot->kind;
	ev.id = slot->id;
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

			/* LET 线程路径：在工作线程执行逻辑读写 */
			if (ev.kind == KF_KIND_LET) {
				s_ns = ktime_get();
				if (s_ns > ev.nominal_release_ns + (s64)late_start_slack_ns)
					late_start++;
				let_run(ev.id);
				let_rw_fires++;
				kf_note_let_lat(ev.nominal_release_ns, s_ns);
				continue;
			}

%(xdom_anchor)s%(probe_before_sw)s
			s_ns = ktime_get();
			if (s_ns > ev.nominal_release_ns + (s64)late_start_slack_ns)
				late_start++;

			if (ev.id == ECOS_FIRST_GROUP) {
				if (kf_open.started)
					incomplete_cycle++;
				kf_open.cycle_id = ev.cycle_id;
				kf_open.release_ns = ev.nominal_release_ns;
				kf_open.start_ns = s_ns;
				kf_open.groups_seen = 0;
				kf_open.started = true;
			}

			if (kf_open.started && kf_open.cycle_id == ev.cycle_id &&
			    ev.id < ECOS_GROUPS)
				kf_open.groups_seen |= (1U << ev.id);

			ecos_run_body((int)ev.id);
			/* 组体跑完 → 本拍计算就绪，供后续 LET commit 消费 */
			WRITE_ONCE(kf_let_ready, true);
			f_ns = ktime_get();

			if (ev.id == ECOS_LAST_GROUP) {
				T_ns = (s64)deadline_us * 1000LL;
				if (kf_open.started && kf_open.cycle_id == ev.cycle_id) {
					if (kf_open.groups_seen == KF_ALL_GROUPS_MASK) {
						ecos_rsf_add(kf_open.release_ns,
							     kf_open.start_ns, f_ns);
						if (f_ns > kf_open.release_ns + T_ns)
							deadline_miss++;
						periods++;
					} else {
						missing_groups++;
					}
				} else {
					unpaired_finish++;
				}
				kf_open.started = false;
				kf_open.groups_seen = 0;
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
	kf_next_slot = 0;
	let_rw_lat_max_ns = 0;
	WRITE_ONCE(kf_let_ready, false);
	memset(&kf_open, 0, sizeof(kf_open));

	kf_worker = kthread_create(kf_worker_fn, NULL, "ecos_sw");
	if (IS_ERR(kf_worker)) {
		pr_err("ecos-sw: kthread_create failed\n");
		return PTR_ERR(kf_worker);
	}
	ecos_set_sched(kf_worker, rt_priority);
	if (cpu >= 0)
		kthread_bind(kf_worker, cpu);
	wake_up_process(kf_worker);

	t0 = ktime_get();
	kf_base_ns = t0;
	{
		ktime_t first = ns_to_ktime(kf_base_ns + kf_slots[0].phase_ns);

		if (cpu >= 0) {
			struct ecos_start_arg a = { .at = first };

			smp_call_function_single(cpu, kf_start_on_cpu, &a, 1);
		} else {
			hrtimer_start(&kf_timer, first, HRTIMER_MODE_ABS_PINNED);
		}
	}

	pr_info("ecos-sw: single-worker %%d 组，周期 %%u µs，deadline %%u µs，%%s%%u，cpu %%d（证据 %(evidence)s）\n",
		ECOS_GROUPS, period_us, deadline_us,
		use_fifo ? "FIFO " : "CFS prio=",
		use_fifo ? rt_priority : 0, cpu);
	return 0;
}

static void __exit ecos_exit(void)
{
	hrtimer_cancel(&kf_timer);
	if (kf_worker)
		kthread_stop(kf_worker);
	ecos_stage_report();
	pr_info("ecos-sw: 已停止 periods=%%lu overflow=%%lu backlog_max=%%lu late=%%lu deadline_miss=%%lu unpaired=%%lu missing=%%lu let_rw=%%lu irq=%%lu skip=%%lu\n",
		periods, release_overflow, queue_backlog_max, late_start,
		deadline_miss, unpaired_finish, missing_groups,
		let_rw_fires, let_rw_irq, let_skip_commit);
}

module_init(ecos_init);
module_exit(ecos_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ECoS single-worker host (%(ngroups)d groups)");
'''


def _ns(us):
    return "%.0f" % (float(us) * 1000.0) if us is not None else "0"


def _ident(s):
    out = []
    for ch in s:
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out)
    return s if not s[:1].isdigit() else "v_" + s


def _let_ctype(name):
    """与 body_emit / stage_impl 对齐：跨阶段全局一律 int32_t（含 *_ver）。"""
    (name)  # 保留形参供日后按 TEIR 精细化
    return "int32_t"


def _let_can_stub(e):
    """有显式 publish / snap_vars 映射时可生成硬中断安全赋值桩。"""
    if e.get("kind") == "output_commit" and e.get("publish"):
        return True
    if e.get("kind") == "input_snapshot":
        snaps = e.get("snap_vars") or []
        srcs = e.get("vars") or []
        return bool(snaps) and len(snaps) == len(srcs)
    return False


def _emit_let_stub_body(e):
    """返回 (body_lines, used_symbols)。body 不含函数外壳。"""
    kind = e.get("kind")
    timeout = e.get("on_timeout") or "skip_commit"
    lines = []
    used = set()
    if kind == "output_commit":
        # 超时：计算未就绪
        lines.append("\tif (!READ_ONCE(kf_let_ready)) {")
        if timeout == "hold_last":
            lines.append("\t\tlet_hold_last++;")
            lines.append("\t\treturn;")
        elif timeout == "fault":
            lines.append("\t\tlet_skip_commit++;")
            lines.append("\t\tWRITE_ONCE(frozen, true);")
            lines.append("\t\treturn;")
        else:  # skip_commit
            lines.append("\t\tlet_skip_commit++;")
            lines.append("\t\treturn;")
        lines.append("\t}")
        for item in (e.get("publish") or []):
            if not isinstance(item, dict):
                continue
            dst = item.get("dst")
            if not dst:
                continue
            used.add(dst)
            if item.get("op") == "inc":
                lines.append("\tWRITE_ONCE(%s, READ_ONCE(%s) + 1u);" % (dst, dst))
            else:
                src = item.get("src")
                if not src:
                    continue
                used.add(src)
                lines.append("\tWRITE_ONCE(%s, READ_ONCE(%s));" % (dst, src))
        lines.append("\tWRITE_ONCE(kf_let_ready, false);")
    elif kind == "input_snapshot":
        srcs = list(e.get("vars") or [])
        snaps = list(e.get("snap_vars") or [])
        for src, dst in zip(srcs, snaps):
            used.add(src)
            used.add(dst)
            lines.append("\tWRITE_ONCE(%s, READ_ONCE(%s));" % (dst, src))
    return lines, used


def _build_slots_and_let(ir):
    """组释放 + LET 钉扎 → 统一相位槽；并生成 irq 安全桩 / let_run。"""
    events = list(getattr(ir, "let_events", None) or [])
    slots = []
    for i, g in enumerate(ir.groups):
        ph = g.phase_us if g.phase_us is not None else 0.0
        slots.append((float(ph), 0, i, 0, None))  # kind=group
    let_meta = []
    for li, e in enumerate(events):
        at = float(e.get("at_us") or 0.0)
        # irq 仅当可生成赋值桩；否则强制 thread（避免在硬中断调重计算阶段）
        want_irq = (e.get("path") or "thread") == "irq"
        irq = 1 if (want_irq and _let_can_stub(e)) else 0
        st = e.get("stage") or ("let_%d" % li)
        slots.append((at, 1, li, irq, st))
        let_meta.append(e)
    # 同时刻：组释放 → output_commit → input_snapshot
    def _key(s):
        ph, kind, idx, irq, st = s
        if kind == 0:
            return (ph, 0, idx)
        ev = let_meta[idx]
        kpri = 0 if ev.get("kind") == "output_commit" else 1
        return (ph, 1, kpri, idx)

    slots.sort(key=_key)
    inits = []
    for ph, kind, idx, irq, _st in slots:
        inits.append("\t{ .phase_ns = %s, .kind = %u, .id = %u, .irq_path = %u }"
                     % (_ns(ph), kind, idx, irq))
    if not let_meta:
        return {
            "nslots": len(slots),
            "slot_inits": ",\n".join(inits) + ("\n" if inits else ""),
            "let_externs": "/* 无 LET 事件 */\n",
            "let_run_fn": ("static void let_run(u32 id)\n{\n"
                           "\t(void)id;\n}\n"),
        }

    stage_externs = []
    var_externs = []
    stubs = []
    cases = []
    seen_fn = set()
    seen_var = set()
    irq_by_id = {}
    for ph, kind, idx, irq, _st in slots:
        if kind == 1:
            irq_by_id[idx] = irq

    for li, e in enumerate(let_meta):
        st = e.get("stage") or ("let_%d" % li)
        path = e.get("path") or "thread"
        use_stub = _let_can_stub(e)
        # irq 槽或具备映射表时走赋值桩；否则回退调阶段函数（仅 thread）
        if use_stub:
            body, used = _emit_let_stub_body(e)
            for name in sorted(used):
                if name in seen_var:
                    continue
                seen_var.add(name)
                var_externs.append("extern %s %s;" % (_let_ctype(name), name))
            stub_name = "let_stub_%u" % li
            stubs.append(
                "static void %s(void)\t/* %s @%.0fµs path=%s */\n{\n%s\n}\n"
                % (stub_name, e.get("kind"), float(e.get("at_us") or 0),
                   path, "\n".join(body) if body else "\t/* empty */")
            )
            cases.append("\tcase %u:\n\t\t%s();\n\t\tbreak;" % (li, stub_name))
        else:
            if irq_by_id.get(li):
                # 安全网：不可桩却标了 irq —— 生成空操作并注释
                cases.append(
                    "\tcase %u:\n\t\t/* path=irq 但缺 publish/snap 映射，跳过 */\n"
                    "\t\tbreak;" % li
                )
            else:
                if st not in seen_fn:
                    stage_externs.append("extern void %s(void);" % st)
                    seen_fn.add(st)
                cases.append(
                    "\tcase %u:\n\t\t%s();\t/* %s @%.0fµs path=thread */\n\t\tbreak;"
                    % (li, st, e.get("kind"), float(e.get("at_us") or 0))
                )

    let_externs = ("\n".join(var_externs + stage_externs)
                   + ("\n" if (var_externs or stage_externs) else ""))
    let_run = (
        "".join(stubs)
        + "static void let_run(u32 id)\n{\n"
        "\tswitch (id) {\n"
        + "\n".join(cases) + "\n"
        "\tdefault:\n\t\tbreak;\n"
        "\t}\n}\n"
    )
    return {
        "nslots": len(slots),
        "slot_inits": ",\n".join(inits) + ("\n" if inits else ""),
        "let_externs": let_externs,
        "let_run_fn": let_run,
    }


def render(ir, module_name, shared):
    """shared: dict of already-computed placeholders from linux_kernel_emitter.emit."""
    xdom_tick = shared.get("xdom_tick_shift_single")
    if not xdom_tick:
        xdom_tick = "\t/* 下一相位由 kf_arm_next() 按 base+cycle+phase 绝对计算 */\n"

    probe_before = shared.get("probe_before_sw", "")
    probe_after = shared.get("probe_after_sw", "")
    let_bits = _build_slots_and_let(ir)

    host = _HOST % {
        "name": "ecos_fused_linux.c",
        "ngroups": shared["ngroups"],
        "nstages": shared["nstages"],
        "first_group": shared["first_group"],
        "last_group": shared["last_group"],
        "period": shared["period"],
        "deadline": shared.get("deadline", shared["period"]),
        "rtprio": shared["rtprio"],
        "cpu": shared.get("cpu", -1),
        "use_fifo": shared.get("use_fifo", 1),
        "late_slack_ns": shared.get("late_slack_ns", 50000),
        "evidence": shared["evidence"],
        "layout": shared["layout"],
        "nslots": let_bits["nslots"],
        "slot_inits": let_bits["slot_inits"],
        "let_externs": let_bits["let_externs"],
        "let_run_fn": let_bits["let_run_fn"],
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
        "xdom_anchor": shared["xdom_anchor"].replace("g->id", "ev.id")
            if shared.get("xdom_anchor") else "",
        "xdom_shift_decls": shared["xdom_shift_decls"],
        "xdom_tick_shift_single": xdom_tick,
    }
    return host
