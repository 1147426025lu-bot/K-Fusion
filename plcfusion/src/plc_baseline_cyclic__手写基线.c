/*
 * plc_baseline_cyclic__手写基线.c — 论文对照：最小手写 hrtimer 1ms 抖动计
 *
 * 不含 PLCFusion、不含 cyclictest 融合代码；仅 ktime 测 jitter min/max。
 * 构建: scripts/paper/ignite_baseline_cyclic__手写基线.sh
 */
#include <linux/module.h>
#include <linux/hrtimer.h>
#include <linux/ktime.h>
#include <linux/slab.h>
#include <linux/kthread.h>
#include <linux/completion.h>
#include <linux/cpumask.h>
#include <linux/debugfs.h>
#include <linux/fs.h>

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("PLCFusion paper baseline: minimal hrtimer cyclic jitter meter");

static int probe_cpu = 3;
static int interval_us = 1000;
static bool shutdown_req;

static struct hrtimer baseline_hr;
static struct task_struct *baseline_task;
static struct completion baseline_done;

static unsigned long cycles;
static long min_ns = 1000000;
static long max_ns;
static ktime_t last_tick;
static bool primed;

module_param(probe_cpu, int, 0644);
module_param(interval_us, int, 0644);

static enum hrtimer_restart baseline_handler(struct hrtimer *timer)
{
	ktime_t now = ktime_get();
	u64 base_ns = (u64)interval_us * 1000ULL;
	s64 delta, jitter_ns;

	(void)timer;
	if (shutdown_req)
		return HRTIMER_NORESTART;

	if (primed) {
		delta = ktime_to_ns(ktime_sub(now, last_tick));
		jitter_ns = delta - (s64)base_ns;
		if (jitter_ns < min_ns)
			min_ns = jitter_ns;
		if (jitter_ns > max_ns)
			max_ns = jitter_ns;
		cycles++;
		last_tick = now;
	} else {
		primed = true;
		last_tick = now;
	}

	hrtimer_forward_now(&baseline_hr, ns_to_ktime(base_ns));
	return HRTIMER_RESTART;
}

static char stats_buf[96];

static ssize_t baseline_stats_show(char *buf)
{
	long abs_max = min_ns < 0 ? -min_ns : min_ns;
	long b = max_ns < 0 ? -max_ns : max_ns;

	if (b > abs_max)
		abs_max = b;
	if (!cycles)
		return sprintf(buf, "idle\n");
	return sprintf(buf,
		       "cycles=%lu min_ns=%ld max_ns=%ld abs_max_ns=%ld source=baseline_hrtimer\n",
		       cycles, min_ns, max_ns, abs_max);
}

static ssize_t baseline_stats_read(struct file *file, char __user *ubuf,
				   size_t count, loff_t *ppos)
{
	int len = baseline_stats_show(stats_buf);

	(void)file;
	return simple_read_from_buffer(ubuf, count, ppos, stats_buf, len);
}

static const struct file_operations baseline_stats_fops = {
	.read = baseline_stats_read,
	.llseek = noop_llseek,
};

static int baseline_worker(void *arg)
{
	struct cpumask mask;

	(void)arg;
	if (probe_cpu >= 0 && probe_cpu < nr_cpu_ids) {
		cpumask_clear(&mask);
		cpumask_set_cpu(probe_cpu, &mask);
		set_cpus_allowed_ptr(current, &mask);
	}

	primed = false;
	cycles = 0;
	min_ns = 1000000;
	max_ns = 0;
	last_tick = ktime_get();

	hrtimer_init(&baseline_hr, CLOCK_MONOTONIC, HRTIMER_MODE_REL_PINNED_HARD);
	baseline_hr.function = baseline_handler;
	hrtimer_start(&baseline_hr, ns_to_ktime((u64)interval_us * 1000ULL),
		      HRTIMER_MODE_REL_PINNED_HARD);

	while (!shutdown_req)
		schedule_timeout_interruptible(HZ);

	hrtimer_cancel(&baseline_hr);
	kthread_complete_and_exit(&baseline_done, 0);
}

static int __init baseline_init(void)
{
	if (interval_us < 1)
		interval_us = 1000;

	debugfs_create_file("baseline_stats", 0444, NULL, NULL, &baseline_stats_fops);

	baseline_task = kthread_create(baseline_worker, NULL, "baseline_cyclic");
	if (IS_ERR(baseline_task))
		return PTR_ERR(baseline_task);

	init_completion(&baseline_done);
	wake_up_process(baseline_task);
	printk("baseline_cyclic: cpu=%d interval_us=%d (hand-written paper ref)\n",
	       probe_cpu, interval_us);
	return 0;
}

static void __exit baseline_exit(void)
{
	long abs_max;
	long b;

	shutdown_req = true;
	if (baseline_task)
		wake_up_process(baseline_task);
	wait_for_completion_timeout(&baseline_done, msecs_to_jiffies(5000));

	abs_max = min_ns < 0 ? -min_ns : min_ns;
	b = max_ns < 0 ? -max_ns : max_ns;
	if (b > abs_max)
		abs_max = b;

	printk("BaselineSummary: cycles=%lu min_ns=%ld max_ns=%ld abs_max_ns=%ld source=baseline_hrtimer\n",
	       cycles, min_ns, max_ns, abs_max);
}

module_init(baseline_init);
module_exit(baseline_exit);
