/* ecos_fused_linux.c —— 由 ECoS 融合方案生成的 ring-0 宿主；不要手改。
 *
 * 方案：1 组 / 3 阶段，周期 1000 µs，证据级别 empirical
 * 组落位：g0@0µs[stage_sample + stage_pid + stage_sink]
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
#include <linux/atomic.h>	/* 释放计数：hrtimer 回调与内核线程共享，必须原子 */
#include <linux/string.h>
#include <linux/smp.h>
#include <uapi/linux/sched/types.h>	/* struct sched_attr（uapi；内部 sched/types.h 里没有） */


#include "ecos_fused_body.h"


#define ECOS_GROUPS 1
#define ECOS_NSTAGES 3
#define ECOS_FIRST_GROUP 0
#define ECOS_LAST_GROUP 0

static unsigned int period_us = 1000;
module_param(period_us, uint, 0444);
MODULE_PARM_DESC(period_us, "控制周期（µs），默认取方案里的周期");

/* 实时优先级：默认 99，与用户态 BM2 实验的 FIFO 99 对齐。
 * 注意 5.15 只导出 sched_setattr_nocheck / sched_set_fifo；
 * sched_setscheduler_nocheck 在 kallsyms 里有符号但**没有 EXPORT**，链接/加载会失败。 */
static unsigned int rt_priority = 99;
module_param(rt_priority, uint, 0444);
MODULE_PARM_DESC(rt_priority, "内核线程 SCHED_FIFO 优先级（默认 99，与用户态口径一致）");

/* 绑核：-1 = 不绑（默认）。>=0 时把每组的内核线程和它的 hrtimer 都放到该核上。
 * 为什么要绑：不绑时线程可能迁移，唤醒走跨核 IPI，抖动里混进了"跑到哪去了"
 * 这个与融合无关的变量。用户态 BM2 基线是绑核跑的，对比必须同口径。 */
static int cpu = -1;
module_param(cpu, int, 0444);
MODULE_PARM_DESC(cpu, "绑定的 CPU 编号（-1 = 不绑）");

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

/* 1 组 1 个定时器 + 1 个线程 + 1 个唤醒点。
 *
 * ⚠ 释放计数必须是**原子**的：hrtimer 回调（硬中断上下文）与内核线程并发读写。
 * 旧实现用普通 `bool pending`，两处裸访问既非原子、又把"多次释放"塌成"一次"
 * ——线程落后时丢拍且完全不可见。换成 atomic_t 后：每次定时器 atomic_inc，
 * 线程用 atomic_xchg 一次取走全部，n-1 即积压拍数（记进 missed）。 */
struct ecos_group {
	int id;
	struct hrtimer timer;
	struct task_struct *task;
	wait_queue_head_t wait;
	atomic_t releases;	/* 自上次运行以来定时器释放了几次（原子） */
	s64 last_r_ns;		/* 本拍名义释放 r_k（hrtimer 到期时刻） */
};

static struct ecos_group ecos_groups[ECOS_GROUPS] = {
	{ .id = 0 }
};

static const s64 ecos_phase_ns[ECOS_GROUPS] = {
0
};

/* 观察变量：把指定全局量发布成只读 sysfs 参数，加载后可 cat 出来与
 * 用户态参考实现比对。没有观察点就"加载成功"也证明不了算得对。
 * 限制：只支持 int32_t 全局量（其余类型请自己在上层转成 int32_t）。 */
extern int32_t g_in;
static int32_t ecos_obs_0;
module_param_named(g_in, ecos_obs_0, int, 0444);
extern int32_t g_integ;
static int32_t ecos_obs_1;
module_param_named(g_integ, ecos_obs_1, int, 0444);
extern int32_t g_out;
static int32_t ecos_obs_2;
module_param_named(g_out, ecos_obs_2, int, 0444);
extern int32_t g_seq;
static int32_t ecos_obs_3;
module_param_named(g_seq, ecos_obs_3, int, 0444);

static unsigned long periods;      /* 已完成周期数（由组 0 计数） */
module_param(periods, ulong, 0444);
MODULE_PARM_DESC(periods, "已完成周期数");

/* 积压拍数：线程来不及跑时，定时器已释放但它还没跑的拍数（Σ(n-1)）。
 * 严格周期配对实验里这个数必须为 0；非 0 说明"周期体超时/线程被抢占到兑现不了
 * 周期"，此时再谈死区与延迟都没有意义——先把落后消掉。 */
static unsigned long missed;
module_param(missed, ulong, 0444);
MODULE_PARM_DESC(missed, "积压拍数（线程落后于定时器释放的次数，正常应为 0）");

/* 冻结开关：sysfs 上分几次 cat 参数之间会跨周期（10 ms 周期下必然发生），
 * 读出来的 periods / 观察变量就不属于同一拍。写 1 暂停周期体，取完快照再写 0。
 * 冻结期间不执行、也不计数——所以快照里的 periods 与各变量严格同拍。 */
static bool frozen;
module_param(frozen, bool, 0644);
MODULE_PARM_DESC(frozen, "写 1 暂停周期体（取一致快照），写 0 恢复");



/* 与 plc_probe__统一测量探针.h 同口径的 r/s/f（内核不能 include 用户态 stdio 头）。
 *   start_lat = s − r    唤醒延迟
 *   exec      = f − s    执行时间
 *   response  = f − r    端到端 R（论文量）
 *   miss      = f > r + T
 * 只在组 0 记账（与 periods 同一拍）。读 rsf_probe。 */
#include <linux/math64.h>

#define ECOS_RSF_BUCKETS 1024	/* 0..1023 µs，与 fusion_codegen 默认桶对齐（flat 的 R≈800µs） */

struct ecos_rsf_series {
	const char *name;
	unsigned long hist[ECOS_RSF_BUCKETS];
	unsigned long n;
	unsigned long over;
	long min_ns, max_ns;
	s64 sum_ns;
};

static struct ecos_rsf_series ecos_rsf_start = { .name = "start_lat", .min_ns = LONG_MAX };
static struct ecos_rsf_series ecos_rsf_exec  = { .name = "exec",      .min_ns = LONG_MAX };
static struct ecos_rsf_series ecos_rsf_resp  = { .name = "response",  .min_ns = LONG_MAX };
static unsigned long ecos_rsf_miss;
static s64 ecos_rsf_r0, ecos_rsf_s0;	/* 本拍首组 r/s，末组完成时配对 */

static void ecos_rsf_push(struct ecos_rsf_series *s, s64 v_ns)
{
	unsigned int b;
	long v;

	if (v_ns < 0)
		v_ns = 0;
	v = (long)v_ns;
	s->n++;
	s->sum_ns += v_ns;
	if (s->n == 1 || v < s->min_ns)
		s->min_ns = v;
	if (v > s->max_ns)
		s->max_ns = v;
	b = (unsigned int)div_s64(v_ns, 1000);
	if (b < ECOS_RSF_BUCKETS)
		s->hist[b]++;
	else
		s->over++;
}

static void ecos_rsf_add(s64 r_ns, s64 s_ns, s64 f_ns)
{
	s64 T = (s64)period_us * 1000;

	ecos_rsf_push(&ecos_rsf_start, s_ns - r_ns);
	ecos_rsf_push(&ecos_rsf_exec,  f_ns - s_ns);
	ecos_rsf_push(&ecos_rsf_resp,  f_ns - r_ns);
	if (f_ns > r_ns + T)
		ecos_rsf_miss++;
}

static long ecos_rsf_pct(const struct ecos_rsf_series *s, unsigned int permille)
{
	unsigned long need, cum = 0;
	int i;

	if (!s->n)
		return -1;
	need = (s->n * (unsigned long)permille + 999UL) / 1000UL;
	if (need == 0)
		need = 1;
	for (i = 0; i < ECOS_RSF_BUCKETS; i++) {
		cum += s->hist[i];
		if (cum >= need)
			return (long)i;
	}
	return (long)ECOS_RSF_BUCKETS;	/* over 夹持：分位是下界 */
}

static int ecos_rsf_show(char *buf, const struct kernel_param *kp)
{
	int n = 0;
	const struct ecos_rsf_series *ss[3] = {
		&ecos_rsf_start, &ecos_rsf_exec, &ecos_rsf_resp
	};
	int i;

	n += scnprintf(buf + n, PAGE_SIZE - n,
		       "PLC_PROBE r/s/f v1.2 kernel miss=%lu periods=%lu\n",
		       ecos_rsf_miss, periods);
	for (i = 0; i < 3; i++) {
		const struct ecos_rsf_series *s = ss[i];
		s64 avg = s->n ? div_s64(s->sum_ns, s->n) : 0;

		n += scnprintf(buf + n, PAGE_SIZE - n,
			       "  %s n=%lu over=%lu min=%ldns p50=%ldus p99=%ldus "
			       "avg=%lldns max=%ldns\n",
			       s->name, s->n, s->over, s->min_ns,
			       ecos_rsf_pct(s, 500), ecos_rsf_pct(s, 990),
			       (long long)avg, s->max_ns);
	}
	return n;
}

static const struct kernel_param_ops ecos_rsf_ops = {
	.get = ecos_rsf_show,
};

module_param_cb(rsf_probe, &ecos_rsf_ops, NULL, 0444);
MODULE_PARM_DESC(rsf_probe, "统一 r/s/f：start_lat / exec / response（与用户态 plc_probe 同口径）");


/* ---- 阶段执行时间实测（profiler）----
 * 每个阶段在 ecos_fused_body.c 里被 ecos_stage_enter/exit 包裹（弱符号钩子）。
 * 这里提供强定义覆盖弱符号，用 ktime_get() 量出每个阶段的真实执行时间。
 * 用途：消除 ast_to_plan 里 exec_us 的“占位”诚实边界——在目标板上跑一次，
 * 读 exec_probe 即可拿到每阶段 max/avg（ns），回填 --exec-us。 */
#include <linux/math64.h>	/* div_s64 */

static s64 ecos_stage_max_ns[ECOS_NSTAGES];
static s64 ecos_stage_sum_ns[ECOS_NSTAGES];
static unsigned long ecos_stage_cnt[ECOS_NSTAGES];
static s64 ecos_stage_t0[ECOS_NSTAGES];
static const char *ecos_stage_names[ECOS_NSTAGES] = {
	"stage_sample",
	"stage_pid",
	"stage_sink",
};

void ecos_stage_enter(unsigned int idx)
{
	if (idx < ECOS_NSTAGES)
		ecos_stage_t0[idx] = ktime_get();
}

void ecos_stage_exit(unsigned int idx)
{
	if (idx < ECOS_NSTAGES) {
		s64 d = ktime_get() - ecos_stage_t0[idx];

		if (d > 0) {
			ecos_stage_cnt[idx]++;
			ecos_stage_sum_ns[idx] += d;
			if (ecos_stage_cnt[idx] == 1 || d > ecos_stage_max_ns[idx])
				ecos_stage_max_ns[idx] = d;
		}
	}
}

static void ecos_stage_report(void)
{
	int i;

	pr_info("ecos: 阶段执行时间实测（ns）：");
	for (i = 0; i < ECOS_NSTAGES; i++) {
		s64 avg = ecos_stage_cnt[i]
			? div_s64(ecos_stage_sum_ns[i], ecos_stage_cnt[i]) : 0;

		pr_info("  [%d] %s: max=%lld avg=%lld n=%lu", i,
			ecos_stage_names[i], (long long)ecos_stage_max_ns[i],
			(long long)avg, ecos_stage_cnt[i]);
	}
}

/* 读 exec_probe 即打印每阶段 max/avg（ns），无需卸载模块。 */
static int ecos_stage_show(char *buf, const struct kernel_param *kp)
{
	int n = 0, i;
	unsigned long long tot = 0;

	for (i = 0; i < ECOS_NSTAGES; i++)
		tot += (unsigned long long)ecos_stage_cnt[i];
	n += scnprintf(buf + n, PAGE_SIZE - n, "count=%llu samples\n", tot);
	for (i = 0; i < ECOS_NSTAGES; i++) {
		s64 avg = ecos_stage_cnt[i]
			? div_s64(ecos_stage_sum_ns[i], ecos_stage_cnt[i]) : 0;

		n += scnprintf(buf + n, PAGE_SIZE - n,
			       "  [%d] %s: max=%lldns avg=%lldns n=%lu\n",
			       i, ecos_stage_names[i],
			       (long long)ecos_stage_max_ns[i],
			       (long long)avg, ecos_stage_cnt[i]);
	}
	return n;
}

static const struct kernel_param_ops ecos_stage_ops = {
	.get = ecos_stage_show,
};

module_param_cb(exec_probe, &ecos_stage_ops, NULL, 0444);
MODULE_PARM_DESC(exec_probe, "每阶段实测 max/avg 执行时间（ns），读即打印");


static enum hrtimer_restart ecos_tick(struct hrtimer *t)
{
	struct ecos_group *g = container_of(t, struct ecos_group, timer);

	/* 定时器里只计数 + 唤醒；业务逻辑留给线程，避免在硬中断上下文跑控制律。
	 * atomic_inc 保证"落后时的多次释放"一次不丢——这正是严格配对实验要看的。 */
	g->last_r_ns = ktime_to_ns(hrtimer_get_expires(t));
	atomic_inc(&g->releases);
	wake_up_interruptible(&g->wait);
	hrtimer_forward_now(&g->timer, ktime_set(0, (s64)period_us * 1000));
	return HRTIMER_RESTART;
}

static void ecos_run_body(int id)
{
	switch (id) {
	case 0:
		ecos_body_g0();
		break;
	}
	if (id == 0)
		periods++;
	ecos_obs_0 = g_in;
	ecos_obs_1 = g_integ;
	ecos_obs_2 = g_out;
	ecos_obs_3 = g_seq;
}

struct ecos_start_arg {
	int idx;
	ktime_t at;
};

/* 在目标核上启动定时器：HRTIMER_MODE_ABS_PINNED 钉的是**调用它的那个 CPU**，
 * 所以必须让这段代码真正跑在该核上。hrtimer_start 在 IPI（中断）上下文里
 * 是安全的，不需要睡眠。 */
static void ecos_start_on_cpu(void *info)
{
	struct ecos_start_arg *a = info;

	hrtimer_start(&ecos_groups[a->idx].timer, a->at, HRTIMER_MODE_ABS_PINNED);
}

static int ecos_runner(void *data)
{	struct ecos_group *g = data;

	while (!kthread_should_stop()) {
		wait_event_interruptible(g->wait,
					 atomic_read(&g->releases) != 0 || kthread_should_stop());
		if (kthread_should_stop())
			break;
		{
			/* 原子取走并清零释放计数：n = 自上次运行以来定时器释放了几次。
			 * n == 0：伪唤醒，回去睡。n > 1：线程落后，n-1 记进 missed（积压拍数），
			 * 但**只跑一次**周期体——固定周期控制律不该用"追赶"抹掉迟到的事实，
			 * 把落后暴露出来才是严格配对实验要的。 */
			int n = atomic_xchg(&g->releases, 0);

			if (n == 0)
				continue;
			if (n > 1)
				missed += (unsigned long)(n - 1);
		}
		/* 冻结：不执行、不计数 —— 保证 sysfs 快照同拍 */
		if (unlikely(frozen))
			continue;

		{
			s64 r_ns = g->last_r_ns;
			s64 s_ns = ktime_get();

			ecos_run_body(g->id);
			{
				s64 f_ns = ktime_get();

				/* 端到端 R：首组的 r/s + 末组的 f（与用户态 driver 的 response 同口径）。
				 * 单组融合时 FIRST==LAST，退化为组内三戳。 */
				if (g->id == ECOS_FIRST_GROUP) {
					ecos_rsf_r0 = r_ns;
					ecos_rsf_s0 = s_ns;
				}
				if (g->id == ECOS_LAST_GROUP)
					ecos_rsf_add(ecos_rsf_r0, ecos_rsf_s0, f_ns);
			}
		}

	}
	return 0;
}

static int __init ecos_init(void)
{
	int i;
	s64 t0;

	if (cpu >= 0 && !cpu_online(cpu)) {
		pr_err("ecos: cpu %d 不在线（看 /sys/devices/system/cpu/online）\n", cpu);
		return -EINVAL;
	}

	/* 第一遍：建线程。**不在这里定相位**——kthread_create/设优先级每次要花
	 * 几十~几百 µs；若每组各取一次 ktime_get() 当锚点，相位就被逐组污染。
	 * 实测（5 组，period=1000）：plant 名义 800 µs 实际跑到 ≈1370 µs，
	 * 死区量出 626 µs 而不是 200 µs。必须建完线程再统一取一次 t0。 */
	for (i = 0; i < ECOS_GROUPS; i++) {
		struct ecos_group *g = &ecos_groups[i];

		init_waitqueue_head(&g->wait);
		atomic_set(&g->releases, 0);

		hrtimer_init(&g->timer, CLOCK_MONOTONIC, HRTIMER_MODE_ABS_PINNED);
		g->timer.function = ecos_tick;

		g->task = kthread_create(ecos_runner, g, "ecos_g%d", g->id);
		if (IS_ERR(g->task)) {
			pr_err("ecos: kthread_create(%d) failed\n", g->id);
			return PTR_ERR(g->task);
		}
		/* 注意：sched_setscheduler_nocheck 在 5.15 **没有导出**（insmod 会报
		 * Unknown symbol），只有 sched_set_fifo / sched_setattr_nocheck 导出。
		 * 这里优先用 sched_setattr_nocheck 把优先级设成 rt_priority（默认 99，
		 * 与用户态 BM2 的 FIFO 99 对齐），失败再退回 sched_set_fifo。
		 */
		ecos_set_fifo(g->task, rt_priority);
		if (cpu >= 0)
			kthread_bind(g->task, cpu);
		wake_up_process(g->task);
	}

	/* 第二遍：所有组共用一个锚点，组间相位差才等于方案里的相位差。
	 * 绑核时把每个定时器都在目标核上启动，否则 ABS_PINNED 会钉在 insmod
	 * 所在的那个核上，线程与定时器分居两核，唤醒要走跨核 IPI。 */
	t0 = ktime_get();
	for (i = 0; i < ECOS_GROUPS; i++) {
		if (cpu >= 0) {
			struct ecos_start_arg a;

			a.idx = i;
			a.at = t0 + ecos_phase_ns[i];
			smp_call_function_single(cpu, ecos_start_on_cpu, &a, 1);
		} else {
			hrtimer_start(&ecos_groups[i].timer,
				      t0 + ecos_phase_ns[i],
				      HRTIMER_MODE_ABS_PINNED);
		}
	}

	pr_info("ecos: %d 组已启动，周期 %u µs，FIFO 优先级 %u，cpu %d（证据级别 empirical）\n",
		ECOS_GROUPS, period_us, rt_priority, cpu);
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
	ecos_stage_report();
	pr_info("ecos: 已停止\n");
}

module_init(ecos_init);
module_exit(ecos_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ECoS fused periodic control (1 groups)");
