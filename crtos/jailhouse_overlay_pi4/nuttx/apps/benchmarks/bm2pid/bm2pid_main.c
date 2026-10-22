/*
 * bm2pid_main.c — BM2：三轴级联 PID 控制基准
 *
 * 用途（K-Fusion 论文 E1/E3）：
 *   1) 作为"真实控制律"负载，替代只做空转计时的 cyclictest；
 *   2) 提供第二组「依赖闭包」数据（E3）；
 *   3) 检验 FixedPoint Pass 在真实浮点控制律上的适用边界（Q32 动态范围）。
 *
 * 与 cyclictest 的口径保持一致：
 *   - 1 kHz 周期，clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME) 绝对定时；
 *   - 热点循环内**零 printf**（打印会污染被测对象，实测能把 max 从 9µs 抬到 64µs）；
 *   - 结尾输出 6 位桶号的直方图，现有解析脚本可直接吃。
 *
 * 额外输出（cyclictest 没有的）：
 *   - 每周期的**执行时间**（唤醒→算完），以及超时周期数（exec > period）；
 *   - 控制质量：各轴跟踪误差的 RMS 与最大绝对值
 *     —— 定点化若改变数值行为，这两个数会先露出来。
 *
 * 控制结构：位置环 → 速度环（级联），每轴带积分抗饱和；
 *           反馈信号先过 8 阶 FIR（模拟真实 PLC 的传感器滤波）。
 */

#ifdef __NuttX__
#include <nuttx/config.h>
#endif

#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define N_AXES      3
#define FIR_TAPS    8
#define HIST_BUCKETS 200          /* 0..199 µs，1 µs 桶 */
#define PERIOD_NS   1000000LL     /* 1 kHz */

struct pid_s {
	double kp;
	double ki;
	double kd;
	double integ;
	double prev_err;
	double lo;
	double hi;
};

struct axis_s {
	struct pid_s pos_loop;        /* 外环：位置 */
	struct pid_s vel_loop;        /* 内环：速度 */
	double pos;                   /* 被控对象状态 */
	double vel;
	double ref;                   /* 位置指令 */
	double y[FIR_TAPS];           /* 反馈 FIR 历史 */
	int    yidx;
	double err_rms_acc;
	double err_abs_max;
};

static struct axis_s g_ax[N_AXES];

/* 每周期抖动直方图（µs）与执行时间统计 */
static unsigned long g_hist[HIST_BUCKETS];
static long long     g_cycles;
static long long     g_overruns;      /* exec_ns > PERIOD_NS（本周期算得比周期还长） */
static long long     g_dl_miss;       /* finish > release + PERIOD_NS（真正的完成期限错过） */
static long long     g_overflow;      /* 抖动 >= HIST_BUCKETS µs，直方图装不下 */
static long long     g_exec_max_ns;
static long long     g_exec_sum_ns;
static long long     g_jit_sum_ns;
static long long     g_jit_max_ns;
static long long     g_jit_min_ns = -1;   /* -1 = 尚未记录 */

/* ---------- 定点友好的纯计算 ---------- */

static double pid_step(struct pid_s *p, double err, double dt)
{
	double deriv;
	double out;

	p->integ += err * dt;
	deriv = (err - p->prev_err) / dt;
	p->prev_err = err;

	out = p->kp * err + p->ki * p->integ + p->kd * deriv;

	/* 抗积分饱和：输出饱和时把积分拉回边界 */
	if (out > p->hi) {
		out = p->hi;
		if (p->ki != 0.0)
			p->integ = (out - p->kp * err - p->kd * deriv) / p->ki;
	} else if (out < p->lo) {
		out = p->lo;
		if (p->ki != 0.0)
			p->integ = (out - p->kp * err - p->kd * deriv) / p->ki;
	}
	return out;
}

static double fir_step(struct axis_s *a, double x)
{
	double acc = 0.0;
	int i;

	a->y[a->yidx] = x;
	a->yidx = (a->yidx + 1) % FIR_TAPS;

	/* 对称系数，模拟一个低通 */
	static const double c[FIR_TAPS] = {
		0.05, 0.10, 0.15, 0.20, 0.20, 0.15, 0.10, 0.05
	};

	for (i = 0; i < FIR_TAPS; i++)
		acc += c[i] * a->y[(a->yidx + i) % FIR_TAPS];

	return acc;
}

static void axis_init(struct axis_s *a, double ref)
{
	memset(a, 0, sizeof(*a));

	a->pos_loop.kp = 12.0;  a->pos_loop.ki = 0.8;  a->pos_loop.kd = 0.15;
	a->pos_loop.lo = -20.0; a->pos_loop.hi = 20.0;
	a->vel_loop.kp = 4.0;   a->vel_loop.ki = 2.5;  a->vel_loop.kd = 0.02;
	a->vel_loop.lo = -60.0; a->vel_loop.hi = 60.0;
	a->ref = ref;
}

/* 实时任务函数的链接属性。
 *
 * 默认为空：让编译器自由内联（性能最好，但函数边界消失）。
 * 定义 -DBM2_SCAN_ATTR=__attribute__((noinline)) 可让它保持独立存在，
 * 用于「WCET 冻结」对照实验 —— 冻结（optnone）必须有独立的函数边界，
 * 否则会连带冻结整个 main（实测代价 +17.6%，那是误用而非设计）。
 */

#ifndef BM2_SCAN_ATTR
#  define BM2_SCAN_ATTR
#endif

/* 一个控制周期：3 轴 × （FIR + 位置环 + 速度环 + 二阶对象积分） */
static BM2_SCAN_ATTR void scan_cycle(double dt)
{
	int i;

	for (i = 0; i < N_AXES; i++) {
		struct axis_s *a = &g_ax[i];
		double y;
		double e_pos;
		double v_ref;
		double e_vel;
		double u;
		double err;

		y = fir_step(a, a->pos);                       /* 传感器滤波 */
		e_pos = a->ref - y;
		v_ref = pid_step(&a->pos_loop, e_pos, dt);          /* 外环 */
		e_vel = v_ref - a->vel;
		u = pid_step(&a->vel_loop, e_vel, dt);              /* 内环 */

		/* 二阶被控对象（含阻尼），显式欧拉 */
		a->vel += (u - 0.35 * a->vel) * dt;
		a->pos += a->vel * dt;

		err = a->ref - a->pos;
		a->err_rms_acc += err * err;
		if (err < 0.0)
			err = -err;
		if (err > a->err_abs_max)
			a->err_abs_max = err;
	}
}

static void hist_add(long long v_ns)
{
	long us;

	if (v_ns < 0)
		v_ns = -v_ns;
	us = (long)(v_ns / 1000);
	if (us >= HIST_BUCKETS) {
		/* 真正的直方图溢出：>= 200 µs 的样本装不进表里。
		 * 旧实现把这种情况也算进 "Histogram Overflows"，但那个字段
		 * 其实打印的是 exec > period 的次数 —— 两件事被混成了一个。 */
		g_overflow++;
		us = HIST_BUCKETS - 1;
	}
	g_hist[us]++;
}

static long long ts_ns(const struct timespec *t)
{
	return (long long)t->tv_sec * 1000000000LL + t->tv_nsec;
}

static int run(long cycles)
{
	struct timespec next, now;
	long long wake_ns, exec_ns, jit_ns;
	long long i;

	if (clock_gettime(CLOCK_MONOTONIC, &next) != 0)
		return -1;

	for (i = 0; i < cycles; i++) {
		next.tv_nsec += (long)(PERIOD_NS % 1000000000LL);
		if (next.tv_nsec >= 1000000000L) {
			next.tv_sec += 1;
			next.tv_nsec -= 1000000000L;
		}

		if (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) != 0)
			return -1;

		if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
			return -1;
		wake_ns = ts_ns(&now);

		jit_ns = wake_ns - ts_ns(&next);
		hist_add(jit_ns);
		if (jit_ns > g_jit_max_ns)
			g_jit_max_ns = jit_ns;
		{
			long long a = jit_ns < 0 ? -jit_ns : jit_ns;
			g_jit_sum_ns += a;
			if (g_jit_min_ns < 0 || a < g_jit_min_ns)
				g_jit_min_ns = a;
		}

		scan_cycle(1.0e-3);

		if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
			return -1;
		exec_ns = ts_ns(&now) - wake_ns;
		g_exec_sum_ns += exec_ns;
		if (exec_ns > g_exec_max_ns)
			g_exec_max_ns = exec_ns;
		/* exec > period 只能说明"这一周期算得久"，不代表错过完成期限。
		 * 真正的 deadline miss 是 f_k > r_k + T（晚醒 900 µs + 执行 200 µs
		 * 的执行时间没超周期，但本周期已经赶不上交付）。 */
		if (exec_ns > PERIOD_NS)
			g_overruns++;
		if (wake_ns + exec_ns > ts_ns(&next) + PERIOD_NS)
			g_dl_miss++;

		g_cycles++;
	}

	return 0;
}

int main(int argc, char *argv[])
{
	long cycles = 100000;   /* 100 s @1kHz，与 cyclictest 的 -l 100000 对齐 */
	int opt;

	while ((opt = getopt(argc, argv, "l:v")) != -1) {
		switch (opt) {
		case 'l':
			cycles = strtol(optarg, NULL, 10);
			break;
		case 'v':
			printf("bm2pid: %d axes, cascade PID, FIR %d taps, period %lld ns\n",
			       N_AXES, FIR_TAPS, PERIOD_NS);
			break;
		default:
			printf("usage: bm2pid [-l cycles] [-v]\n");
			return 1;
		}
	}

	if (cycles <= 0)
		cycles = 100000;

	axis_init(&g_ax[0], 1.0);
	axis_init(&g_ax[1], -2.5);
	axis_init(&g_ax[2], 4.0);

	if (run(cycles) != 0) {
		printf("bm2pid: run failed (%d)\n", errno);
		return 1;
	}

	/* --- 与 cyclictest -q 兼容的直方图 ---
	 *
	 * 字段语义（2026-10-22 修正，此前三处都写错了）：
	 *   Min/Avg/Max Latencies : 唤醒抖动 = 实际唤醒 − 标称释放时刻（µs）
	 *   Histogram Overflows   : 抖动 ≥ 200 µs、直方图表装不下的样本数
	 *                           （旧实现打印的是 exec > period 的次数，两件事被混了）
	 *   Deadline Misses       : finish > release + period 的周期数
	 *                           （旧实现只有 exec > period，那不是 deadline miss）
	 *   exec_us               : 执行时间（唤醒 → 算完），与抖动是两回事
	 */
	printf("\n# Total: %08lld\n", g_cycles);
	printf("# Min Latencies: %08lld\n",
	       (g_jit_min_ns < 0 ? 0LL : g_jit_min_ns) / 1000);
	printf("# Avg Latencies: %08lld\n",
	       g_cycles ? (g_jit_sum_ns / g_cycles) / 1000 : 0);
	printf("# Max Latencies: %08lld\n", g_jit_max_ns / 1000);
	printf("# Histogram Overflows: %08lld\n", g_overflow);
	printf("# Deadline Misses: %08lld\n", g_dl_miss);
	printf("# Histogram\n");
	{
		long b;
		for (b = 0; b < HIST_BUCKETS; b++)
			printf("%06ld %06lu\n", b, g_hist[b]);
	}

	/* --- 控制质量（定点化若改变数值行为，这里会先露出来）--- */
	{
		int i;
		printf("\n# Control quality\n");
		for (i = 0; i < N_AXES; i++) {
			struct axis_s *a = &g_ax[i];
			/* 用均方误差而非 RMS：避免引入 libm 的 sqrt，保持依赖闭包干净 */
			double ms = (g_cycles > 0)
				    ? a->err_rms_acc / (double)g_cycles : 0.0;
			printf("# axis%d ref=%.3f pos=%.9f err_ms=%.6e err_max=%.6e\n",
			       i, a->ref, a->pos, ms, a->err_abs_max);
		}
		printf("# exec_us: avg=%.3f max=%.3f overruns=%lld/%lld\n",
		       g_cycles ? (double)g_exec_sum_ns / g_cycles / 1000.0 : 0.0,
		       (double)g_exec_max_ns / 1000.0, g_overruns, g_cycles);
	}

	return 0;
}
