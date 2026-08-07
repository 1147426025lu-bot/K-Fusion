/*
 * plc_runner_official.c — cyclictest 主线专用宿主（hrtimer + L2 调优）
 *
 * 功能: timerthread 来自融合 .o；timer_create → plc_timer hrtimer；
 *       支持 runner_profile fused/plc、jitter 补偿、ringbuf/hist 开关
 * 构建: scripts/deploy/ignite_official_cycletest__cyclictest主线.sh
 */
#include <linux/module.h>
#include <linux/hrtimer.h>
#include <linux/ktime.h>
#include <linux/slab.h>
#include <linux/printk.h>
#include <linux/moduleparam.h>
#include <linux/kthread.h>
#include <linux/completion.h>
#include <linux/sched.h>
#include <linux/sched/rt.h>
#include <linux/sched/signal.h>
#include <linux/cpumask.h>
#include <linux/stdarg.h>
#include <linux/vmalloc.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/uaccess.h>
#include <linux/wait.h>
#include <linux/atomic.h>
#include <linux/delay.h>
#include <linux/debugfs.h>
#include <linux/sched/types.h>

#include "../include/plc_shm__共享内存.h"

MODULE_LICENSE("GPL");

extern int shutdown;
extern int use_nsecs;

#define MODE_CYCLIC		0
#define MODE_SYS_NANOSLEEP	3
#define CLOCK_MONOTONIC		1
#define SCHED_FIFO		1
#define CYCLIC_TIMER_RELTIME	0
#define FUSED_CYCLIC_SIG	(SIGRTMIN + 3)

#define VALBUF_SIZE		16384
#define VALBUF_MASK		(VALBUF_SIZE - 1)

/* 与 rt-tests cyclictest.c 布局一致，供 official_cycletest_kernel.o 读写 */
struct thread_stat {
	unsigned long cycles;
	unsigned long cyclesread;
	long min;
	long max;
	long act;
	double avg;
	long *values;
	long *smis;
	void *hist;
	unsigned long thread;
	int threadstarted;
	int tid;
	long reduce;
	long redmax;
	long cycleofmax;
	unsigned long smi_count;
};

struct thread_param {
	int prio;
	int policy;
	int mode;
	int timermode;
	int signal;
	int clock;
	unsigned long max_cycles;
	struct thread_stat *stats;
	int bufmsk;
	unsigned long interval;
	int cpu;
	int node;
	int tnum;
	int msr_fd;
};

struct plc_timespec {
	long tv_sec;
	long tv_nsec;
};

struct plc_itimerspec {
	struct plc_timespec it_interval;
	struct plc_timespec it_value;
};

struct plc_cyclic_timer {
	struct hrtimer hr;
	struct task_struct *task;
	wait_queue_head_t waitq;
	ktime_t interval;
	ktime_t next_abs;
	ktime_t next_nominal;
	atomic_t pending;
	atomic_t overrun_acc;
	bool active;
	u64 comp_warmup;
};

struct fused_fast {
	unsigned long cycles;
	long min_ns;
	long max_ns;
	bool active;
	bool primed;
	ktime_t last_tick;
	u64 interval_ns;
	long *values;
};

struct fused_ring_file_hdr {
	u32 magic;
	u32 version;
	u64 cycles;
	s64 min_ns;
	s64 max_ns;
	u32 sample_count;
	u32 hist_bins;
	s64 hist_lo_ns;
	s64 hist_step_ns;
} __packed;

#define FUSED_RING_MAGIC	0x504c434aUL
#define FUSED_RING_VERSION	2
#define FUSED_HIST_BINS		400
#define FUSED_HIST_LO_NS	(-25000L)
#define FUSED_HIST_STEP_NS	125L

void *stdout;
void *stderr;
char *optarg;
int optind;

static bool shutdown_requested;
static struct thread_param *fused_par;
static struct thread_stat *fused_stat;
static struct task_struct *fused_task;
static struct completion fused_worker_done;
static struct plc_cyclic_timer fused_cyclic;
static struct fused_fast fused_fast;

static struct plc_shm *plc_shm;
static struct miscdevice plc_misc;
static bool plc_misc_registered;

static int cycletest_priority = 99;
static int jitter_probe_cpu = 3;
static int timerthread_cpu = -1;
static bool probe_rt_enable = true;
static ulong cycletest_interval_us = 1000;
static bool shutdown_request;

static char runner_profile_str[32] = "fused";
static char ring_export_path[256];
static char plc_tdma_periods_str[64] = "1000,2000,4000";
static int export_decim_max;
static int decim_stride = 50;
static long *fused_decim;
static unsigned long fused_decim_cap;
static u32 *fused_hist;
static int clock_abs_enable = 1;
static bool jitter_compensation_enable = true;
static int jitter_resync_thresh_ns = 0;
static int jitter_ewma_ignore_ns = 2000;
static bool jitter_spike_log_enable;
static bool fused_hist_enable = true;
static bool fused_wake_timertthread = true;
static bool fused_ringbuf_enable = true;

#define FUSED_EWMA_ALPHA		16
#define FUSED_EWMA_SHIFT		4
#define FUSED_MAX_COMPENSATION_NS	500LL
#define FUSED_COMP_WARMUP_SAMPLES	64

static s64 fused_jitter_ewma_ns;
static s64 fused_jitter_compensation_ns;
static u32 fused_spike_resync_count;
static unsigned int measure_grace_ticks;
static unsigned int measure_grace_default = 128;

module_param(cycletest_priority, int, 0644);
module_param(jitter_probe_cpu, int, 0644);
module_param(timerthread_cpu, int, 0644);
module_param(probe_rt_enable, bool, 0644);
module_param(cycletest_interval_us, ulong, 0644);
module_param_string(runner_profile, runner_profile_str, sizeof(runner_profile_str), 0644);
module_param_string(ring_export_path, ring_export_path, sizeof(ring_export_path), 0644);
module_param_string(plc_tdma_periods_us, plc_tdma_periods_str,
		    sizeof(plc_tdma_periods_str), 0644);
module_param(export_decim_max, int, 0644);
module_param(decim_stride, int, 0644);
module_param(clock_abs_enable, int, 0644);
module_param(jitter_compensation_enable, bool, 0644);
module_param(jitter_resync_thresh_ns, int, 0644);
module_param(measure_grace_default, uint, 0644);
module_param(jitter_ewma_ignore_ns, int, 0644);
module_param(jitter_spike_log_enable, bool, 0644);
module_param(fused_hist_enable, bool, 0644);
module_param(fused_wake_timertthread, bool, 0644);
module_param(fused_ringbuf_enable, bool, 0644);

extern void *timerthread(void *param);

static bool fused_is_spike(s64 jitter_ns)
{
	if (jitter_resync_thresh_ns <= 0)
		return false;
	return jitter_ns > jitter_resync_thresh_ns ||
	       jitter_ns < -jitter_resync_thresh_ns;
}

static bool profile_is_plc(void)
{
	return strcmp(runner_profile_str, "plc") == 0;
}

static int effective_probe_cpu(void)
{
	return jitter_probe_cpu >= 0 ? jitter_probe_cpu : 3;
}

static int effective_timerthread_cpu(void)
{
	if (timerthread_cpu >= 0 && timerthread_cpu < nr_cpu_ids &&
	    cpu_online(timerthread_cpu))
		return timerthread_cpu;
	return effective_probe_cpu();
}

static void pin_cpu(int cpu, const char *role)
{
	struct cpumask mask;
	int ret;

	if (cpu < 0 || cpu >= nr_cpu_ids || !cpu_online(cpu))
		return;
	cpumask_clear(&mask);
	cpumask_set_cpu(cpu, &mask);
	ret = set_cpus_allowed_ptr(current, &mask);
	if (ret)
		printk("⚠️ [AI-PLC] pin %s cpu=%d failed ret=%d\n", role, cpu, ret);
	else
		printk("🧷 [AI-PLC] pinned %s to cpu=%d\n", role, cpu);
}

static void configure_rt_fifo(int prio, const char *role)
{
	if (prio < 1)
		prio = 1;
	if (prio > MAX_RT_PRIO - 1)
		prio = MAX_RT_PRIO - 1;
	sched_set_fifo(current);
	current->timer_slack_ns = 0;
	printk("🎯 [AI-PLC] %s SCHED_FIFO prio=%d\n", role, prio);
}

static void fused_comp_reset(void)
{
	fused_jitter_ewma_ns = 0;
	fused_jitter_compensation_ns = 0;
	fused_cyclic.comp_warmup = 0;
}

static inline void fused_update_compensation(s64 jitter_ns)
{
	s64 aj = jitter_ns < 0 ? -jitter_ns : jitter_ns;

	if (jitter_ewma_ignore_ns > 0 && aj > jitter_ewma_ignore_ns)
		return;

	fused_jitter_ewma_ns = ((FUSED_EWMA_ALPHA - 1) * fused_jitter_ewma_ns + jitter_ns)
		>> FUSED_EWMA_SHIFT;

	if (!jitter_compensation_enable)
		return;

	fused_jitter_compensation_ns = fused_jitter_ewma_ns;
	if (fused_jitter_compensation_ns > FUSED_MAX_COMPENSATION_NS)
		fused_jitter_compensation_ns = FUSED_MAX_COMPENSATION_NS;
	else if (fused_jitter_compensation_ns < -FUSED_MAX_COMPENSATION_NS)
		fused_jitter_compensation_ns = -FUSED_MAX_COMPENSATION_NS;
}

static void fused_fast_reset(u64 interval_ns)
{
	fused_fast.cycles = 0;
	fused_fast.min_ns = 1000000;
	fused_fast.max_ns = 0;
	fused_fast.primed = false;
	fused_fast.interval_ns = interval_ns;
	fused_fast.active = true;
	fused_comp_reset();
	if (fused_hist)
		memset(fused_hist, 0, FUSED_HIST_BINS * sizeof(u32));
}

static void fused_hist_record(s64 j)
{
	u32 bin;
	s64 rel;

	if (!fused_hist)
		return;
	rel = j - FUSED_HIST_LO_NS;
	if (rel < 0)
		return;
	bin = (u32)(rel / FUSED_HIST_STEP_NS);
	if (bin >= FUSED_HIST_BINS)
		bin = FUSED_HIST_BINS - 1;
	fused_hist[bin]++;
}

static void fused_resync_timer_phase(struct plc_cyclic_timer *ct, ktime_t now)
{
	ct->next_nominal = ktime_add_ns(now, ct->interval);
	fused_fast.last_tick = now;
	fused_fast.primed = true;
	fused_spike_resync_count++;
}

static void fused_schedule_abs_timer(struct plc_cyclic_timer *ct)
{
	ct->next_abs = ct->next_nominal;
	if (jitter_compensation_enable &&
	    ct->comp_warmup > FUSED_COMP_WARMUP_SAMPLES)
		ct->next_abs = ktime_sub_ns(ct->next_abs,
					    fused_jitter_compensation_ns);
	hrtimer_start(&ct->hr, ct->next_abs, HRTIMER_MODE_ABS_PINNED_HARD);
}

static void fused_fast_record(s64 jitter_ns)
{
	unsigned long idx = fused_fast.cycles;

	if (jitter_ns < fused_fast.min_ns)
		fused_fast.min_ns = jitter_ns;
	if (jitter_ns > fused_fast.max_ns)
		fused_fast.max_ns = jitter_ns;
	fused_fast.cycles++;
	if (fused_hist_enable)
		fused_hist_record(jitter_ns);
	if (fused_ringbuf_enable && fused_decim && decim_stride > 0 &&
	    (idx % decim_stride) == 0) {
		unsigned long slot = idx / decim_stride;

		if (slot < fused_decim_cap)
			fused_decim[slot] = jitter_ns;
	}
	if (fused_fast.values)
		fused_fast.values[idx & VALBUF_MASK] = jitter_ns;
}

static void fused_export_samples_file(void)
{
	struct fused_ring_file_hdr hdr;
	struct file *filp;
	loff_t pos = 0;
	unsigned long decim_n;
	ssize_t w;

	if (!ring_export_path[0] || !fused_fast.cycles)
		return;

	decim_n = fused_fast.cycles / decim_stride;
	if (decim_n > fused_decim_cap)
		decim_n = fused_decim_cap;

	memset(&hdr, 0, sizeof(hdr));
	hdr.magic = FUSED_RING_MAGIC;
	hdr.version = FUSED_RING_VERSION;
	hdr.cycles = fused_fast.cycles;
	hdr.min_ns = fused_fast.min_ns;
	hdr.max_ns = fused_fast.max_ns;
	hdr.sample_count = (u32)decim_n;
	hdr.hist_bins = fused_hist ? FUSED_HIST_BINS : 0;
	hdr.hist_lo_ns = FUSED_HIST_LO_NS;
	hdr.hist_step_ns = FUSED_HIST_STEP_NS;

	filp = filp_open(ring_export_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
	if (IS_ERR(filp))
		return;

	w = kernel_write(filp, &hdr, sizeof(hdr), &pos);
	if (w == sizeof(hdr) && decim_n)
		kernel_write(filp, fused_decim, decim_n * sizeof(long), &pos);
	if (w == sizeof(hdr) && fused_hist)
		kernel_write(filp, fused_hist, FUSED_HIST_BINS * sizeof(u32), &pos);
	filp_close(filp, NULL);
	printk("📁 [AI-PLC] export decim=%lu hist_bins=%u cycles=%llu path=%s\n",
	       decim_n, hdr.hist_bins, hdr.cycles, ring_export_path);
}

static enum hrtimer_restart plc_cyclic_handler(struct hrtimer *timer)
{
	struct plc_cyclic_timer *ct = container_of(timer, struct plc_cyclic_timer, hr);
	ktime_t now;
	s64 delta, jitter_ns;
	bool spike = false;
	u64 base_ns = (u64)cycletest_interval_us * 1000ULL;

	if (READ_ONCE(shutdown) || READ_ONCE(shutdown_requested))
		return HRTIMER_NORESTART;

	now = ktime_get();
	jitter_ns = 0;
	if (fused_fast.active) {
		if (fused_fast.primed) {
			delta = ktime_to_ns(ktime_sub(now, fused_fast.last_tick));
			jitter_ns = delta - (s64)base_ns;
			spike = fused_is_spike(jitter_ns);

			if (measure_grace_ticks > 0) {
				measure_grace_ticks--;
				fused_fast.last_tick = now;
				spike = false;
			} else {
				/* 始终如实记录 — 不丢弃尖峰样本 */
				fused_fast_record(jitter_ns);
				ct->comp_warmup++;
				if (ct->comp_warmup > FUSED_COMP_WARMUP_SAMPLES)
					fused_update_compensation(jitter_ns);

				if (spike && jitter_resync_thresh_ns > 0) {
					if (jitter_spike_log_enable &&
					    fused_spike_resync_count < 64)
						printk("JitterSpikeResync: jitter=%lld ns resync#=%u\n",
						       jitter_ns, fused_spike_resync_count + 1);
					fused_resync_timer_phase(ct, now);
				} else {
					fused_fast.last_tick = now;
				}
			}
		} else {
			fused_fast.primed = true;
			fused_fast.last_tick = now;
		}
	}

	if (ct->task && fused_wake_timertthread) {
		atomic_inc(&ct->pending);
		wake_up_process(ct->task);
	}

	if (clock_abs_enable) {
		if (!spike || jitter_resync_thresh_ns <= 0) {
			ct->next_nominal = ktime_add_ns(ct->next_nominal, ct->interval);
			if (ktime_after(now, ktime_add_ns(ct->next_nominal, ct->interval)))
				ct->next_nominal = ktime_add_ns(now, ct->interval);
		}
		fused_schedule_abs_timer(ct);
	} else {
		hrtimer_forward_now(&ct->hr, ct->interval);
	}

	return HRTIMER_RESTART;
}

static int plc_misc_open(struct inode *inode, struct file *file)
{
	(void)inode;
	(void)file;
	return 0;
}

static int plc_misc_mmap(struct file *file, struct vm_area_struct *vma)
{
	unsigned long size = vma->vm_end - vma->vm_start;

	(void)file;
	if (!plc_shm || size > PAGE_ALIGN(PLC_SHM_SIZE))
		return -EINVAL;
	return remap_vmalloc_range(vma, (void *)plc_shm, 0);
}

static const struct file_operations plc_fops = {
	.owner = THIS_MODULE,
	.open = plc_misc_open,
	.mmap = plc_misc_mmap,
};

static int plc_misc_init(void)
{
	int ret;

	if (plc_misc_registered)
		return 0;

	plc_shm = vmalloc_user(PAGE_ALIGN(PLC_SHM_SIZE));
	if (!plc_shm)
		return -ENOMEM;
	memset(plc_shm, 0, PAGE_ALIGN(PLC_SHM_SIZE));
	plc_shm->hdr.magic = PLC_SHM_MAGIC;
	plc_shm->hdr.version = PLC_SHM_VERSION;
	plc_misc.minor = MISC_DYNAMIC_MINOR;
	plc_misc.name = "plcfusion";
	plc_misc.fops = &plc_fops;
	ret = misc_register(&plc_misc);
	if (ret) {
		vfree(plc_shm);
		plc_shm = NULL;
		return ret;
	}
	plc_misc_registered = true;
	return 0;
}

static void plc_misc_exit(void)
{
	if (plc_misc_registered) {
		misc_deregister(&plc_misc);
		plc_misc_registered = false;
	}
	if (plc_shm) {
		vfree(plc_shm);
		plc_shm = NULL;
	}
}

/* --- PLC POSIX 桩（供融合 cyclictest 调用） --- */

int plc_ktime_get_ts(int clk_id, struct plc_timespec *ts)
{
    u64 ns = ktime_get_ns();

    (void)clk_id;
    ts->tv_sec = div_u64(ns, NSEC_PER_SEC);
    ts->tv_nsec = (long)(ns - (u64)ts->tv_sec * NSEC_PER_SEC);
    return 0;
}

int plc_sleep(int clockid, int flags, struct plc_timespec *request,
              struct plc_timespec *remain)
{
    u64 ns;
    u64 slept;
    const u64 chunk_ns = 100 * NSEC_PER_USEC;

    (void)clockid;
    (void)flags;
    (void)remain;

    if (!request)
        return 0;

    ns = (u64)request->tv_sec * NSEC_PER_SEC + (u64)request->tv_nsec;
    if (!ns)
        return 0;

    for (slept = 0; slept < ns; ) {
        u64 step = ns - slept;
        ktime_t expire;

        if (READ_ONCE(shutdown) || READ_ONCE(shutdown_requested))
            return 0;
        if (fatal_signal_pending(current))
            return -EINTR;

        if (step > chunk_ns)
            step = chunk_ns;

        if (step < 50 * NSEC_PER_USEC) {
            u32 us = div_u64(step + NSEC_PER_USEC - 1, NSEC_PER_USEC);

            usleep_range(us, us + 1);
        } else {
            expire = ktime_add_ns(ktime_get(), step);
            set_current_state(TASK_INTERRUPTIBLE);
            schedule_hrtimeout(&expire, HRTIMER_MODE_ABS);
            __set_current_state(TASK_RUNNING);
        }
        slept += step;
		cond_resched();
	}

	return fatal_signal_pending(current) ? -EINTR : 0;
}

int plc_nanosleep(const struct plc_timespec *req, struct plc_timespec *rem)
{
	return plc_sleep(CLOCK_MONOTONIC, 0, (struct plc_timespec *)req, rem);
}

int plc_clock_nanosleep(int clockid, int flags,
			const struct plc_timespec *request,
			struct plc_timespec *remain)
{
	return plc_sleep(clockid, flags, (struct plc_timespec *)request, remain);
}

int plc_printk(const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
    return 0;
}

void *plc_kmalloc(size_t size)
{
	return kmalloc(size, in_interrupt() ? GFP_ATOMIC : GFP_KERNEL);
}

int usleep(unsigned long usec)
{
	if (!usec)
        return 0;
    usleep_range(usec, usec + 1);
    return 0;
}

struct option {
	const char *name;
	int has_arg;
	int *flag;
	int val;
};

int getopt_long(int argc, char *const argv[], const char *optstring,
		const struct option *longopts, int *longindex)
{
	(void)argc;
	(void)argv;
	(void)optstring;
	(void)longopts;
	(void)longindex;
    return -1;
}

int plc_timer_create(int clockid, void *sevp, void **timerid)
{
	struct plc_cyclic_timer *ct = &fused_cyclic;

	(void)clockid;
	(void)sevp;
	if (!timerid)
		return -EINVAL;
	if (ct->active)
		return -EBUSY;

	hrtimer_init(&ct->hr, CLOCK_MONOTONIC, HRTIMER_MODE_REL_PINNED_HARD);
	ct->hr.function = plc_cyclic_handler;
	ct->task = current;
	init_waitqueue_head(&ct->waitq);
	atomic_set(&ct->pending, 0);
	atomic_set(&ct->overrun_acc, 0);
	ct->interval = ns_to_ktime((u64)cycletest_interval_us * 1000ULL);
	ct->next_nominal = ktime_add_ns(ktime_get(), ct->interval);
	ct->next_abs = ct->next_nominal;
	ct->comp_warmup = 0;
	ct->active = true;
	*timerid = (void *)ct;
	return 0;
}

int plc_timer_settime(void *timerid, int flags,
		      const struct plc_itimerspec *new_value,
		      struct plc_itimerspec *old_value)
{
	struct plc_cyclic_timer *ct = (struct plc_cyclic_timer *)timerid;
	u64 interval_ns;

	(void)old_value;
	(void)flags;
	if (!ct || !ct->active || !new_value)
		return -EINVAL;

	interval_ns = (u64)new_value->it_interval.tv_sec * NSEC_PER_SEC +
			(u64)new_value->it_interval.tv_nsec;
	if (!interval_ns)
		interval_ns = (u64)cycletest_interval_us * 1000ULL;

	ct->interval = ns_to_ktime(interval_ns);
	fused_fast_reset(interval_ns);
	ct->next_nominal = ktime_add_ns(ktime_get(), ct->interval);
	ct->next_abs = ct->next_nominal;
	hrtimer_start(&ct->hr, ct->next_abs, HRTIMER_MODE_ABS_PINNED_HARD);
	return 0;
}

int plc_timer_getoverrun(void *timerid)
{
	struct plc_cyclic_timer *ct = (struct plc_cyclic_timer *)timerid;

	if (!ct)
		return 0;
	return atomic_xchg(&ct->overrun_acc, 0);
}

int plc_timer_delete(void *timerid)
{
	struct plc_cyclic_timer *ct = (struct plc_cyclic_timer *)timerid;

	if (!ct || !ct->active)
		return -EINVAL;
	hrtimer_cancel(&ct->hr);
	ct->active = false;
	ct->task = NULL;
	fused_fast.active = false;
	return 0;
}

int plc_sigemptyset(unsigned long *set)
{
	if (!set)
		return -EINVAL;
	*set = 0;
	return 0;
}

int plc_sigaddset(unsigned long *set, int sig)
{
	if (!set)
		return -EINVAL;
	*set |= (1UL << (sig - 1));
	return 0;
}

int plc_sigprocmask(int how, unsigned long *set, unsigned long *oldset)
{
	(void)how;
	(void)set;
	(void)oldset;
	return 0;
}

int plc_sigwait(const unsigned long *set, int *sig)
{
	struct plc_cyclic_timer *ct = &fused_cyclic;
	long ret;

	(void)set;
	for (;;) {
		if (READ_ONCE(shutdown) || READ_ONCE(shutdown_requested))
			return -EINTR;
		if (atomic_read(&ct->pending) > 0) {
			atomic_dec(&ct->pending);
			if (sig)
				*sig = FUSED_CYCLIC_SIG;
			return 0;
		}
		ret = wait_event_interruptible(ct->waitq,
					       atomic_read(&ct->pending) > 0 ||
					       READ_ONCE(shutdown) ||
					       READ_ONCE(shutdown_requested));
		if (ret)
			return ret;
	}
}

int plc_setscheduler(int pid, int policy, const struct sched_param *param)
{
	(void)pid;
	(void)policy;
	if (param)
		sched_set_fifo(current);
	return 0;
}

unsigned long plc_pthread_self(void)
{
	return (unsigned long)current;
}

int plc_pthread_setaffinity_np(unsigned long thread, size_t cpusetsize,
			       const unsigned long *cpuset)
{
	(void)thread;
	(void)cpusetsize;
	(void)cpuset;
	return 0;
}

int plc_gettid(void)
{
	return task_pid_nr(current);
}

/* cyclictest 主线：融合 .o 中 main 的 pthread 调用需可链接，热路径不执行 */
int plc_pthread_create(unsigned long *thread, void *attr,
		       void *(*start_routine)(void *), void *arg)
{
	(void)thread;
	(void)attr;
	(void)start_routine;
	(void)arg;
	return 0;
}

int plc_pthread_join(unsigned long thread, void **retval)
{
	(void)thread;
	(void)retval;
	return 0;
}

int plc_pthread_kill(unsigned long thread, int sig)
{
	(void)thread;
	(void)sig;
	return 0;
}

static char fused_stats_buf[128];

static ssize_t fused_stats_show(char *buf)
{
	long abs_max = fused_fast.min_ns < 0 ? -fused_fast.min_ns : fused_fast.min_ns;
	long b = fused_fast.max_ns < 0 ? -fused_fast.max_ns : fused_fast.max_ns;

	if (b > abs_max)
		abs_max = b;
	if (!fused_fast.cycles)
		return sprintf(buf, "idle\n");
	return sprintf(buf,
			 "cycles=%lu min_ns=%ld max_ns=%ld abs_max_ns=%ld ring_samples=%u source=fused_hrtimer\n",
			 fused_fast.cycles, fused_fast.min_ns, fused_fast.max_ns,
			 abs_max, VALBUF_SIZE);
}

static struct dentry *fused_stats_dentry;
static struct dentry *fused_stats_reset_dentry;

static void fused_measurement_stats_reset(void)
{
	fused_fast.cycles = 0;
	fused_fast.min_ns = 1000000;
	fused_fast.max_ns = 0;
	fused_spike_resync_count = 0;
	measure_grace_ticks = measure_grace_default;
	if (fused_hist)
		memset(fused_hist, 0, FUSED_HIST_BINS * sizeof(u32));
}

static ssize_t fused_stats_reset_write(struct file *file, const char __user *ubuf,
				       size_t count, loff_t *ppos)
{
	char buf[16];
	u64 interval_ns;
	ssize_t n;

	(void)file;
	(void)ppos;
	if (!count)
		return 0;
	n = simple_write_to_buffer(buf, sizeof(buf) - 1, ppos, ubuf, count);
	if (n <= 0)
		return n;
	buf[n < (ssize_t)sizeof(buf) ? n : sizeof(buf) - 1] = '\0';
	if (strncmp(buf, "reset_stats", 11) == 0) {
		fused_measurement_stats_reset();
		printk("FusedStatsReset: stats-only cleared grace=%u comp retained resync=0\n",
		       measure_grace_default);
		return count;
	}
	if (strncmp(buf, "reset", 5) != 0)
		return -EINVAL;
	interval_ns = fused_fast.interval_ns;
	if (!interval_ns)
		interval_ns = (u64)cycletest_interval_us * 1000ULL;
	fused_spike_resync_count = 0;
	fused_fast_reset(interval_ns);
	printk("FusedStatsReset: full reset resync=0\n");
	return count;
}

static const struct file_operations fused_stats_reset_fops = {
	.write = fused_stats_reset_write,
	.llseek = noop_llseek,
};

static ssize_t fused_stats_read(struct file *file, char __user *ubuf,
				size_t count, loff_t *ppos)
{
	int len;

	(void)file;
	len = fused_stats_show(fused_stats_buf);
	return simple_read_from_buffer(ubuf, count, ppos, fused_stats_buf, len);
}

static const struct file_operations fused_stats_fops = {
	.read = fused_stats_read,
	.llseek = noop_llseek,
};

static void fused_dump_samples(void)
{
	unsigned long i, n, step;

	if (!fused_stat || !fused_stat->values)
        return;
	n = fused_stat->cycles;
	if (n > VALBUF_SIZE)
		n = VALBUF_SIZE;
	/* 长测时限制 Jitter: 行数，避免 dmesg 环缓冲冲掉 FusedSummary */
	step = n > 20000 ? n / 20000 : 1;
	if (n > 500000)
		step = n / 5000;
	for (i = 0; i < n; i += step) {
		printk("Jitter: %ld\n", fused_stat->values[i & VALBUF_MASK]);
		if ((i & 0xff) == 0)
        cond_resched();
    }
}

static void runner_request_stop(void)
{
	WRITE_ONCE(shutdown, 1);
	shutdown_requested = true;

	if (fused_cyclic.active) {
		hrtimer_cancel(&fused_cyclic.hr);
		fused_cyclic.active = false;
	}
	wake_up_interruptible(&fused_cyclic.waitq);
	if (fused_task)
		wake_up_process(fused_task);
}

static void fused_print_summary(const char *tag)
{
	const char *src = profile_is_plc() ? "plc_hrtimer" : "fused_hrtimer";

	if (fused_stat)
		printk("FusedSummary: tag=%s cycles=%lu min_ns=%ld max_ns=%ld source=%s\n",
		       tag, fused_stat->cycles, fused_stat->min, fused_stat->max, src);
	if (fused_fast.cycles)
		printk("FusedSummary: tag=%s cycles=%lu min_ns=%ld max_ns=%ld spike_resync=%u source=%s_fast\n",
		       tag, fused_fast.cycles, fused_fast.min_ns, fused_fast.max_ns,
		       fused_spike_resync_count, src);
}

static int fused_cycletest_worker(void *arg)
{
    (void)arg;

	pin_cpu(effective_timerthread_cpu(), "official_cycletest");
    if (probe_rt_enable)
		configure_rt_fifo(cycletest_priority, "official_cycletest");

	printk("🚀 [AI-PLC] timerthread (hrtimer-driven) interval=%lu us cpu=%d profile=%s\n",
	       cycletest_interval_us, effective_timerthread_cpu(), runner_profile_str);
	timerthread(fused_par);
	/* 勿 return 后由父线程 kthread_stop — 易与 module exit 竞态；用 complete_and_exit */
	kthread_complete_and_exit(&fused_worker_done, 0);
}

static int shutdown_request_set(const char *val, const struct kernel_param *kp)
{
	bool req = false;

	(void)kp;
	if (kstrtobool(val, &req))
		return -EINVAL;
	if (req)
		runner_request_stop();
    return 0;
}

static const struct kernel_param_ops shutdown_request_ops = {
	.set = shutdown_request_set,
	.get = param_get_bool,
};

module_param_cb(shutdown_request, &shutdown_request_ops, &shutdown_request, 0644);

static int __init runner_init(void)
{
	int plc_ret;

    if (cycletest_priority < 1)
        cycletest_priority = 80;
    if (cycletest_interval_us < 1)
        cycletest_interval_us = 1000;

	use_nsecs = 1;
	shutdown = 0;
	shutdown_requested = false;
	init_completion(&fused_worker_done);

	fused_stat = kzalloc(sizeof(*fused_stat), GFP_KERNEL);
	fused_par = kzalloc(sizeof(*fused_par), GFP_KERNEL);
	if (!fused_stat || !fused_par)
		return -ENOMEM;

	fused_stat->values = kzalloc(VALBUF_SIZE * sizeof(long), GFP_KERNEL);
	fused_fast.values = kzalloc(VALBUF_SIZE * sizeof(long), GFP_KERNEL);
	if (!fused_stat->values || !fused_fast.values)
		return -ENOMEM;

	if (fused_hist_enable) {
		fused_hist = kzalloc(FUSED_HIST_BINS * sizeof(u32), GFP_KERNEL);
		if (!fused_hist)
			return -ENOMEM;
	}

	if (fused_ringbuf_enable && export_decim_max > 0) {
		fused_decim = vmalloc(export_decim_max * sizeof(long));
		if (!fused_decim)
            return -ENOMEM;
		fused_decim_cap = export_decim_max;
	}

	fused_stat->min = 1000000;
	fused_stat->max = 0;
	fused_par->stats = fused_stat;
	fused_par->prio = cycletest_priority;
	fused_par->policy = SCHED_FIFO;
	fused_par->mode = MODE_CYCLIC;
	fused_par->timermode = 1; /* TIMER_ABSTIME，与 cyclictest 默认一致 */
	fused_par->clock = CLOCK_MONOTONIC;
	fused_par->signal = FUSED_CYCLIC_SIG;
	fused_par->interval = cycletest_interval_us;
	fused_par->cpu = effective_timerthread_cpu();
	fused_par->node = -1;
	fused_par->tnum = 0;
	fused_par->msr_fd = -1;
	fused_par->bufmsk = VALBUF_MASK;

	fused_stats_dentry = debugfs_create_file("fused_stats", 0444, NULL, NULL,
						 &fused_stats_fops);
	(void)fused_stats_dentry;
	fused_stats_reset_dentry = debugfs_create_file("fused_stats_reset", 0200,
						       NULL, NULL,
						       &fused_stats_reset_fops);
	(void)fused_stats_reset_dentry;

	plc_ret = 0;
	if (profile_is_plc())
		plc_ret = plc_misc_init();

	printk("🟢 [AI-PLC] loaded profile=%s cpu_probe=%d cpu_tt=%d abs=%d comp=%d hist=%d wake_tt=%d ring=%d plc_ret=%d\n",
	       runner_profile_str, effective_probe_cpu(),
	       effective_timerthread_cpu(), clock_abs_enable,
	       jitter_compensation_enable, fused_hist_enable,
	       fused_wake_timertthread, fused_ringbuf_enable, plc_ret);

	fused_task = kthread_create(fused_cycletest_worker, NULL, "official_cycletest");
	if (IS_ERR(fused_task)) {
		plc_misc_exit();
		return PTR_ERR(fused_task);
	}
	wake_up_process(fused_task);
    return 0;
}

static void __exit runner_exit(void)
{
	unsigned long tmo;

	printk("🟠 [AI-PLC] unloading profile=%s\n", runner_profile_str);
	runner_request_stop();

	if (fused_task) {
		tmo = wait_for_completion_timeout(&fused_worker_done,
						  msecs_to_jiffies(10000));
		if (!tmo) {
			printk("⚠️ [AI-PLC] timerthread 10s 超时，尝试 kthread_stop\n");
			kthread_stop(fused_task);
		}
		fused_task = NULL;
	}

	fused_export_samples_file();
	fused_export_samples_file();
	fused_dump_samples();
	fused_print_summary("final");

	debugfs_remove(fused_stats_reset_dentry);
	debugfs_remove(fused_stats_dentry);

	plc_misc_exit();

	vfree(fused_decim);
	kfree(fused_hist);
	kfree(fused_fast.values);
	kfree(fused_stat->values);
	kfree(fused_stat);
	kfree(fused_par);

	printk("🟢 [AI-PLC] unloaded\n");
}

module_init(runner_init);
module_exit(runner_exit);
