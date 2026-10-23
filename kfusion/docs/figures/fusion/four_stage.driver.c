/* 由 fusion_codegen__生成融合代码.py 生成，不要手改。
 *
 * 方案：1 组 / 4 阶段  平台 linux_kernel
 *   g0   stage_sample + stage_fir + stage_pid + stage_clamp
 *
 * plan 模式：1 次释放/周期；flat 模式：4 次释放/周期。
 * 两者工作量相同，唯一自变量是释放次数。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <errno.h>

/* 先定桶数再包含探针头：桶太少会让所有样本挤进桶顶，p50 变成假值 */
#define PLC_PROBE_BUCKETS 1024
#include "plc_probe__统一测量探针.h"
#include "/root/K-Fusion/kfusion/examples/ecos/four_stage_chain__四阶段示例.c"

#define PERIOD_NS  1000000LL
#define DEADLINE_NS 1000000LL
#define STEP_NS    200000LL
#define MAX_STAGES 4

static struct plc_probe_series g_start, g_work, g_span, g_resp;
static struct plc_probe_series g_exec_stage[MAX_STAGES];

static long long now_ns(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (long long)t.tv_sec * 1000000000LL + t.tv_nsec;
}

static void bump_ns(struct timespec *t, long long ns)
{
	t->tv_nsec += (long)(ns % 1000000000LL);
	t->tv_sec += (time_t)(ns / 1000000000LL);
	while (t->tv_nsec >= 1000000000L) {
		t->tv_nsec -= 1000000000L;
		t->tv_sec += 1;
	}
}

/* 阶段包装：调用表达式可以带实参 */
static void call_stage_sample(void) { stage_sample(); }
static void call_stage_fir(void) { stage_fir(); }
static void call_stage_pid(void) { stage_pid(1000); }
static void call_stage_clamp(void) { stage_clamp(); }

struct stage_ent { const char *name; const char *series; void (*fn)(void); };
static const struct stage_ent kStages[MAX_STAGES] = {
	{ "stage_sample", "exec_stage_sample", call_stage_sample },
	{ "stage_fir", "exec_stage_fir", call_stage_fir },
	{ "stage_pid", "exec_stage_pid", call_stage_pid },
	{ "stage_clamp", "exec_stage_clamp", call_stage_clamp },
};

struct release_ent { int n; int idx[MAX_STAGES]; };

static const struct release_ent kPlan[] = {
	{ 4, { 0, 1, 2, 3 } },   /* g0 */
};

static const struct release_ent kFlat[] = {
	{ 1, { 0 } },   /* stage_sample */
	{ 1, { 1 } },   /* stage_fir */
	{ 1, { 2 } },   /* stage_pid */
	{ 1, { 3 } },   /* stage_clamp */
};

int main(int argc, char **argv)
{
	const char *mode = argc > 1 ? argv[1] : "plan";
	long cycles = argc > 2 ? atol(argv[2]) : 60000L;
	const struct release_ent *rel;
	int nrel, i, k, j;
	struct timespec next;
	long long misses = 0, overruns = 0;

	if (strcmp(mode, "flat") == 0) {
		rel = kFlat;
		nrel = (int)(sizeof(kFlat) / sizeof(kFlat[0]));
	} else {
		rel = kPlan;
		nrel = (int)(sizeof(kPlan) / sizeof(kPlan[0]));
	}

	plc_probe_reset(&g_start, "start_lat");
	plc_probe_reset(&g_work, "exec");
	plc_probe_reset(&g_span, "span");
	plc_probe_reset(&g_resp, "response");
	for (i = 0; i < MAX_STAGES; i++) {
		if (i < 4)
			plc_probe_reset(&g_exec_stage[i], kStages[i].series);
		else
			plc_probe_reset(&g_exec_stage[i], "unused");
	}

	if (clock_gettime(CLOCK_MONOTONIC, &next) != 0)
		return 1;

	for (k = 0; k < cycles; k++) {
		long long r_first = 0, s_first = 0, f_last = 0, work = 0;

		bump_ns(&next, PERIOD_NS);
		for (j = 0; j < nrel; j++) {
			struct timespec due = next;
			long long r, s, prev;
			int q;

			/* 第 j 次释放：按固定相位错开，保证每次释放都要真的睡一次 */
			bump_ns(&due, (long long)j * STEP_NS);
			r = (long long)due.tv_sec * 1000000000LL + due.tv_nsec;
			if (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &due, NULL) != 0)
				return 1;
			s = now_ns();
			plc_probe_add(&g_start, s - r);
			if (j == 0) { r_first = r; s_first = s; }

			prev = s;
			for (q = 0; q < rel[j].n; q++) {
				int idx = rel[j].idx[q];
				long long f;
				kStages[idx].fn();
				f = now_ns();
				plc_probe_add(&g_exec_stage[idx], f - prev);
				work += f - prev;
				prev = f;
			}
			f_last = prev;
		}

		plc_probe_add(&g_work, work);		/* 真实工作量 */
		plc_probe_add(&g_span, f_last - s_first);	/* 含空等 */
		plc_probe_add(&g_resp, f_last - r_first);
		if (f_last - r_first > DEADLINE_NS)
			misses++;
		if (work > PERIOD_NS)
			overruns++;
	}

	printf("\n# mode: %s  releases_per_period=%d  cycles=%ld\n",
	       mode, nrel, cycles);
	plc_probe_head(PERIOD_NS, DEADLINE_NS, (unsigned long long)cycles);
	plc_probe_line(&g_start);
	plc_probe_line(&g_work);
	plc_probe_line(&g_span);
	plc_probe_line(&g_resp);
	printf("# deadline_miss: %lld/%ld (%s)\n", misses, cycles,
	       misses ? "FAIL" : "none");
	printf("# exec_overrun: %lld/%ld\n", overruns, cycles);
	for (i = 0; i < 4; i++)
		plc_probe_line(&g_exec_stage[i]);
	plc_probe_hist(&g_start);
	plc_probe_hist(&g_resp);
	return 0;
}
