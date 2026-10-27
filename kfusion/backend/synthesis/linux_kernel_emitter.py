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
#include <linux/atomic.h>	/* 释放计数：hrtimer 回调与内核线程共享，必须原子 */
#include <linux/string.h>
#include <linux/smp.h>
#include <uapi/linux/sched/types.h>	/* struct sched_attr（uapi；内部 sched/types.h 里没有） */
%(probe_includes)s

#include "ecos_fused_body.h"
%(xdom_include)s

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

%(probe_decls)s
%(xdom_shift_decls)s
static enum hrtimer_restart ecos_tick(struct hrtimer *t)
{
	struct ecos_group *g = container_of(t, struct ecos_group, timer);

	/* 定时器里只计数 + 唤醒；业务逻辑留给线程，避免在硬中断上下文跑控制律。
	 * atomic_inc 保证"落后时的多次释放"一次不丢——这正是严格配对实验要看的。 */
	atomic_inc(&g->releases);
	wake_up_interruptible(&g->wait);
%(xdom_tick_shift)s	return HRTIMER_RESTART;
}

static void ecos_run_body(int id)
{
	switch (id) {
%(body_cases)s
	}
	if (id == 0)
		periods++;
%(digest_update)s%(xdom_report)s
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
%(xdom_anchor)s%(probe_before)s
		ecos_run_body(g->id);
%(probe_after)s
	}
	return 0;
}

static int __init ecos_init(void)
{
	int i;
	s64 t0;
%(xdom_init)s
	if (cpu >= 0 && !cpu_online(cpu)) {
		pr_err("ecos: cpu %%d 不在线（看 /sys/devices/system/cpu/online）\\n", cpu);
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

	pr_info("ecos: %%d 组已启动，周期 %%u µs，FIFO 优先级 %%u，cpu %%d（证据级别 %(evidence)s）\\n",
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
%(mod)s-y := ecos_fused_linux.o ecos_fused_body.o stage_impl.o%(xdom_objs)s

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


def emit(ir, module_name, phases_known=True, xdom=False, xdom_resync=False):
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

    # ---- 死区探针（可选）----
    # 环闭合边 = 上一拍「末组结束」→ 本拍「首组开始」。
    # 融合把 plant 从 800 µs 前拉到 ~0 µs，这条边就变成跨一整拍；不融合时约 200 µs。
    probe_includes = probe_decls = probe_before = probe_after = ""
    if getattr(ir, "probe", False):
        phases = [((g.phase_us if g.phase_us is not None else 0.0), i)
                  for i, g in enumerate(ir.groups)]
        first_g = min(phases)[1]
        last_g = max(phases)[1]
        probe_includes = ("#include <linux/math64.h>\t/* div_s64，探针求均值用 */\n"
                          "\n#define ECOS_FIRST_GROUP %d\n#define ECOS_LAST_GROUP %d"
                          % (first_g, last_g))
        probe_decls = """/* 死区探针：量的是环闭合边 dz = t(本拍首组开始) − t(上拍末组结束)。
 * 每拍两次 ktime_get()（几十 ns），读数里已含这点开销。
 * 注意：首组与末组在不同内核线程时，dz_last_ns 的读写是跨线程的
 * （arm64 对齐 8 字节访问原子），只用于统量级，不做因果归因。 */
static s64 dz_last_ns;
static bool dz_have;
static s64 dz_sum_ns;
/* 对外暴露的统计量用 long：module_param 只支持 long/ulong 这一档，
 * s64 在 64 位内核上是 long long，与 long* 不兼容（编译会 -Werror）。
 * ns 量级（≈1e6）装进 long 绰绰有余。 */
static long dz_min_ns, dz_max_ns, dz_avg_ns;
static unsigned long dz_count;
/* 诊断：末组相对**同拍首组**的滞后（多组时用它判断某个组是否没在名义相位跑）。
 * 单组时恒为 0。 */
static s64 dz_t_first;
static s64 dz_lag_sum;
static long dz_lag_avg_ns;
module_param(dz_lag_avg_ns, long, 0444);
MODULE_PARM_DESC(dz_lag_avg_ns, "末组相对同拍首组的平均滞后（ns）");

module_param(dz_min_ns, long, 0444);
MODULE_PARM_DESC(dz_min_ns, "闭合边间隔最小值（ns）");
module_param(dz_avg_ns, long, 0444);
MODULE_PARM_DESC(dz_avg_ns, "闭合边间隔均值（ns）");
module_param(dz_max_ns, long, 0444);
MODULE_PARM_DESC(dz_max_ns, "闭合边间隔最大值（ns）");
module_param(dz_count, ulong, 0444);
MODULE_PARM_DESC(dz_count, "已采样拍数");

/* 直方图：1 µs 桶 × 16384（覆盖 16.384 ms）。读的时候现算分位数，
 * 只读不改统计，所以可以反复读。 */
#define ECOS_DZ_BUCKETS 16384
static unsigned int dz_hist[ECOS_DZ_BUCKETS];

static int ecos_dz_hist_show(char *buf, const struct kernel_param *kp)
{
	/* 目标分位（千分比）：50%, 99%, 99.9% */
	static const unsigned int pct[3] = { 500, 990, 999 };
	unsigned long cum = 0;
	long found[3] = { -1, -1, -1 };
	int n = 0, i, k;

	if (!dz_count)
		return scnprintf(buf, PAGE_SIZE,
				 "count=0（还没有样本，等几拍再读）\\n");

	for (i = 0; i < ECOS_DZ_BUCKETS; i++) {
		cum += dz_hist[i];
		for (k = 0; k < 3; k++)
			if (found[k] < 0 &&
			    cum * 1000ULL >= (unsigned long long)dz_count * pct[k])
				found[k] = i;
	}

	n += scnprintf(buf + n, PAGE_SIZE - n,
		       "count=%lu bucket=1000ns min=%ldus p50=%ldus p99=%ldus "
		       "p99.9=%ldus avg=%ldus max=%ldus lag_avg=%ldus\\n",
		       dz_count, dz_min_ns / 1000, found[0], found[1], found[2],
		       dz_avg_ns / 1000, dz_max_ns / 1000, dz_lag_avg_ns / 1000);

	n += scnprintf(buf + n, PAGE_SIZE - n, "hist:");
	for (i = 0; i < ECOS_DZ_BUCKETS && n < (int)PAGE_SIZE - 40; i++)
		if (dz_hist[i])
			n += scnprintf(buf + n, PAGE_SIZE - n, " %d:%u",
				       i, dz_hist[i]);
	n += scnprintf(buf + n, PAGE_SIZE - n, "\\n");
	return n;
}

static const struct kernel_param_ops ecos_dz_hist_ops = {
	.get = ecos_dz_hist_show,
};

module_param_cb(dz_hist, &ecos_dz_hist_ops, NULL, 0444);
MODULE_PARM_DESC(dz_hist, "闭合边间隔直方图 + p50/p99/p99.9/max");"""
        probe_before = """		if (g->id == ECOS_FIRST_GROUP) {
			s64 t_now = ktime_get();
			unsigned int b;

			dz_t_first = t_now;
			if (dz_have) {
				s64 d = t_now - dz_last_ns;

				if (d > 0) {
					dz_count++;
					dz_sum_ns += d;
					dz_avg_ns = div_s64(dz_sum_ns, dz_count);
					if (dz_count == 1 || d < dz_min_ns)
						dz_min_ns = d;
					if (d > dz_max_ns)
						dz_max_ns = d;
					b = (unsigned int)div_s64(d, 1000);
					if (b < ECOS_DZ_BUCKETS)
						dz_hist[b]++;
				}
			}
		}"""
        probe_after = """		if (g->id == ECOS_LAST_GROUP) {
			s64 t_end = ktime_get();
			s64 lag = t_end - dz_t_first;

			dz_last_ns = t_end;
			dz_have = true;
			if (ECOS_LAST_GROUP != ECOS_FIRST_GROUP && lag > 0) {
				dz_lag_sum += lag;
				dz_lag_avg_ns = div_s64(dz_lag_sum, dz_count + 1);
			}
		}"""

    # ---- 周期性重同步（消费侧是内核模块时）----
    # 为什么内核侧也必须有一份：§25 只做了 NuttX 宿主，理由是"漂移让 age 单调下降"。
    # 但那是**一个方向**的结论：实测 root 侧 ch0（nuttx→root）的 age 是**上升**的
    # （588→603 µs / 60 s，+15 ppm）。方向反了以后，消费者恰好就是 root 这一侧，
    # 而它当时没有任何应用平移的代码 —— 上边界根本无从守。
    #
    # 与 NuttX 侧的差别：那边只有一个 base，直接 += d 就行；这边每组一个 hrtimer、
    # 各自 forward_now 推进。所以平移必须**广播给本拍所有组**（各用一次），
    # 否则只动一组会把组间相位差改掉 —— 那等于偷偷改了方案。
    # 各组定时器都 PINNED 到同一个核，回调串行，因此共享两个普通变量即可。
    if xdom_resync:
        xdom_shift_decls = (
            "\n/* 一次性周期伸缩：待应用位移（µs，带符号）与剩余组数。\n"
            " * 只在消费侧是内核模块时才生成；关闭时这两行与下面的代码都不存在。 */\n"
            "static int32_t xdom_shift_us;\n"
            "static int xdom_shift_left;\n")
        xdom_tick_shift = (
            "\t{\n"
            "\t\t/* 组 0 负责向桥要一次请求，并广播给本拍的全部组 */\n"
            "\t\tif (g->id == 0) {\n"
            "\t\t\tint32_t _d = ecos_xdom_resync_take();\n"
            "\n"
            "\t\t\tif (_d) {\n"
            "\t\t\t\txdom_shift_us = _d;\n"
            "\t\t\t\txdom_shift_left = ECOS_GROUPS;\n"
            "\t\t\t\tpr_info(\"xdom resync shift_us=%d left=%d n=%u\\n\",\n"
            "\t\t\t\t\t(int)_d, xdom_shift_left,\n"
            "\t\t\t\t\tecos_xdom_resync_count());\n"
            "\t\t\t}\n"
            "\t\t}\n"
            "\t\t{\n"
            "\t\t\ts64 _next = (s64)period_us;\n"
            "\n"
            "\t\t\tif (xdom_shift_left > 0) {\n"
            "\t\t\t\t_next += xdom_shift_us;\n"
            "\t\t\t\txdom_shift_left--;\n"
            "\t\t\t}\n"
            "\t\t\tif (_next < 1)\n"
            "\t\t\t\t_next = 1;   /* 契约层已禁止 |d| >= period，这里只是兜底 */\n"
            "\t\t\thrtimer_forward_now(&g->timer, ktime_set(0, _next * 1000));\n"
            "\t\t}\n"
            "\t}\n")
    else:
        xdom_shift_decls = ""
        xdom_tick_shift = \
            "\thrtimer_forward_now(&g->timer, ktime_set(0, (s64)period_us * 1000));\n"

    host = _LINUX_C % {
        "name": HOST_C, "ngroups": ngroups, "nstages": len(ir.order),
        "period": int(ir.period_us or 0), "rtprio": 99, "evidence": ir.evidence,
        "layout": layout, "group_init": group_init,
        "phase_ns": phase_ns, "body_cases": body_cases,
        "digest_decls": "\n".join(decls),
        "digest_update": ("\n".join(updates) if updates
                          else "\t/* 无观察变量 */"),
        "probe_includes": probe_includes,
        "probe_decls": probe_decls,
        "probe_before": probe_before,
        "probe_after": probe_after,
        "xdom_init": ("\t/* 跨域数据桥：先把两个环定下来（PR10-C） */\n"
                      "\tecos_xdom_init();\n" if xdom else ""),
        "xdom_include": ('#include "ecos_xdom.h"\t/* 跨域数据桥 */\n'
                         if xdom else ""),
        # root 侧的证据：以前一个都没有（只有 nuttx 侧会调 report_evidence），
        # 于是"谁消费我"这个方向的年龄在串口上根本看不到（§18.4 的边界）。
        # 这里按秒打一次，走 pr_info → dmesg。
        # 注意：这是**代入值**，不是模板本体，所以取模写单个 %（写 %% 会原样漏进 C）；
        # 前面要补一个换行——digest_update 结尾没有换行，否则会粘在同一行上。
        "xdom_report": ('\n\tif (id == 0 && periods % 1000UL == 0UL)\n'
                        '\t\tecos_xdom_report_evidence();'
                        if xdom else ""),
        # 周期锚点：**首拍真正跑起来的时刻**（µs）。不是 init 时刻——
        # init 到首拍之间还隔着建线程、起 hrtimer，差几百 µs 到几毫秒。
        # 两域的相对偏移 ρ 只能用它算（PR10-F 已经栽过"各自 init 起算"一次）。
        "xdom_anchor": ('\t\tif (unlikely(periods == 0 && g->id == 0))\n'
                        '\t\t\tpr_info("xdom anchor now_us=%u period_us=%u\\n",\n'
                        '\t\t\t\tecos_xdom_now_us(), period_us);\n'
                        if xdom else ""),
        "xdom_shift_decls": xdom_shift_decls,
        "xdom_tick_shift": xdom_tick_shift,
    }
    return {
        HOST_C: host,
        COMPAT_H: _COMPAT_STDINT,
        KBUILD: _KBUILD % {"mod": module_name,
                           "xdom_objs": " ecos_xdom.o" if xdom else ""},
        README: _README % {"name": module_name, "mod": module_name,
                           "period": int(ir.period_us or 0),
                           "digest_hint": digest_hint},
    }
