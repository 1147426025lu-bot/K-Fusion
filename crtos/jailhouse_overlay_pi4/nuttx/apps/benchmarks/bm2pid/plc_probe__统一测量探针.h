/*
 * plc_probe__统一测量探针.h — 跨域统一测量契约（r / s / f 三时间戳）
 *
 * 为什么需要它：
 *   2026-10-22 的审查指出，项目里同时存在三套探针、记录的量不同：
 *     P1 Linux fused 宿主  记录「间隔偏差」 d_k = t_k − t_{k−1} − T
 *     P2 cyclictest        记录「唤醒延迟」 s_k − r_k
 *     P3 BM2               记录「唤醒延迟」+ 执行时间
 *   把 P1 的极值与 P2 的主体值相除，就得到了不成立的「1.6×」。
 *   本头把「每个周期三个时间戳」定为唯一契约，三条派生序列由它算出：
 *
 *     r_k  标称释放时刻（absolute deadline schedule 里那一个 next）
 *     s_k  实际开始时刻（唤醒后第一次 clock_gettime）
 *     f_k  完成时刻（scan/周期体跑完）
 *
 *     start_lat = s_k − r_k     唤醒延迟（P2 的循环时间；旧称 jitter）
 *     exec      = f_k − s_k     执行时间
 *     response  = f_k − r_k     端到端响应（ECoS 目标函数 J(θ) 要的 R）
 *     miss      = f_k > r_k + T 完成期限错过
 *
 * 用法（只依赖 <stdio.h>，static inline 不引入 .c，不动物建系统）：
 *   static struct plc_probe_series g_start, g_exec, g_resp;
 *   plc_probe_reset(&g_start, "start_lat");
 *   ...
 *   plc_probe_add(&g_start, s_ns - r_ns);
 *   ...
 *   plc_probe_head(PERIOD_NS);  plc_probe_line(&g_start); ...
 *
 * ⚠️ 桶顶 199 µs：超过的样本计入 over，不再进入直方图。
 *    p99.99 等分位数在 over>0 时是**下界**，报数时必须同时给出 over。
 */
#ifndef PLC_PROBE__UNIFIED_H
#define PLC_PROBE__UNIFIED_H

#include <stdio.h>

#define PLC_PROBE_BUCKETS 200        /* 0..199 µs，1 µs 桶 */
#define PLC_PROBE_VER     "r/s/f v1"

struct plc_probe_series {
	const char        *name;         /* 序列名，打印用 */
	unsigned long      hist[PLC_PROBE_BUCKETS];
	unsigned long long n;            /* 样本数 */
	unsigned long long sum_ns;
	unsigned long long min_ns;
	unsigned long long max_ns;
	unsigned long long over;         /* >= PLC_PROBE_BUCKETS µs 的样本数 */
};

static inline void plc_probe_reset(struct plc_probe_series *s, const char *name)
{
	int i;

	s->name = name;
	for (i = 0; i < PLC_PROBE_BUCKETS; i++)
		s->hist[i] = 0;
	s->n = 0;
	s->sum_ns = 0;
	s->min_ns = ~0ULL;
	s->max_ns = 0;
	s->over = 0;
}

static inline void plc_probe_add(struct plc_probe_series *s, long long v_ns)
{
	unsigned long long a = (unsigned long long)(v_ns < 0 ? -v_ns : v_ns);
	unsigned long us = (unsigned long)(a / 1000ULL);

	if (us >= PLC_PROBE_BUCKETS) {
		s->over++;
		us = PLC_PROBE_BUCKETS - 1;
	}
	s->hist[us]++;
	s->n++;
	s->sum_ns += a;
	if (a < s->min_ns)
		s->min_ns = a;
	if (a > s->max_ns)
		s->max_ns = a;
}

/* ppm: 500000 = p50, 990000 = p99, 999000 = p99.9, 999900 = p99.99 */
static inline unsigned long plc_probe_pctl(const struct plc_probe_series *s,
					   unsigned long ppm)
{
	unsigned long long target, c = 0;
	unsigned long k;

	if (s->n == 0)
		return 0;
	target = (s->n * (unsigned long long)ppm + 999999ULL) / 1000000ULL;
	for (k = 0; k < PLC_PROBE_BUCKETS; k++) {
		c += s->hist[k];
		if (c >= target)
			return k;
	}
	return PLC_PROBE_BUCKETS - 1;
}

static inline void plc_probe_head(long long period_ns,
				  unsigned long long samples)
{
	printf("\n# probe: %s\n", PLC_PROBE_VER);
	printf("# period_ns: %lld\n", period_ns);
	printf("# samples: %llu\n", samples);
	printf("# series_us: start_lat exec response  (1us buckets, cap %d)\n",
	       PLC_PROBE_BUCKETS - 1);
}

static inline void plc_probe_line(const struct plc_probe_series *s)
{
	unsigned long long n = s->n ? s->n : 1;

	/* 前缀用 stat_，避免与旧版兼容行 `# exec_us: avg=…` 撞名 */
	printf("# stat_%s_us: min=%.3f avg=%.3f p50=%lu p90=%lu p99=%lu "
	       "p99.9=%lu p99.99=%lu max=%.3f over=%llu n=%llu\n",
	       s->name,
	       (double)(s->min_ns == ~0ULL ? 0 : s->min_ns) / 1000.0,
	       (double)s->sum_ns / (double)n / 1000.0,
	       plc_probe_pctl(s, 500000), plc_probe_pctl(s, 900000),
	       plc_probe_pctl(s, 990000), plc_probe_pctl(s, 999000),
	       plc_probe_pctl(s, 999900),
	       (double)s->max_ns / 1000.0, s->over, s->n);
}

/* 稀疏直方图：每个非零桶一行。为什么要单独一条：
 *   `# stat_*` 只有 6 个分位点，画不出 CDF；而 CDF 是「重尾 vs 确定性」最直观的证据。
 *   一桶一行而不是一行打全部，是因为内核 pr_info 单行有长度上限，长尾会被截断
 *   （截断后的 CDF 看起来会很漂亮——这正是不能接受的地方）。 */
static inline void plc_probe_hist(const struct plc_probe_series *s)
{
	int k;

	for (k = 0; k < PLC_PROBE_BUCKETS; k++) {
		if (s->hist[k])
			printf("# hist_%s_us: %d %lu\n", s->name, k, s->hist[k]);
	}
	if (s->over)
		printf("# hist_%s_us: over %llu\n", s->name, s->over);
}

#endif /* PLC_PROBE__UNIFIED_H */
