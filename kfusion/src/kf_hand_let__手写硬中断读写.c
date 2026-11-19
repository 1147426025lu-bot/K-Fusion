/*
 * kf_hand_let__手写硬中断读写.c — 论文 C 组：手写定时器硬中断读写基线
 *
 * 目的：与 D 组（编译器生成硬中断读写，irq 臂 / kf_task）做配对对照。
 *   同源同任务（同一 let_pc_shared 产消任务、同一共享变量、同一相位 400us 写 / 500us 读），
 *   唯一变量 = 读写桩由谁产生：本模块手写 vs D 组经 PLCFusionPass 从契约生成。
 *
 * 观测口径与 D 组逐字节一致（这样 let_log_dump__导出读写日志.py 直接可读）：
 *   d = t_after - nominal（完成时刻 - 逻辑时刻，可正可负）
 *   a = |d|；读写分报 max|a|，并记 late/early
 *   /dev/kf_let_log：magic 0x4b464c54，头 8×u32，样本 <I4B3qI（36B）
 *   ctx 标志：hardirq=0x01 softirq=0x02 task=0x04 nmi=0x08
 *
 * 构建（在 Pi 内核树）：
 *   make -C /lib/modules/$(uname -r)/build M=<本目录> modules
 *   insmod kf_task.ko cpu=3 period_us=1000 rt_priority=80 let_log_cap=...
 * 注意：模块名必须是 kf_task（对象名 kf_task.o），以复用 sysfs 参数路径与实验脚本。
 */
#include <linux/module.h>
#include <linux/hrtimer.h>
#include <linux/ktime.h>
#include <linux/slab.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/sched.h>
#include <linux/sched/types.h>
#include <linux/kthread.h>
#include <linux/cpumask.h>
#include <linux/vmalloc.h>

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Paper group C: hand-written hardirq shared read/write baseline (LET)");

/* ---- 与 D 组同口径的常量 ---- */
#define KF_LET_OP_WRITE   1u
#define KF_LET_OP_READ    2u
#define KF_LET_ST_OK      0u
#define KF_LET_CTX_HARD   0x01u
#define KF_LET_CTX_SOFT   0x02u
#define KF_LET_CTX_TASK   0x04u
#define KF_LET_CTX_NMI    0x08u
#define KF_LET_LOG_MAGIC  0x4b464c54u
#define KF_N_SLOTS        4
#define KF_SLOT_G0_PH     0LL        /* group0 @0us：worker 跑计算体 */
#define KF_SLOT_WRITE_PH  400000LL   /* output_commit @400us（硬中断纯读写桩） */
#define KF_SLOT_READ_PH   500000LL   /* input_snapshot @500us（硬中断纯读写桩） */
#define KF_SLOT_G1_PH     700000LL   /* group1 @700us：本拍收尾记账 */

/* ---- 共享数据（与 let_pc_shared 同语义；obs 暴露供冻结核对） ---- */
static int32_t g_state;
static int32_t g_cmd_pending;
static int32_t g_cmd;
static int32_t g_cmd_ver;
static int32_t g_cmd_snap;
static int32_t g_cmd_snap_ver;
static int32_t g_seed = 12345;
static int32_t g_ready;

/* ---- 模块参数（默认烧入与 D 组对齐） ---- */
static int cpu = 3;
static unsigned int period_us = 1000;
static unsigned int rt_priority = 80;
static unsigned int let_log_cap = 200000;
static int hrtimer_hard = 1;   /* 1=ABS_PINNED_HARD（默认，C 组主口径）0=软中断，作对照 */
module_param(cpu, int, 0444);
module_param(period_us, uint, 0444);
module_param(rt_priority, uint, 0444);
module_param(let_log_cap, uint, 0444);
module_param(hrtimer_hard, int, 0444);

/* ---- 观测量（sysfs 参数名与 D 组一致） ---- */
static unsigned long periods;
static unsigned long let_rw_fires;
static unsigned long let_rw_irq;
static unsigned long let_rd_ok;
static unsigned long let_wr_ok;
static unsigned long let_skip_commit;
static unsigned long let_ctx_hardirq;
static unsigned long let_ctx_softirq;
static unsigned long let_ctx_task;
static unsigned long let_log_drops;
static long let_rw_lat_max_ns;
static long let_rd_jit_max_ns;
static long let_wr_jit_max_ns;
static long let_rd_late_max_ns;
static long let_rd_early_max_ns;
static long let_wr_late_max_ns;
static long let_wr_early_max_ns;
static long let_op_dur_max_ns;
/* 回调全程耗时：入口→出口（含 kf_arm_next 重装定时器）。
 * 用途：回答「硬中断里除读写桩还花了多少」，判断"纯读写桩"是否真纯净。
 * 只记 max 与超阈值计数——内核侧没有分位数，别把 max 当典型值。 */
static long let_cb_dur_max_ns;
static long let_cb_wr_dur_max_ns;
static long let_cb_rd_dur_max_ns;
static unsigned long let_cb_over_10us;
static unsigned long deadline_miss;
static unsigned long missing_groups;
static unsigned long release_overflow;
module_param(periods, ulong, 0444);
module_param(let_rw_fires, ulong, 0444);
module_param(let_rw_irq, ulong, 0444);
module_param(let_rd_ok, ulong, 0444);
module_param(let_wr_ok, ulong, 0444);
module_param(let_skip_commit, ulong, 0444);
module_param(let_ctx_hardirq, ulong, 0444);
module_param(let_ctx_softirq, ulong, 0444);
module_param(let_ctx_task, ulong, 0444);
module_param(let_log_drops, ulong, 0444);
module_param(let_rw_lat_max_ns, long, 0444);
module_param(let_rd_jit_max_ns, long, 0444);
module_param(let_wr_jit_max_ns, long, 0444);
module_param(let_rd_late_max_ns, long, 0444);
module_param(let_rd_early_max_ns, long, 0444);
module_param(let_wr_late_max_ns, long, 0444);
module_param(let_wr_early_max_ns, long, 0444);
module_param(let_op_dur_max_ns, long, 0444);
module_param(let_cb_dur_max_ns, long, 0444);
module_param(let_cb_wr_dur_max_ns, long, 0444);
module_param(let_cb_rd_dur_max_ns, long, 0444);
module_param(let_cb_over_10us, ulong, 0444);
module_param(deadline_miss, ulong, 0444);
module_param(missing_groups, ulong, 0444);
module_param(release_overflow, ulong, 0444);
/* obs 变量（供 frozen 快照核对，参数名与 D 组一致） */
module_param_named(g_state, g_state, int, 0444);
module_param_named(g_cmd_pending, g_cmd_pending, int, 0444);
module_param_named(g_cmd, g_cmd, int, 0444);
module_param_named(g_cmd_ver, g_cmd_ver, int, 0444);
module_param_named(g_cmd_snap, g_cmd_snap, int, 0444);
module_param_named(g_cmd_snap_ver, g_cmd_snap_ver, int, 0444);

/* ---- 逐样本日志结构与 magic（与 dump 脚本逐字节一致） ---- */
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
	u32 arm_path;   /* C 组置 2 以便与 D 组(1)/thread(0) 区分；dump 仅记录不判定 */
	u32 reserved;
};

static struct kf_let_sample *kf_let_log;
static unsigned int kf_let_log_n;
static struct miscdevice kf_let_log_dev;

static enum hrtimer_mode kf_timer_mode;
static struct hrtimer kf_timer;
static s64 kf_base_ns;
static u64 kf_cycle;
static int kf_next_slot;   /* 0=g0计算 1=写 2=读 3=g1收尾 */

/* 计算交给 worker 线程（与 D 组同构）：硬中断只跑纯读写桩 */
static struct task_struct *kf_worker;
static wait_queue_head_t kf_wait;
static int kf_group_pending;   /* 硬中断置位，worker 消费 */
static unsigned long g_out;
module_param_named(g_out, g_out, ulong, 0444);

/* 手写计算体（对应 stage_prod/act）：产值和消费，均不在硬中断里跑 */
static void hand_compute_body(void)
{
	/* 产：更新 g_state -> g_cmd_pending，供 400us 写桩提交 */
	WRITE_ONCE(g_state, READ_ONCE(g_state) + (READ_ONCE(g_seed) % 7) - 3);
	WRITE_ONCE(g_seed, READ_ONCE(g_seed) * 1103515245LL + 12345);
	WRITE_ONCE(g_cmd_pending, READ_ONCE(g_state));
	/* 消费：上一拍 g_cmd_snap -> 输出 */
	WRITE_ONCE(g_out, (unsigned long)READ_ONCE(g_cmd_snap));
	WRITE_ONCE(g_ready, 1);   /* 计算就绪，写桩可提交 */
}

static u8 kf_ctx_flags(void)
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

/* 手写读写桩：与 let_pc_shared 的 output_commit / input_snapshot 同语义 */
static int hand_stub_write(u32 *ver_out)
{
	if (!READ_ONCE(g_ready)) {
		let_skip_commit++;
		if (ver_out)
			*ver_out = READ_ONCE(g_cmd_ver);
		return 1; /* skip */
	}
	WRITE_ONCE(g_cmd, READ_ONCE(g_cmd_pending));
	WRITE_ONCE(g_cmd_ver, READ_ONCE(g_cmd_ver) + 1u);
	WRITE_ONCE(g_ready, false);
	if (ver_out)
		*ver_out = READ_ONCE(g_cmd_ver);
	return KF_LET_ST_OK;
}

static int hand_stub_read(u32 *ver_out)
{
	WRITE_ONCE(g_cmd_snap, READ_ONCE(g_cmd));
	WRITE_ONCE(g_cmd_snap_ver, READ_ONCE(g_cmd_ver));
	if (ver_out)
		*ver_out = READ_ONCE(g_cmd_snap_ver);
	return KF_LET_ST_OK;
}

static void kf_note_abs(s64 nominal_ns, s64 done_ns, u8 op, u8 status)
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
	} else {
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

static void kf_log_push(const struct kf_let_sample *s)
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
	smp_wmb();
	kf_let_log_n = i + 1;
}

static void kf_exec(u8 let_op, s64 nominal_ns)
{
	struct kf_let_sample s;
	u32 ver = 0;
	int st;
	u8 ctx;
	s64 dur;

	memset(&s, 0, sizeof(s));
	s.cycle_id = (u32)kf_cycle;
	s.op = let_op;
	s.cpu = (u8)smp_processor_id();
	s.nominal_ns = nominal_ns;
	s.t_before_ns = ktime_get();
	if (let_op == KF_LET_OP_WRITE)
		st = hand_stub_write(&ver);
	else
		st = hand_stub_read(&ver);
	s.t_after_ns = ktime_get();
	ctx = kf_ctx_flags();
	s.ctx = ctx;
	s.status = (st < 0) ? 3u : (u8)st;
	s.version = ver;

	dur = s.t_after_ns - s.t_before_ns;
	if (dur > let_op_dur_max_ns)
		let_op_dur_max_ns = dur;

	if (ctx & KF_LET_CTX_HARD)
		let_ctx_hardirq++;
	else if (ctx & KF_LET_CTX_SOFT)
		let_ctx_softirq++;
	else
		let_ctx_task++;
	let_rw_fires++;
	let_rw_irq++;   /* C 组读写桩恒在硬中断回调 */

	kf_note_abs(nominal_ns, s.t_after_ns, let_op, s.status);
	kf_log_push(&s);
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
	hdr.arm_path = 2u;  /* 手写硬中断 */
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

static s64 kf_phase_ns(int slot)
{
	switch (slot) {
	case 0: return KF_SLOT_G0_PH;
	case 1: return KF_SLOT_WRITE_PH;
	case 2: return KF_SLOT_READ_PH;
	default: return KF_SLOT_G1_PH;
	}
}

static void kf_arm_next(void)
{
	s64 at = kf_base_ns + (s64)kf_cycle * (s64)period_us * 1000LL
	       + kf_phase_ns(kf_next_slot);
	hrtimer_start(&kf_timer, ns_to_ktime(at), kf_timer_mode);
}

/* worker：执行计算体（任务态），不参与读写抖动测量 */
static int kf_worker_fn(void *data)
{
	(void)data;
	while (!kthread_should_stop()) {
		wait_event_interruptible(kf_wait,
			READ_ONCE(kf_group_pending) || kthread_should_stop());
		if (kthread_should_stop())
			break;
		if (!READ_ONCE(kf_group_pending))
			continue;
		WRITE_ONCE(kf_group_pending, 0);
		hand_compute_body();
	}
	return 0;
}

static enum hrtimer_restart kf_tick(struct hrtimer *t)
{
	s64 nominal_ns = ktime_to_ns(hrtimer_get_expires(t));
	s64 t_entry = ktime_get();
	int slot = kf_next_slot;
	s64 cb_dur;

	/* 硬中断回调只做：纯读写桩 或 置位唤醒 worker（计算不在硬中断跑） */
	switch (kf_next_slot) {
	case 0: /* group0 @0us：唤醒 worker 跑计算 */
		WRITE_ONCE(kf_group_pending, 1);
		wake_up_interruptible(&kf_wait);
		kf_next_slot = 1;
		break;
	case 1: /* output_commit @400us：纯写桩 */
		kf_exec(KF_LET_OP_WRITE, nominal_ns);
		kf_next_slot = 2;
		break;
	case 2: /* input_snapshot @500us：纯读桩 */
		kf_exec(KF_LET_OP_READ, nominal_ns);
		kf_next_slot = 3;
		break;
	default: /* group1 @700us：收尾记账 */
		kf_next_slot = 0;
		kf_cycle++;
		periods = kf_cycle;
		break;
	}
	kf_arm_next();

	/* 全程耗时含 kf_arm_next 重装，故恒大于单桩耗时；差值即固定开销 */
	cb_dur = ktime_get() - t_entry;
	if (cb_dur > let_cb_dur_max_ns)
		let_cb_dur_max_ns = cb_dur;
	if (slot == 1 && cb_dur > let_cb_wr_dur_max_ns)
		let_cb_wr_dur_max_ns = cb_dur;
	if (slot == 2 && cb_dur > let_cb_rd_dur_max_ns)
		let_cb_rd_dur_max_ns = cb_dur;
	if (cb_dur > 10000)
		let_cb_over_10us++;

	return HRTIMER_NORESTART;
}

static int __init kf_init(void)
{
	int ret;
	ktime_t t0;

	if (cpu >= 0 && !cpu_online(cpu)) {
		pr_err("kf_hand: cpu %d 不在线\n", cpu);
		return -EINVAL;
	}
	/* 默认硬中断（C 组主口径）；hrtimer_hard=0 退到软中断，供单变量对照 */
	kf_timer_mode = hrtimer_hard ? HRTIMER_MODE_ABS_PINNED_HARD
				     : HRTIMER_MODE_ABS_PINNED;

	if (let_log_cap) {
		kf_let_log = vzalloc((unsigned long)let_log_cap * sizeof(struct kf_let_sample));
		if (!kf_let_log) {
			pr_err("kf_hand: let_log 分配失败\n");
			return -ENOMEM;
		}
		kf_let_log_n = 0;
		kf_let_log_dev.minor = MISC_DYNAMIC_MINOR;
		kf_let_log_dev.name = "kf_let_log";
		kf_let_log_dev.fops = &kf_let_log_fops;
		kf_let_log_dev.mode = 0444;
		ret = misc_register(&kf_let_log_dev);
		if (ret) {
			vfree(kf_let_log);
			kf_let_log = NULL;
			pr_err("kf_hand: misc_register 失败 %d\n", ret);
			return ret;
		}
	}

	init_waitqueue_head(&kf_wait);
	WRITE_ONCE(kf_group_pending, 0);
	kf_worker = kthread_create(kf_worker_fn, NULL, "kf_hand_worker");
	if (IS_ERR(kf_worker)) {
		ret = PTR_ERR(kf_worker);
		if (kf_let_log) {
			misc_deregister(&kf_let_log_dev);
			vfree(kf_let_log);
			kf_let_log = NULL;
		}
		pr_err("kf_hand: kthread_create 失败 %d\n", ret);
		return ret;
	}
	if (cpu >= 0) {
		kthread_bind(kf_worker, cpu);
		sched_set_fifo(kf_worker);
	}
	wake_up_process(kf_worker);

	hrtimer_init(&kf_timer, CLOCK_MONOTONIC, kf_timer_mode);
	kf_timer.function = kf_tick;
	kf_cycle = 0;
	kf_next_slot = 0;
	kf_base_ns = ktime_to_ns(ktime_get()) + 200000000LL;  /* 200ms 后起步 */
	t0 = ns_to_ktime(kf_base_ns + KF_SLOT_G0_PH);
	hrtimer_start(&kf_timer, t0, kf_timer_mode);
	pr_info("kf_hand: C组手写定时读写已启动 cpu=%d period_us=%u mode=%s（计算在 worker）\n",
		cpu, period_us, hrtimer_hard ? "hardirq" : "softirq");
	return 0;
}

static void __exit kf_exit(void)
{
	hrtimer_cancel(&kf_timer);
	if (kf_worker) {
		kthread_stop(kf_worker);
		kf_worker = NULL;
	}
	if (kf_let_log) {
		misc_deregister(&kf_let_log_dev);
		vfree(kf_let_log);
		kf_let_log = NULL;
	}
	pr_info("kf_hand: 卸载 cycles=%lu rd_ok=%lu wr_ok=%lu hardirq=%lu cb_max=%ldns over10us=%lu\n",
		periods, let_rd_ok, let_wr_ok, let_ctx_hardirq,
		let_cb_dur_max_ns, let_cb_over_10us);
}

module_init(kf_init);
module_exit(kf_exit);
