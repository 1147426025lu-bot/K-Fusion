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

_LET_HOST_H = r'''/* kf_let_host.h —— LET 宿主与读写实现之间的唯一接口；由生成器产出，不要手改。
 *
 * 为什么要这份头：读写实现（手写 C 组 / 契约生成 D 组）与宿主分开编译，
 * 但状态码、门禁语义、失败策略只能有一处定义，否则两组会悄悄漂移。
 * 计数（skip/stale/...）与策略全部留在宿主内，实现方拿不到也改不了。
 */
#ifndef KF_LET_HOST_H
#define KF_LET_HOST_H

#include <linux/types.h>

#define KF_LET_OP_NONE  0u
#define KF_LET_OP_WRITE 1u	/* output_commit → W,d */
#define KF_LET_OP_READ  2u	/* input_snapshot → R,r */

#define KF_LET_ST_OK    0u
#define KF_LET_ST_SKIP  1u
#define KF_LET_ST_HOLD  2u
#define KF_LET_ST_ERR   3u

/* 反查不到生产者组时退化为只按周期配对（不静默否定，也不假装是单变量实验） */
#define KF_PROD_ANY 0xffffffffu

/* 被 output_commit 依赖的生产者组；无 LET 时为 -1 */
#define KF_PROD_GID %(prod_gid)d

enum kf_stage_state {
	KF_STAGE_OK = 0,
	KF_STAGE_NOT_READY = 1,		/* 尚无任何可用结果 */
	KF_STAGE_STALE = 2,		/* 只有更早周期的结果（旧值） */
	KF_STAGE_CONSUMED = 3,		/* 本拍已发布过 */
	KF_STAGE_TOO_OLD = 4,		/* 有本拍结果但超出时效预算 */
	KF_STAGE_GROUP_MISMATCH = 5,	/* 标记来自非生产者组 */
};

#define KF_LET_POLICY_SKIP  0u
#define KF_LET_POLICY_HOLD  1u
#define KF_LET_POLICY_FAULT 2u

/* 宿主：按 (周期, 生产者组, 完成时刻) 原子取用本拍结果，age_out 返回结果年龄 */
int kf_let_gate_take(u64 cycle_id, u32 need_group, s64 *age_out);

/* 宿主：门禁未通过时按策略收尾（计数在宿主内），返回样本该记的状态码 */
int kf_let_gate_fail(int st, u32 policy);

/* 读写实现：id=事件下标，op=KF_LET_OP_*，返回 KF_LET_ST_* */
int let_run(u64 cycle_id, u32 id, u32 op, u32 *ver_out);

#endif /* KF_LET_HOST_H */
'''

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
 *   · PREEMPT_RT 上 path=irq 使用 HRTIMER_MODE_*_HARD，并在样本里记录真实上下文
 *   · 读写抖动：R/W 取操作完成后的时刻；分读/写最大 |偏差|；逐条写入环形日志
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/hrtimer.h>
#include <linux/kthread.h>
#include <linux/wait.h>
#include <linux/ktime.h>
#include <linux/delay.h>	/* 故障注入用 usleep_range */
#include <linux/sched.h>
#include <linux/sched/rt.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/smp.h>
#include <linux/vmalloc.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/hardirq.h>
#include <linux/preempt.h>
#include <uapi/linux/sched/types.h>
%(probe_includes)s

#include "ecos_fused_body.h"
#include "kf_let_host.h"
%(xdom_include)s

#define ECOS_GROUPS %(ngroups)d
#define ECOS_NSTAGES %(nstages)d
#define ECOS_FIRST_GROUP %(first_group)d
#define ECOS_LAST_GROUP %(last_group)d
#define KF_Q_DEPTH 64
#define KF_ALL_GROUPS_MASK ((1U << ECOS_GROUPS) - 1U)
#define KF_N_SLOTS %(nslots)d
/* KF_PROD_GID 在 kf_let_host.h 里（宿主与手写实现共用） */
#define KF_KIND_GROUP 0u
#define KF_KIND_LET   1u
/* KF_LET_OP_* / KF_LET_ST_* / KF_STAGE_* / 门禁接口统一在 kf_let_host.h：
 * 手写实现（C 组）与生成实现（D 组）include 同一份，状态码不会各写一套。 */
#define KF_LET_CTX_HARD 0x01u
#define KF_LET_CTX_SOFT 0x02u
#define KF_LET_CTX_TASK 0x04u
#define KF_LET_CTX_NMI  0x08u
#define KF_LET_LOG_MAGIC 0x4b464c54u	/* 'KFLT' */

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
	u8 let_op;	/* KF_LET_OP_*；仅 LET 槽有效 */
};

/* 逐次逻辑读写样本（紧凑；硬中断路径只写环形缓冲，不打印） */
struct kf_let_sample {
	u32 cycle_id;
	u8 op;
	u8 status;
	u8 ctx;
	u8 cpu;
	s64 nominal_ns;
	s64 t_before_ns;
	s64 t_after_ns;
	u32 version;
} __packed;

struct kf_let_log_hdr {
	u32 magic;
	u32 hdr_version;
	u32 sample_bytes;
	u32 n_samples;
	u32 drops;
	u32 period_us;
	u32 arm_path;	/* 0=thread 1=irq(HARD) */
	u32 reserved;
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
MODULE_PARM_DESC(let_rw_fires, "LET 逻辑读写槽位执行次数（含 skip）");

static unsigned long let_rw_irq;
module_param(let_rw_irq, ulong, 0444);
MODULE_PARM_DESC(let_rw_irq, "在 hrtimer 回调路径执行的次数（是否硬中断看 let_ctx_*）");

static long let_rw_lat_max_ns;
module_param(let_rw_lat_max_ns, long, 0444);
MODULE_PARM_DESC(let_rw_lat_max_ns, "兼容旧名：max(|读偏差|,|写偏差|)（ns）；看 let_rd/wr_jit_max_ns");

static long let_rd_jit_max_ns;
module_param(let_rd_jit_max_ns, long, 0444);
MODULE_PARM_DESC(let_rd_jit_max_ns, "有效读：max|R-r|（ns）；R=快照完成后时刻");

static long let_wr_jit_max_ns;
module_param(let_wr_jit_max_ns, long, 0444);
MODULE_PARM_DESC(let_wr_jit_max_ns, "有效写：max|W-d|（ns）；W=发布完成后时刻");

static long let_rd_late_max_ns;
module_param(let_rd_late_max_ns, long, 0444);
MODULE_PARM_DESC(let_rd_late_max_ns, "有效读：最大正偏差 max(R-r)（ns，晚于计划）");

static long let_rd_early_max_ns;
module_param(let_rd_early_max_ns, long, 0444);
MODULE_PARM_DESC(let_rd_early_max_ns, "有效读：最大提前量 max(r-R)（ns）");

static long let_wr_late_max_ns;
module_param(let_wr_late_max_ns, long, 0444);
MODULE_PARM_DESC(let_wr_late_max_ns, "有效写：最大正偏差 max(W-d)（ns，晚于计划）");

static long let_wr_early_max_ns;
module_param(let_wr_early_max_ns, long, 0444);
MODULE_PARM_DESC(let_wr_early_max_ns, "有效写：最大提前量 max(d-W)（ns）");

static long let_op_dur_max_ns;
module_param(let_op_dur_max_ns, long, 0444);
MODULE_PARM_DESC(let_op_dur_max_ns, "单次逻辑读写桩耗时 max(t_after-t_before)（ns）");

static unsigned long let_rd_ok;
module_param(let_rd_ok, ulong, 0444);
MODULE_PARM_DESC(let_rd_ok, "状态=ok 的 input_snapshot 次数");

static unsigned long let_wr_ok;
module_param(let_wr_ok, ulong, 0444);
MODULE_PARM_DESC(let_wr_ok, "状态=ok 的 output_commit 次数");

static unsigned long let_skip_commit;
module_param(let_skip_commit, ulong, 0444);
MODULE_PARM_DESC(let_skip_commit, "计算未就绪时跳过的 output_commit 次数（on_timeout=skip_commit）");

static unsigned long let_hold_last;
module_param(let_hold_last, ulong, 0444);
MODULE_PARM_DESC(let_hold_last, "计算未就绪时保持上拍输出的次数（on_timeout=hold_last）");

static unsigned long let_ctx_hardirq;
module_param(let_ctx_hardirq, ulong, 0444);
MODULE_PARM_DESC(let_ctx_hardirq, "LET 执行时 in_hardirq() 为真的次数");

static unsigned long let_ctx_softirq;
module_param(let_ctx_softirq, ulong, 0444);
MODULE_PARM_DESC(let_ctx_softirq, "LET 执行时 in_serving_softirq() 为真的次数");

static unsigned long let_ctx_task;
module_param(let_ctx_task, ulong, 0444);
MODULE_PARM_DESC(let_ctx_task, "LET 执行时在进程上下文的次数");

static unsigned long let_log_drops;
module_param(let_log_drops, ulong, 0444);
MODULE_PARM_DESC(let_log_drops, "环形日志满而丢弃的样本数（抖动最大值仍更新）");

static unsigned int let_log_cap = %(let_log_cap)d;
module_param(let_log_cap, uint, 0444);
MODULE_PARM_DESC(let_log_cap, "环形日志容量（样本数）；0=不记逐条");

static int hrtimer_hard = %(hrtimer_hard)d;
module_param(hrtimer_hard, int, 0444);
MODULE_PARM_DESC(hrtimer_hard, "1=HRTIMER_MODE_*_HARD（PREEMPT_RT 硬中断）；0=默认（RT 上多为 softirq）");

/* ---- 计算就绪标记：三元组（生产者组、周期号、完成时刻） ----
 * 旧实现是单个 bool：任意组体跑完就置真，output_commit 无法判断「谁算的、哪一拍、
 * 算完多久」，会把上一拍遗留或别的组的结果当成本拍结果发布。现改为逐拍三元组，
 * 且 commit 只能原子地取用一次（consumed），重复提交单独计数。
 */
struct kf_stage_mark {
	u64 cycle_id;	/* 产出该结果的周期 */
	s64 done_ns;	/* 该结果算完的时刻 */
	u32 group_id;	/* 产出该结果的组，必须等于 commit 依赖的生产者 */
	u32 valid;
	u32 consumed;	/* 本拍结果已被 commit 取用，禁止重复发布 */
};

static struct kf_stage_mark kf_mark;

static unsigned long let_ready_not_ready;
module_param(let_ready_not_ready, ulong, 0444);
MODULE_PARM_DESC(let_ready_not_ready, "commit 时无任何可用结果的次数");

static unsigned long let_ready_stale;
module_param(let_ready_stale, ulong, 0444);
MODULE_PARM_DESC(let_ready_stale, "commit 只看到更早周期结果的次数（旧值风险）");

static unsigned long let_ready_consumed;
module_param(let_ready_consumed, ulong, 0444);
MODULE_PARM_DESC(let_ready_consumed, "同一拍重复 commit 的次数（必须为 0）");

static unsigned long let_ready_too_old;
module_param(let_ready_too_old, ulong, 0444);
MODULE_PARM_DESC(let_ready_too_old, "结果年龄超出时效预算的次数");

static unsigned long let_ready_group_mismatch;
module_param(let_ready_group_mismatch, ulong, 0444);
MODULE_PARM_DESC(let_ready_group_mismatch, "就绪标记来自非生产者组的次数（必须为 0）");

static long let_ready_age_max_ns;
module_param(let_ready_age_max_ns, long, 0444);
MODULE_PARM_DESC(let_ready_age_max_ns, "commit 时结果的最大年龄 now-done_ns（ns）");

static int let_result_age_budget_ns = %(age_budget_ns)d;
module_param(let_result_age_budget_ns, int, 0444);
MODULE_PARM_DESC(let_result_age_budget_ns, "结果时效预算（ns）；0=只查周期不查年龄");

/* 故障注入（仅正确性用例；默认全 0，不影响测量口径） */
static unsigned int test_stall_prod_us;
module_param(test_stall_prod_us, uint, 0644);
MODULE_PARM_DESC(test_stall_prod_us, "故障注入：生产者组体前睡眠 N µs（默认 0）");

static unsigned int test_skip_prod_left;
module_param(test_skip_prod_left, uint, 0644);
MODULE_PARM_DESC(test_skip_prod_left, "故障注入：跳过接下来 N 拍生产者组体（默认 0）");

static unsigned long test_prod_skipped;
module_param(test_prod_skipped, ulong, 0444);
MODULE_PARM_DESC(test_prod_skipped, "被故障注入跳过的生产者组体次数");

static struct kf_let_sample *kf_let_log;
static unsigned int kf_let_log_n;
static enum hrtimer_mode kf_timer_mode;

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
/* 队列锁与就绪标记锁都必须是 raw_spinlock_t：hrtimer_hard=1 时整只定时器在硬中断
 * 上下文触发（含 group 释放槽），PREEMPT_RT 上 spinlock_t 是可睡眠的 rtmutex，
 * 在硬中断里取它属于非法路径。临界区只有几个字，raw 不会带来可测的额外延迟。
 */
static DEFINE_RAW_SPINLOCK(kf_lock);
static DEFINE_RAW_SPINLOCK(kf_mark_lock);
static struct kf_release_event kf_q[KF_Q_DEPTH];
static unsigned int kf_head;
static unsigned int kf_tail;
static s64 kf_base_ns;
static u64 kf_cycle;
static int kf_next_slot;

/* 生产者组体跑完后登记三元组；只有登记过的结果才可能被 commit 取用 */
static void kf_mark_stage(u64 cycle_id, u32 group_id, s64 done_ns)
{
	unsigned long flags;

	raw_spin_lock_irqsave(&kf_mark_lock, flags);
	kf_mark.cycle_id = cycle_id;
	kf_mark.done_ns = done_ns;
	kf_mark.group_id = group_id;
	kf_mark.valid = 1;
	kf_mark.consumed = 0;
	raw_spin_unlock_irqrestore(&kf_mark_lock, flags);
}

/* 取用本拍结果：周期一致 + 生产者一致 + 未被取用 + 未超龄 才放行。
 * 取用是原子的：同一拍第二次 commit 会得到 KF_STAGE_CONSUMED。
 */
static int kf_stage_take(u64 cycle_id, u32 need_group, s64 now,
			 s64 budget_ns, s64 *age_out)
{
	unsigned long flags;
	int st = KF_STAGE_OK;

	*age_out = 0;
	raw_spin_lock_irqsave(&kf_mark_lock, flags);
	if (!kf_mark.valid) {
		st = KF_STAGE_NOT_READY;
	} else if (kf_mark.cycle_id < cycle_id) {
		st = KF_STAGE_STALE;
		*age_out = now - kf_mark.done_ns;
	} else if (kf_mark.cycle_id > cycle_id) {
		/* 结果来自未来拍：不可能发生，按未就绪 fail-closed 而不是放行 */
		st = KF_STAGE_NOT_READY;
	} else if (kf_mark.group_id != need_group) {
		st = (kf_mark.group_id > need_group) ? KF_STAGE_GROUP_MISMATCH
						     : KF_STAGE_NOT_READY;
	} else if (kf_mark.consumed) {
		st = KF_STAGE_CONSUMED;
	} else {
		*age_out = now - kf_mark.done_ns;
		if (budget_ns > 0 && *age_out > budget_ns) {
			st = KF_STAGE_TOO_OLD;
		} else {
			kf_mark.consumed = 1;
			st = KF_STAGE_OK;
		}
	}
	raw_spin_unlock_irqrestore(&kf_mark_lock, flags);
	return st;
}

static void kf_stage_account(int st)
{
	switch (st) {
	case KF_STAGE_STALE:
		let_ready_stale++;
		break;
	case KF_STAGE_CONSUMED:
		let_ready_consumed++;
		break;
	case KF_STAGE_TOO_OLD:
		let_ready_too_old++;
		break;
	case KF_STAGE_GROUP_MISMATCH:
		let_ready_group_mismatch++;
		break;
	case KF_STAGE_NOT_READY:
	default:
		let_ready_not_ready++;
		break;
	}
}

/* ---- 对读写实现的接口（kf_let_host.h 声明）；策略与计数都留在宿主里，
 * 手写实现与生成实现只负责「到点真正读写哪些量」，改不了门禁口径。 ---- */
int kf_let_gate_take(u64 cycle_id, u32 need_group, s64 *age_out)
{
	int st = kf_stage_take(cycle_id, need_group, ktime_get(),
			       (s64)let_result_age_budget_ns, age_out);

	if (*age_out > let_ready_age_max_ns)
		let_ready_age_max_ns = *age_out;
	return st;
}

int kf_let_gate_fail(int st, u32 policy)
{
	kf_stage_account(st);
	if (policy == KF_LET_POLICY_HOLD) {
		let_hold_last++;
		return KF_LET_ST_HOLD;
	}
	if (policy == KF_LET_POLICY_FAULT) {
		let_skip_commit++;
		WRITE_ONCE(frozen, true);
		return KF_LET_ST_ERR;
	}
	let_skip_commit++;
	return KF_LET_ST_SKIP;
}

%(let_run_decl)s

static u8 kf_let_ctx_flags(void)
{
	u8 c = 0;

	if (in_nmi())
		c |= KF_LET_CTX_NMI;
	if (in_hardirq())
		c |= KF_LET_CTX_HARD;
	if (in_serving_softirq())
		c |= KF_LET_CTX_SOFT;
	if (!in_interrupt())
		c |= KF_LET_CTX_TASK;
	return c;
}

static void kf_let_note_abs(s64 nominal_ns, s64 done_ns, u8 op, u8 status)
{
	s64 d, a;

	if (status != KF_LET_ST_OK)
		return;
	d = done_ns - nominal_ns;
	a = (d < 0) ? -d : d;
	if (op == KF_LET_OP_READ) {
		let_rd_ok++;
		if (a > let_rd_jit_max_ns)
			let_rd_jit_max_ns = a;
		if (d > let_rd_late_max_ns)
			let_rd_late_max_ns = d;
		if (-d > let_rd_early_max_ns)
			let_rd_early_max_ns = -d;
	} else if (op == KF_LET_OP_WRITE) {
		let_wr_ok++;
		if (a > let_wr_jit_max_ns)
			let_wr_jit_max_ns = a;
		if (d > let_wr_late_max_ns)
			let_wr_late_max_ns = d;
		if (-d > let_wr_early_max_ns)
			let_wr_early_max_ns = -d;
	}
	if (a > let_rw_lat_max_ns)
		let_rw_lat_max_ns = a;
}

static void kf_let_log_push(const struct kf_let_sample *s)
{
	unsigned int i;

	if (!kf_let_log || !let_log_cap)
		return;
	i = kf_let_log_n;
	if (i >= let_log_cap) {
		let_log_drops++;
		return;
	}
	kf_let_log[i] = *s;
	/* 单生产者（绑核定时器/工人）；完成后可见 */
	smp_wmb();
	kf_let_log_n = i + 1;
}

static void kf_let_exec(u64 cycle_id, u32 let_id, u8 let_op, u8 irq_path,
			s64 nominal_ns)
{
	struct kf_let_sample s;
	u32 ver = 0;
	int st;
	u8 ctx;

	s.cycle_id = (u32)cycle_id;
	s.op = let_op;
	s.cpu = (u8)smp_processor_id();
	s.nominal_ns = nominal_ns;
	s.t_before_ns = ktime_get();
	st = let_run(cycle_id, let_id, let_op, &ver);
	s.t_after_ns = ktime_get();
	ctx = kf_let_ctx_flags();
	s.ctx = ctx;
	s.status = (st < 0) ? KF_LET_ST_ERR : (u8)st;
	s.version = ver;

	{
		s64 dur = s.t_after_ns - s.t_before_ns;

		if (dur > let_op_dur_max_ns)
			let_op_dur_max_ns = dur;
	}

	if (ctx & KF_LET_CTX_HARD)
		let_ctx_hardirq++;
	else if (ctx & KF_LET_CTX_SOFT)
		let_ctx_softirq++;
	else
		let_ctx_task++;

	let_rw_fires++;
	if (irq_path)
		let_rw_irq++;

	kf_let_note_abs(nominal_ns, s.t_after_ns, let_op, s.status);
	kf_let_log_push(&s);
}

static ssize_t kf_let_log_read(struct file *f, char __user *buf, size_t len,
			       loff_t *ppos)
{
	struct kf_let_log_hdr hdr;
	size_t total, off;
	unsigned int n;

	n = READ_ONCE(kf_let_log_n);
	smp_rmb();
	memset(&hdr, 0, sizeof(hdr));
	hdr.magic = KF_LET_LOG_MAGIC;
	hdr.hdr_version = 1;
	hdr.sample_bytes = (u32)sizeof(struct kf_let_sample);
	hdr.n_samples = n;
	hdr.drops = (u32)let_log_drops;
	hdr.period_us = period_us;
	hdr.arm_path = hrtimer_hard ? 1u : 0u;
	total = sizeof(hdr) + (size_t)n * sizeof(struct kf_let_sample);
	if (*ppos >= total)
		return 0;
	if (len > total - *ppos)
		len = total - *ppos;
	off = (size_t)*ppos;
	if (off < sizeof(hdr)) {
		size_t chunk = sizeof(hdr) - off;

		if (chunk > len)
			chunk = len;
		if (copy_to_user(buf, (char *)&hdr + off, chunk))
			return -EFAULT;
		*ppos += chunk;
		return chunk;
	}
	off -= sizeof(hdr);
	if (!kf_let_log)
		return -ENOENT;
	if (copy_to_user(buf, (char *)kf_let_log + off, len))
		return -EFAULT;
	*ppos += len;
	return len;
}

static const struct file_operations kf_let_log_fops = {
	.owner = THIS_MODULE,
	.read = kf_let_log_read,
	.llseek = default_llseek,
};

static struct miscdevice kf_let_log_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "kf_let_log",
	.fops = &kf_let_log_fops,
	.mode = 0444,
};

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
	hrtimer_start(&kf_timer, ns_to_ktime(at), kf_timer_mode);
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

	/* LET + irq：在定时器回调直接跑受限读写桩（不得做重计算） */
	if (slot->kind == KF_KIND_LET && slot->irq_path) {
		kf_let_exec(kf_cycle, slot->id, slot->let_op, 1, expires);
		kf_advance_slot();
%(xdom_tick_shift_single)s
		kf_arm_next();
		return HRTIMER_NORESTART;
	}

	ev.cycle_id = kf_cycle;
	ev.kind = slot->kind;
	ev.id = slot->id;
	ev.nominal_release_ns = expires;

	raw_spin_lock_irqsave(&kf_lock, flags);
	if (kf_q_full()) {
		release_overflow++;
		overflow = release_overflow;
	} else {
		kf_q[kf_tail] = ev;
		kf_tail = (kf_tail + 1) %% KF_Q_DEPTH;
		kf_note_depth();
	}
	kf_advance_slot();
	raw_spin_unlock_irqrestore(&kf_lock, flags);

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

			raw_spin_lock_irqsave(&kf_lock, flags);
			if (kf_q_empty()) {
				raw_spin_unlock_irqrestore(&kf_lock, flags);
				break;
			}
			ev = kf_q[kf_head];
			kf_head = (kf_head + 1) %% KF_Q_DEPTH;
			kf_note_depth();
			raw_spin_unlock_irqrestore(&kf_lock, flags);

			if (unlikely(frozen))
				continue;

			/* LET 线程路径：在工作线程执行逻辑读写 */
			if (ev.kind == KF_KIND_LET) {
				s_ns = ktime_get();
				if (s_ns > ev.nominal_release_ns + (s64)late_start_slack_ns)
					late_start++;
				{
					u8 op = KF_LET_OP_NONE;
					unsigned int si;

					for (si = 0; si < KF_N_SLOTS; si++) {
						if (kf_slots[si].kind == KF_KIND_LET &&
						    kf_slots[si].id == ev.id) {
							op = kf_slots[si].let_op;
							break;
						}
					}
					kf_let_exec(ev.cycle_id, ev.id, op, 0,
						    ev.nominal_release_ns);
				}
				continue;
			}

			/* 故障注入：只作用于生产者组，默认全 0 时行为与未注入一致。
			 * skip 放在组记账之前，跳过的拍会体现为 missing_groups 而不是"假装完成"。
			 */
			if (ev.id == KF_PROD_GID) {
				if (READ_ONCE(test_skip_prod_left)) {
					WRITE_ONCE(test_skip_prod_left,
						   READ_ONCE(test_skip_prod_left) - 1);
					test_prod_skipped++;
					continue;	/* 不跑生产者组体、不登记结果 */
				}
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

			/* 故障注入：让生产者算得比逻辑写入时刻更晚，用于超时/旧值用例 */
			if (ev.id == KF_PROD_GID && READ_ONCE(test_stall_prod_us)) {
				unsigned int us = READ_ONCE(test_stall_prod_us);

				usleep_range(us, us + 200);
			}

			ecos_run_body((int)ev.id);
			f_ns = ktime_get();
			/* 组体跑完 → 登记三元组（周期、组号、完成时刻），供 commit 配对取用 */
			kf_mark_stage(ev.cycle_id, ev.id, f_ns);

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

	hrtimer_start(&kf_timer, a->at, kf_timer_mode);
}

static int __init ecos_init(void)
{
	s64 t0;
	int ret;
%(xdom_init)s
	if (cpu >= 0 && !cpu_online(cpu)) {
		pr_err("ecos-sw: cpu %%d 不在线\n", cpu);
		return -EINVAL;
	}

	kf_timer_mode = hrtimer_hard ? HRTIMER_MODE_ABS_PINNED_HARD
				     : HRTIMER_MODE_ABS_PINNED;

	if (let_log_cap) {
		kf_let_log = vzalloc((unsigned long)let_log_cap *
				     sizeof(struct kf_let_sample));
		if (!kf_let_log) {
			pr_err("ecos-sw: let_log 分配失败 cap=%%u\n", let_log_cap);
			return -ENOMEM;
		}
		kf_let_log_n = 0;
		ret = misc_register(&kf_let_log_dev);
		if (ret) {
			vfree(kf_let_log);
			kf_let_log = NULL;
			pr_err("ecos-sw: misc_register kf_let_log 失败 %%d\n", ret);
			return ret;
		}
	}

	init_waitqueue_head(&kf_wait);
	hrtimer_init(&kf_timer, CLOCK_MONOTONIC, kf_timer_mode);
	kf_timer.function = kf_tick;
	kf_head = kf_tail = 0;
	kf_cycle = 0;
	kf_next_slot = 0;
	let_rw_lat_max_ns = 0;
	let_rd_jit_max_ns = 0;
	let_wr_jit_max_ns = 0;
	let_rd_late_max_ns = 0;
	let_rd_early_max_ns = 0;
	let_wr_late_max_ns = 0;
	let_wr_early_max_ns = 0;
	let_op_dur_max_ns = 0;
	memset(&kf_mark, 0, sizeof(kf_mark));
	let_ready_not_ready = 0;
	let_ready_stale = 0;
	let_ready_consumed = 0;
	let_ready_too_old = 0;
	let_ready_group_mismatch = 0;
	let_ready_age_max_ns = 0;
	test_prod_skipped = 0;
	memset(&kf_open, 0, sizeof(kf_open));

	kf_worker = kthread_create(kf_worker_fn, NULL, "ecos_sw");
	if (IS_ERR(kf_worker)) {
		pr_err("ecos-sw: kthread_create failed\n");
		if (kf_let_log) {
			misc_deregister(&kf_let_log_dev);
			vfree(kf_let_log);
			kf_let_log = NULL;
		}
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
			hrtimer_start(&kf_timer, first, kf_timer_mode);
		}
	}

	pr_info("ecos-sw: single-worker %%d 组，周期 %%u µs，deadline %%u µs，%%s%%u，cpu %%d，hrtimer_hard=%%d，let_log_cap=%%u（证据 %(evidence)s）\n",
		ECOS_GROUPS, period_us, deadline_us,
		use_fifo ? "FIFO " : "CFS prio=",
		use_fifo ? rt_priority : 0, cpu, hrtimer_hard, let_log_cap);
	return 0;
}

static void __exit ecos_exit(void)
{
	hrtimer_cancel(&kf_timer);
	if (kf_worker)
		kthread_stop(kf_worker);
	ecos_stage_report();
	pr_info("ecos-sw: 已停止 periods=%%lu rd_jit=%%ld wr_jit=%%ld ctx_h/s/t=%%lu/%%lu/%%lu let_rw=%%lu irq=%%lu skip=%%lu drops=%%lu log_n=%%u\n",
		periods, let_rd_jit_max_ns, let_wr_jit_max_ns,
		let_ctx_hardirq, let_ctx_softirq, let_ctx_task,
		let_rw_fires, let_rw_irq, let_skip_commit, let_log_drops,
		kf_let_log_n);
	pr_info("ecos-sw: 就绪门禁 stale=%%lu not_ready=%%lu consumed=%%lu too_old=%%lu group_mismatch=%%lu age_max=%%ldns\n",
		let_ready_stale, let_ready_not_ready, let_ready_consumed,
		let_ready_too_old, let_ready_group_mismatch, let_ready_age_max_ns);
	if (kf_let_log) {
		misc_deregister(&kf_let_log_dev);
		vfree(kf_let_log);
		kf_let_log = NULL;
	}
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


def _let_version_sym(e):
    """样本 version 字段：写取发布版本，读取快照版本（若有）。"""
    if e.get("kind") == "output_commit":
        for item in (e.get("publish") or []):
            if isinstance(item, dict) and item.get("op") == "inc" and item.get("dst"):
                return item["dst"]
        for v in (e.get("vars") or []):
            if str(v).endswith("_ver") or str(v).endswith("_version"):
                return v
        vs = e.get("vars") or []
        return vs[-1] if vs else None
    if e.get("kind") == "input_snapshot":
        snaps = e.get("snap_vars") or []
        for v in snaps:
            if str(v).endswith("_ver") or str(v).endswith("_version"):
                return v
        return snaps[-1] if snaps else None
    return None


def _emit_let_stub_body(e, prod_gid=-1):
    """返回 (body_lines, used_symbols)。body 不含函数外壳；返回 int 状态码。

    prod_gid 是 output_commit 依赖的生产者组号（由 compute_at_us 反查）。
    提交前必须用 (周期, 组号, 完成时刻) 三元组配对，不能只看单个 bool。
    """
    kind = e.get("kind")
    timeout = e.get("on_timeout") or "skip_commit"
    lines = []
    used = set()
    ver = _let_version_sym(e)
    if kind == "output_commit":
        need = ("%du" % prod_gid) if prod_gid is not None and prod_gid >= 0 \
            else "KF_PROD_ANY"
        pol = {"hold_last": "KF_LET_POLICY_HOLD",
               "fault": "KF_LET_POLICY_FAULT"}.get(timeout, "KF_LET_POLICY_SKIP")
        lines.append("\t{")
        lines.append("\t\ts64 age_ns = 0;")
        lines.append("\t\tint st = kf_let_gate_take(cycle_id, %s, &age_ns);" % need)
        lines.append("")
        lines.append("\t\t(void)age_ns;")
        lines.append("\t\tif (st != KF_STAGE_OK) {")
        if ver:
            used.add(ver)
            lines.append("\t\t\tif (ver_out)\n\t\t\t\t*ver_out = READ_ONCE(%s);" % ver)
        lines.append("\t\t\treturn kf_let_gate_fail(st, %s);" % pol)
        lines.append("\t\t}")
        for item in (e.get("publish") or []):
            if not isinstance(item, dict):
                continue
            dst = item.get("dst")
            if not dst:
                continue
            used.add(dst)
            if item.get("op") == "inc":
                lines.append("\t\tWRITE_ONCE(%s, READ_ONCE(%s) + 1u);" % (dst, dst))
            else:
                src = item.get("src")
                if not src:
                    continue
                used.add(src)
                lines.append("\t\tWRITE_ONCE(%s, READ_ONCE(%s));" % (dst, src))
        if ver:
            used.add(ver)
            lines.append("\t\tif (ver_out)\n\t\t\t*ver_out = READ_ONCE(%s);" % ver)
        lines.append("\t\treturn KF_LET_ST_OK;")
        lines.append("\t}")
    elif kind == "input_snapshot":
        srcs = list(e.get("vars") or [])
        snaps = list(e.get("snap_vars") or [])
        for src, dst in zip(srcs, snaps):
            used.add(src)
            used.add(dst)
            lines.append("\tWRITE_ONCE(%s, READ_ONCE(%s));" % (dst, src))
        if ver:
            used.add(ver)
            lines.append("\tif (ver_out)\n\t\t*ver_out = READ_ONCE(%s);" % ver)
        lines.append("\treturn KF_LET_ST_OK;")
    else:
        lines.append("\t(void)ver_out;")
        lines.append("\treturn KF_LET_ST_ERR;")
    return lines, used


def _producer_gid(e, gphase):
    """output_commit 依赖的生产者组号。

    优先用 compute_at_us（阶段原本被计算时所在的组头）；缺该字段时退化为
    「相位严格早于 at_us 的最后一组」。反查不到返回 -1（生成端退化为只按周期配对）。
    """
    at = e.get("compute_at_us")
    if at is not None:
        for gid, ph in gphase.items():
            if abs(ph - float(at)) < 1e-6:
                return gid
        at_us = float(at)
    else:
        at_us = float(e.get("at_us") or 0)
    cands = [gid for gid, ph in gphase.items() if ph < at_us - 1e-6]
    return max(cands) if cands else -1


def _build_slots_and_let(ir, seam=False):
    """组释放 + LET 钉扎 → 统一相位槽；并生成 irq 安全桩 / let_run。"""
    events = list(getattr(ir, "let_events", None) or [])
    slots = []
    gphase = {}
    for i, g in enumerate(ir.groups):
        ph = g.phase_us if g.phase_us is not None else 0.0
        gphase[i] = float(ph)
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
    any_irq = False
    for ph, kind, idx, irq, _st in slots:
        let_op = 0
        if kind == 1:
            any_irq = any_irq or bool(irq)
            ek = let_meta[idx].get("kind")
            if ek == "output_commit":
                let_op = 1  # KF_LET_OP_WRITE
            elif ek == "input_snapshot":
                let_op = 2  # KF_LET_OP_READ
        inits.append(
            "\t{ .phase_ns = %s, .kind = %u, .id = %u, .irq_path = %u, .let_op = %u }"
            % (_ns(ph), kind, idx, irq, let_op))
    if not let_meta:
        empty = ("int let_run(u64 cycle_id, u32 id, u32 op, u32 *ver_out)\n{\n"
                 "\t(void)cycle_id;\n\t(void)id;\n\t(void)op;\n"
                 "\tif (ver_out)\n\t\t*ver_out = 0;\n"
                 "\treturn KF_LET_ST_OK;\n}\n")
        return {
            "nslots": len(slots),
            "slot_inits": ",\n".join(inits) + ("\n" if inits else ""),
            "let_externs": "/* 无 LET 事件 */\n",
            "let_run_decl": ("/* let_run 见 let_impl.c */\n" if seam else
                             empty.replace("int let_run", "static int let_run", 1)),
            "let_impl_c": ("let_impl.c",
                           '/* 无 LET 事件 */\n#include "kf_let_host.h"\n\n' + empty)
            if seam else None,
            "hrtimer_hard": 0,
            "any_irq": 0,
            "prod_gid": -1,
        }

    stage_externs = []
    var_externs = []
    prod_gid = -1
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
        pg = _producer_gid(e, gphase) if e.get("kind") == "output_commit" else -1
        if pg >= 0 and prod_gid < 0:
            prod_gid = pg
        if use_stub:
            body, used = _emit_let_stub_body(e, pg)
            for name in sorted(used):
                if name in seen_var:
                    continue
                seen_var.add(name)
                var_externs.append("extern %s %s;" % (_let_ctype(name), name))
            stub_name = "let_stub_%u" % li
            stubs.append(
                "static int %s(u64 cycle_id, u32 *ver_out)\t/* %s @%.0fµs path=%s */\n{\n%s\n}\n"
                % (stub_name, e.get("kind"), float(e.get("at_us") or 0),
                   path, "\n".join(body) if body else
                   "\t(void)cycle_id;\n\t(void)ver_out;\n\treturn KF_LET_ST_OK;")
            )
            cases.append("\tcase %u:\n\t\treturn %s(cycle_id, ver_out);" % (li, stub_name))
        else:
            if irq_by_id.get(li):
                cases.append(
                    "\tcase %u:\n\t\t(void)ver_out;\n"
                    "\t\treturn KF_LET_ST_ERR;\t/* path=irq 缺映射 */\n" % li
                )
            else:
                if st not in seen_fn:
                    stage_externs.append("extern void %s(void);" % st)
                    seen_fn.add(st)
                cases.append(
                    "\tcase %u:\n\t\t%s();\t/* %s @%.0fµs path=thread */\n"
                    "\t\tif (ver_out)\n\t\t\t*ver_out = 0;\n"
                    "\t\treturn KF_LET_ST_OK;"
                    % (li, st, e.get("kind"), float(e.get("at_us") or 0))
                )

    let_externs = ("\n".join(var_externs + stage_externs)
                   + ("\n" if (var_externs or stage_externs) else ""))
    run_body = ("\tswitch (id) {\n"
                + "\n".join(cases) + "\n"
                "\tdefault:\n\t\tif (ver_out)\n\t\t\t*ver_out = 0;\n"
                "\t\treturn KF_LET_ST_ERR;\n"
                "\t}\n}\n")
    sig = "int let_run(u64 cycle_id, u32 id, u32 op, u32 *ver_out)\n{\n\t(void)op;\n"
    let_run = ("".join(stubs) + "static " + sig + run_body) if not seam else None
    let_impl = None
    if seam:
        let_impl = (
            "let_impl.c",
            "/* let_impl.c —— D 组读写实现：由契约的 publish / snap_vars 生成。\n"
            " * C 组把本文件换成 let_impl_hand.c；宿主、事件表、计时点、缓冲区、门禁全同。\n"
            " * 不要手改：要改读写语义就改契约或手写实现，并同步跑 test_let_ready_gate。\n"
            " */\n"
            "#include <linux/module.h>\n"
            "#include <linux/kernel.h>\n"
            "#include <linux/compiler.h>\n"
            '#include "kf_let_host.h"\n\n'
            + let_externs + "\n"
            + "".join(stubs)
            + sig + run_body)
    return {
        "nslots": len(slots),
        "slot_inits": ",\n".join(inits) + ("\n" if inits else ""),
        "let_externs": let_externs,
        "let_run_decl": ("/* let_run 由 let_impl.c 提供（接口见 kf_let_host.h） */\n"
                         if seam else let_run),
        "let_impl_c": let_impl,
        "hrtimer_hard": 1 if any_irq else 0,
        "any_irq": 1 if any_irq else 0,
        "prod_gid": prod_gid,
    }


def render(ir, module_name, shared, out_files=None):
    """shared: dict of already-computed placeholders from linux_kernel_emitter.emit.

    out_files: 可选 dict；seam 模式下把 let_impl.c 追加进去（返回值仍是宿主文本）。
    """
    xdom_tick = shared.get("xdom_tick_shift_single")
    if not xdom_tick:
        xdom_tick = "\t/* 下一相位由 kf_arm_next() 按 base+cycle+phase 绝对计算 */\n"

    probe_before = shared.get("probe_before_sw", "")
    probe_after = shared.get("probe_after_sw", "")
    import os
    # 对照组分离（C vs D）：读写实现切到独立编译单元，宿主/事件表/计时点/门禁全同。
    seam = os.environ.get("KF_LET_STUB_SEAM", "0") == "1"
    let_bits = _build_slots_and_let(ir, seam=seam)
    _gid = let_bits.get("prod_gid")
    if out_files is not None:
        out_files["kf_let_host.h"] = _LET_HOST_H % {
            "prod_gid": int(_gid) if _gid is not None else -1}
        if seam and let_bits.get("let_impl_c"):
            _fname, _ftext = let_bits["let_impl_c"]
            out_files[_fname] = _ftext
    # 默认：有 irq 槽则 HARD；可用环境 KF_LET_LOG_CAP / KF_HRTIMER_HARD 覆盖
    import os
    log_cap = os.environ.get("KF_LET_LOG_CAP")
    if log_cap is None:
        log_cap = 700000 if let_bits.get("any_irq") or let_bits.get("nslots", 0) else 0
    else:
        log_cap = int(log_cap)
    hard = os.environ.get("KF_HRTIMER_HARD")
    if hard is None:
        hard = int(let_bits.get("hrtimer_hard") or 0)
    else:
        hard = int(hard)

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
        "let_run_decl": let_bits["let_run_decl"],
        "let_log_cap": log_cap,
        "hrtimer_hard": hard,
        "prod_gid": int(let_bits.get("prod_gid") if let_bits.get("prod_gid") is not None else -1),
        "age_budget_ns": int(shared.get("deadline", shared["period"])) * 1000,
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
