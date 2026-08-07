/*
 * plc_fused_timer_host.c — hrtimer 睡眠/定时宿主（FUSE_HOST=hrtimer）
 *
 * 功能: 强符号覆盖 stubs 中弱 plc_usleep/nanosleep/timer_*；供 rt-tests 类应用
 * 增强: ABS pinned 定时、CPU 绑定、debugfs fused_timer_stats、可选 jitter EWMA 补偿
 * 链接: ignite_fused__通用ko构建.sh 在 FUSE_HOST=hrtimer 时编入 .ko
 */
#include <linux/module.h>
#include <linux/hrtimer.h>
#include <linux/sched.h>
#include <linux/wait.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/delay.h>
#include <linux/stdarg.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>
#include <linux/cpumask.h>
#include <linux/moduleparam.h>
#include <linux/atomic.h>

#include "../include/plc_abi__运行时ABI.h"
#include "plc_hrtimer_core__定时核心.h"

extern int shutdown;

#define PLC_GEN_TIMER_MAX 8
#define PLC_GEN_SIG 42

struct plc_gen_timer {
	struct hrtimer hr;
	struct task_struct *task;
	wait_queue_head_t waitq;
	atomic_t pending;
	atomic_t overrun;
	ktime_t interval;
	ktime_t next_nominal;
	ktime_t next_abs;
	u64 comp_warmup;
	bool active;
};

static struct plc_gen_timer plc_gen_timers[PLC_GEN_TIMER_MAX];
static DEFINE_SPINLOCK(plc_gen_timer_lock);

static int fused_hrtimer_abs = 1;
static int fused_hrtimer_cpu = -1;
static bool fused_hrtimer_jitter_comp = true;
static atomic64_t fused_timer_fires = ATOMIC64_INIT(0);
static atomic64_t fused_timer_overruns = ATOMIC64_INIT(0);
static struct plc_hr_comp_state fused_comp;
static struct dentry *fused_timer_stats_dentry;
static bool fused_timer_stats_ready;

static void fused_timer_stats_init_once(void);

module_param(fused_hrtimer_abs, int, 0644);
MODULE_PARM_DESC(fused_hrtimer_abs, "1=HRTIMER_MODE_ABS_PINNED (cyclictest-like)");
module_param(fused_hrtimer_cpu, int, 0644);
MODULE_PARM_DESC(fused_hrtimer_cpu, "Pin timer thread to CPU (-1=any)");
module_param(fused_hrtimer_jitter_comp, bool, 0644);
MODULE_PARM_DESC(fused_hrtimer_jitter_comp, "EWMA jitter compensation for ABS timers");

static void fused_hrtimer_pin_self(void)
{
	plc_hr_pin_cpu(fused_hrtimer_cpu);
}

static const struct plc_hr_comp_cfg fused_comp_cfg = {
	.enabled = true,
	.ignore_above_ns = PLC_HR_DEFAULT_IGNORE_NS,
	.max_comp_ns = PLC_HR_DEFAULT_MAX_COMP_NS,
};

static void fused_update_compensation(s64 jitter_ns)
{
	struct plc_hr_comp_cfg cfg = fused_comp_cfg;

	cfg.enabled = fused_hrtimer_jitter_comp;
	plc_hr_comp_update(&fused_comp, &cfg, jitter_ns);
}

static struct plc_gen_timer *plc_gen_timer_alloc(void)
{
	int i;

	spin_lock(&plc_gen_timer_lock);
	for (i = 0; i < PLC_GEN_TIMER_MAX; i++) {
		if (!plc_gen_timers[i].active) {
			struct plc_gen_timer *t = &plc_gen_timers[i];

			memset(t, 0, sizeof(*t));
			init_waitqueue_head(&t->waitq);
			atomic_set(&t->pending, 0);
			atomic_set(&t->overrun, 0);
			t->active = true;
			spin_unlock(&plc_gen_timer_lock);
			return t;
		}
	}
	spin_unlock(&plc_gen_timer_lock);
	return NULL;
}

static void plc_gen_schedule_abs(struct plc_gen_timer *t)
{
	struct plc_hr_comp_cfg cfg = fused_comp_cfg;

	cfg.enabled = fused_hrtimer_jitter_comp;
	t->next_abs = plc_hr_abs_next(t->next_nominal, t->interval,
				      &fused_comp, &cfg, t->comp_warmup);
	hrtimer_start(&t->hr, t->next_abs, HRTIMER_MODE_ABS_PINNED_HARD);
}

static enum hrtimer_restart plc_gen_timer_fn(struct hrtimer *hr)
{
	struct plc_gen_timer *t = container_of(hr, struct plc_gen_timer, hr);
	ktime_t now;
	s64 delta, jitter_ns;

	atomic64_inc(&fused_timer_fires);
	if (READ_ONCE(shutdown))
		return HRTIMER_NORESTART;

	now = ktime_get();
	if (fused_hrtimer_abs) {
		if (t->comp_warmup > 0) {
			delta = ktime_to_ns(ktime_sub(now, t->next_nominal));
			jitter_ns = delta;
			fused_update_compensation(jitter_ns);
		}
		t->comp_warmup++;
	}

	atomic_inc(&t->pending);
	if (t->task)
		wake_up_process(t->task);
	wake_up_interruptible(&t->waitq);

	if (fused_hrtimer_abs) {
		t->next_nominal = ktime_add_ns(t->next_nominal, t->interval);
		if (ktime_after(now, ktime_add_ns(t->next_nominal, t->interval)))
			t->next_nominal = ktime_add_ns(now, t->interval);
		plc_gen_schedule_abs(t);
		return HRTIMER_NORESTART;
	}

	hrtimer_forward_now(hr, t->interval);
	return HRTIMER_RESTART;
}

int plc_ktime_get_ts(int clk_id, struct plc_timespec *ts)
{
	return plc_hr_ktime_get_ts(clk_id, ts);
}

int plc_nanosleep(const struct plc_timespec *req, struct plc_timespec *rem)
{
	fused_hrtimer_pin_self();
	return plc_hr_timespec_sleep(req, rem);
}

int plc_clock_nanosleep(int clockid, int flags,
			const struct plc_timespec *request,
			struct plc_timespec *remain)
{
	(void)clockid;
	(void)flags;
	fused_hrtimer_pin_self();
	return plc_hr_timespec_sleep(request, remain);
}

int plc_timer_create(int clockid, void *sevp, void **timerid)
{
	struct plc_gen_timer *t;

	(void)clockid;
	(void)sevp;
	if (!timerid)
		return -EINVAL;

	fused_hrtimer_pin_self();
	fused_timer_stats_init_once();
	t = plc_gen_timer_alloc();
	if (!t)
		return -ENOMEM;

	hrtimer_init(&t->hr, CLOCK_MONOTONIC, HRTIMER_MODE_REL_PINNED);
	t->hr.function = plc_gen_timer_fn;
	t->task = current;
	t->interval = ns_to_ktime(NSEC_PER_MSEC);
	t->next_nominal = ktime_get();
	*timerid = t;
	return 0;
}

int plc_timer_settime(void *timerid, int flags,
		      const struct plc_itimerspec *new_value,
		      struct plc_itimerspec *old_value)
{
	struct plc_gen_timer *t = timerid;
	u64 interval_ns;

	(void)flags;
	(void)old_value;
	if (!t || !t->active || !new_value)
		return -EINVAL;

	interval_ns = (u64)new_value->it_interval.tv_sec * NSEC_PER_SEC +
		      (u64)new_value->it_interval.tv_nsec;
	if (!interval_ns)
		interval_ns = NSEC_PER_MSEC;

	t->interval = ns_to_ktime(interval_ns);
	t->next_nominal = ktime_get();
	t->comp_warmup = 0;
	plc_hr_comp_reset(&fused_comp);

	if (fused_hrtimer_abs) {
		plc_gen_schedule_abs(t);
	} else {
		hrtimer_start(&t->hr, t->interval, HRTIMER_MODE_REL_PINNED);
	}
	return 0;
}

int plc_timer_getoverrun(void *timerid)
{
	struct plc_gen_timer *t = timerid;

	if (!t)
		return 0;
	atomic64_inc(&fused_timer_overruns);
	return atomic_xchg(&t->overrun, 0);
}

int plc_timer_delete(void *timerid)
{
	struct plc_gen_timer *t = timerid;

	if (!t || !t->active)
		return -EINVAL;
	hrtimer_cancel(&t->hr);
	spin_lock(&plc_gen_timer_lock);
	t->active = false;
	t->task = NULL;
	spin_unlock(&plc_gen_timer_lock);
	return 0;
}

#ifndef FUSED_HAVE_PTHREAD_HOST
int plc_sigwait(const unsigned long *set, int *sig)
{
	struct plc_gen_timer *t;
	int i;

	(void)set;
	for (i = 0; i < PLC_GEN_TIMER_MAX; i++) {
		t = &plc_gen_timers[i];
		if (!t->active || t->task != current)
			continue;
		for (;;) {
			if (READ_ONCE(shutdown))
				return -EINTR;
			if (atomic_read(&t->pending) > 0) {
				atomic_dec(&t->pending);
				if (sig)
					*sig = PLC_GEN_SIG;
				return 0;
			}
			if (wait_event_interruptible(t->waitq,
						     atomic_read(&t->pending) > 0 ||
						     READ_ONCE(shutdown)))
				return -ERESTARTSYS;
		}
	}
	schedule();
	if (sig)
		*sig = 0;
	return 0;
}
#endif

int plc_printk(const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
	return 0;
}

static int fused_timer_stats_show(struct seq_file *m, void *v)
{
	(void)v;
	seq_printf(m, "fires=%lld\n",
		   (long long)atomic64_read(&fused_timer_fires));
	seq_printf(m, "overruns=%lld\n",
		   (long long)atomic64_read(&fused_timer_overruns));
	seq_printf(m, "abs_mode=%d\n", fused_hrtimer_abs);
	seq_printf(m, "cpu=%d\n", fused_hrtimer_cpu);
	seq_printf(m, "jitter_comp=%d\n", fused_hrtimer_jitter_comp);
	seq_printf(m, "ewma_ns=%lld\n", (long long)fused_comp.ewma_ns);
	seq_printf(m, "comp_ns=%lld\n", (long long)fused_comp.comp_ns);
	return 0;
}

static int fused_timer_stats_open(struct inode *inode, struct file *file)
{
	return single_open(file, fused_timer_stats_show, NULL);
}

static const struct file_operations fused_timer_stats_fops = {
	.owner = THIS_MODULE,
	.open = fused_timer_stats_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static void fused_timer_stats_init_once(void)
{
	if (fused_timer_stats_ready)
		return;
	fused_timer_stats_dentry =
		debugfs_create_file("fused_timer_stats", 0444, NULL, NULL,
				    &fused_timer_stats_fops);
	fused_timer_stats_ready = true;
}
