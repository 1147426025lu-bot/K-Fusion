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

/* 跨域统一测量契约（r/s/f 三时间戳 → start_lat / exec / response 三序列）
 * 契约定义与派生公式见同目录 plc_probe__统一测量探针.h */
#include "plc_probe__统一测量探针.h"

/* 三时间戳与三条派生序列：
 *   r_k = 标称释放（本周期 next）      s_k = 唤醒后第一次取时      f_k = 周期体跑完
 *   start_lat = s−r    exec = f−s    response = f−r
 */
static struct plc_probe_series g_p_start;
static struct plc_probe_series g_p_exec;
static struct plc_probe_series g_p_resp;

static long long     g_cycles;
static long long     g_overruns;      /* exec_ns > PERIOD_NS（本周期算得比周期还长） */
static long long     g_dl_miss;       /* f_k > r_k + D（真正的完成期限错过） */
/* 相对期限 D。ECoS 的契约里 deadline_us 是独立字段，不能默认等于周期。
 * 0 表示未指定 → 沿用周期（与旧行为一致）。 */
static long long     g_deadline_ns;
static long long     g_exec_max_ns;
static long long     g_exec_sum_ns;

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

static long long ts_ns(const struct timespec *t)
{
	return (long long)t->tv_sec * 1000000000LL + t->tv_nsec;
}

static int run(long cycles)
{
	struct timespec next, now;
	long long r_ns, s_ns, f_ns, exec_ns;
	long long i;

	plc_probe_reset(&g_p_start, "start_lat");
	plc_probe_reset(&g_p_exec, "exec");
	plc_probe_reset(&g_p_resp, "response");

	if (clock_gettime(CLOCK_MONOTONIC, &next) != 0)
		return -1;

	for (i = 0; i < cycles; i++) {
		next.tv_nsec += (long)(PERIOD_NS % 1000000000LL);
		if (next.tv_nsec >= 1000000000L) {
			next.tv_sec += 1;
			next.tv_nsec -= 1000000000L;
		}

		r_ns = ts_ns(&next);          /* 标称释放时刻（不随实际唤醒漂移） */

		if (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) != 0)
			return -1;

		if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
			return -1;
		s_ns = ts_ns(&now);

		plc_probe_add(&g_p_start, s_ns - r_ns);   /* start_lat = s − r */

		scan_cycle(1.0e-3);

		if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
			return -1;
		exec_ns = ts_ns(&now) - s_ns;
		f_ns = s_ns + exec_ns;

		plc_probe_add(&g_p_exec, exec_ns);        /* exec     = f − s */
		plc_probe_add(&g_p_resp, f_ns - r_ns);    /* response = f − r */

		g_exec_sum_ns += exec_ns;
		if (exec_ns > g_exec_max_ns)
			g_exec_max_ns = exec_ns;
		/* exec > period 只能说明"这一周期算得久"，不代表错过完成期限。
		 * 真正的 deadline miss 是 f_k > r_k + T（晚醒 900 µs + 执行 200 µs
		 * 的执行时间没超周期，但本周期已经赶不上交付）。 */
		if (exec_ns > PERIOD_NS)
			g_overruns++;
		/* 用独立期限 D 而非周期 T：D < T 时会更早判违约，D > T 时更宽松。
		 * 这与契约里的 task.deadline_us 对齐。 */
		if (f_ns > r_ns + (g_deadline_ns > 0 ? g_deadline_ns : PERIOD_NS))
			g_dl_miss++;

		g_cycles++;
	}

	return 0;
}

int main(int argc, char *argv[])
{
	long cycles = 100000;   /* 100 s @1kHz，与 cyclictest 的 -l 100000 对齐 */
	int opt;

	while ((opt = getopt(argc, argv, "l:d:v")) != -1) {
		switch (opt) {
		case 'l':
			cycles = strtol(optarg, NULL, 10);
			break;
		case 'd':
			/* 相对期限（µs）。0 = 沿用周期，与旧行为一致 */
			g_deadline_ns = strtol(optarg, NULL, 10) * 1000LL;
			break;
		case 'v':
			printf("bm2pid: %d axes, cascade PID, FIR %d taps, period %lld ns\n",
			       N_AXES, FIR_TAPS, PERIOD_NS);
			break;
		default:
			printf("usage: bm2pid [-l cycles] [-d deadline_us] [-v]\n");
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
	       (long long)(g_p_start.min_ns == ~0ULL ? 0 : g_p_start.min_ns) / 1000);
	printf("# Avg Latencies: %08lld\n",
	       g_cycles ? (long long)(g_p_start.sum_ns / (unsigned long long)g_cycles) / 1000 : 0);
	printf("# Max Latencies: %08lld\n", (long long)g_p_start.max_ns / 1000);
	printf("# Histogram Overflows: %08lld\n", (long long)g_p_start.over);
	printf("# Deadline Misses: %08lld\n", g_dl_miss);
	printf("# Histogram\n");
	{
		long b;
		for (b = 0; b < HIST_BUCKETS; b++)
			printf("%06ld %06lu\n", b, g_p_start.hist[b]);
	}

	/* --- 统一探针块（跨域可比的口径，见 plc_probe__统一测量探针.h）--- */
	plc_probe_head(PERIOD_NS, g_deadline_ns, (unsigned long long)g_cycles);
	plc_probe_line(&g_p_start);
	plc_probe_line(&g_p_exec);
	plc_probe_line(&g_p_resp);
	printf("# deadline_miss: %lld/%lld (%s)\n", g_dl_miss, g_cycles,
	       g_dl_miss ? "FAIL" : "none");
	printf("# exec_overrun: %lld/%lld\n", g_overruns, g_cycles);

	/* --- 稀疏直方图（画 CDF 用；`# stat_*` 的 6 个分位点不够）--- */
	plc_probe_hist(&g_p_start);
	plc_probe_hist(&g_p_exec);
	plc_probe_hist(&g_p_resp);

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
