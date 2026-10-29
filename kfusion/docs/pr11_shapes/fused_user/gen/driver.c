/* 由 fusion_codegen__生成融合代码.py 生成，不要手改。
 *
 * 方案：1 组 / 3 阶段  平台 linux_userspace
 *   g0   stage_sample + stage_pid + stage_sink
 *
 * plan 模式：1 次释放/周期；flat 模式：3 次释放/周期。
 * 两者工作量相同，唯一自变量是释放次数。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <errno.h>

/* 先定桶数再包含探针头：桶太少会让所有样本挤进桶顶，p50 变成假值 */
#define PLC_PROBE_BUCKETS 1024
#include "/root/K-Fusion/kfusion/include/plc_probe__统一测量探针.h"
#include "/root/K-Fusion/kfusion/examples/ecos/mixed_pid_stateful__积分混合.c"

#define PERIOD_NS  1000000LL
#define DEADLINE_NS 1000000LL
#define STEP_NS    200000LL
#define MAX_STAGES 3

static struct plc_probe_series g_start, g_work, g_span, g_resp;
static struct plc_probe_series g_exec_stage[MAX_STAGES];
static struct plc_probe_series g_instant[MAX_STAGES];
static long long g_last_misses;

/* 采样序列摘要：每周期结束时把指定全局量揉进 FNV-1a。
 * plan / flat 两个模式的摘要必须逐位相同——两模式的阶段顺序、初始值完全相同，
 * 唯一差别是释放次数；摘要不同就说明融合改变了数据流，而不是只改变了唤醒路径。 */
static uint32_t g_digest = 2166136261u;
#define DIGEST_MIX(x) do { g_digest ^= (uint32_t)(x); g_digest *= 16777619u; } while (0)

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
static void call_stage_pid(void) { stage_pid(); }
static void call_stage_sink(void) { stage_sink(); }

struct stage_ent { const char *name; const char *series; void (*fn)(void); };
static const struct stage_ent kStages[MAX_STAGES] = {
	{ "stage_sample", "exec_stage_sample", call_stage_sample },
	{ "stage_pid", "exec_stage_pid", call_stage_pid },
	{ "stage_sink", "exec_stage_sink", call_stage_sink },
};

struct release_ent { int n; long long phase_ns; int idx[MAX_STAGES]; };

static const struct release_ent kPlan[] = {
	{ 3, 0LL, { 0, 1, 2 } },   /* g0 @ 0 µs */
};

static const struct release_ent kFlat[] = {
	{ 1, 0LL, { 0 } },   /* stage_sample @ 0 µs */
	{ 1, 200000LL, { 1 } },   /* stage_pid @ 200 µs */
	{ 1, 400000LL, { 2 } },   /* stage_sink @ 400 µs */
};

static int run_once(const char *mode, long cycles)
{
	const struct release_ent *rel;
	int nrel, i, k, j;
	struct timespec next;
	long long misses = 0, overruns = 0;

	/* 同一进程连跑 plan/flat 时必须回到编译期初值，否则 digest 不可比 */
	loop_reset();
	g_digest = 2166136261u;

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
		if (i < 3) {
			plc_probe_reset(&g_exec_stage[i], kStages[i].series);
			plc_probe_reset(&g_instant[i], kStages[i].name);
		} else {
			plc_probe_reset(&g_exec_stage[i], "unused");
			plc_probe_reset(&g_instant[i], "unused");
		}
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

			/* 第 j 次释放：用方案里的绝对相位，不是组序号×step */
			bump_ns(&due, rel[j].phase_ns);
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
				plc_probe_add(&g_instant[idx], f - r_first);
				work += f - prev;
				prev = f;
			}
			f_last = prev;
		}

		plc_probe_add(&g_work, work);		/* 真实工作量 */
		plc_probe_add(&g_span, f_last - s_first);	/* 含空等 */
		plc_probe_add(&g_resp, f_last - r_first);
		DIGEST_MIX(g_in);
		DIGEST_MIX(g_integ);
		DIGEST_MIX(g_out);
		DIGEST_MIX(g_seq);
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
	printf("# digest: %08x\n", g_digest);
	for (i = 0; i < 3; i++) {
		plc_probe_line(&g_exec_stage[i]);
		printf("# instant_%s_us: min=%.3f avg=%.3f p50=%lu max=%.3f\n",
		       kStages[i].name,
		       g_instant[i].min_ns / 1000.0,
		       (g_instant[i].n ? (g_instant[i].sum_ns / 1000.0) / g_instant[i].n : 0),
		       plc_probe_pctl(&g_instant[i], 500000),
		       g_instant[i].max_ns / 1000.0);
	}
	plc_probe_hist(&g_start);
	plc_probe_hist(&g_resp);
	g_last_misses = misses;
	return 0;
}

#ifdef __NuttX__
#include "ecos_shm_cmd.h"
#include <unistd.h>
extern void up_invalidate_dcache(uintptr_t start, uintptr_t end);
extern void up_clean_dcache(uintptr_t start, uintptr_t end);

static int shmwait(void)
{
	volatile uint32_t *shm = (volatile uint32_t *)(ECOS_SHM_BASE + ECOS_SHM_OFF);
	uintptr_t base = (uintptr_t)shm;
	uintptr_t end = base + ECOS_SHM_WORDS * 4;
	printf("# shmwait: polling IVSHMEM @ 0x%08x\n",
	       (unsigned)(ECOS_SHM_BASE + ECOS_SHM_OFF));
	for (;;) {
		uint32_t mode_id, cycles;
		const char *mode;
		int rc;

		up_invalidate_dcache(base, end);
		if (shm[ECOS_W_MAGIC] != ECOS_SHM_MAGIC ||
		    shm[ECOS_W_CMD] != ECOS_CMD_RUN) {
			usleep(1000);
			continue;
		}
		mode_id = shm[ECOS_W_MODE];
		cycles = shm[ECOS_W_CYCLES];
		shm[ECOS_W_CMD] = ECOS_CMD_IDLE;
		shm[ECOS_W_STATUS] = ECOS_ST_RUNNING;
		up_clean_dcache(base, end);
		mode = (mode_id == ECOS_MODE_FLAT) ? "flat" : "plan";
		if (cycles == 0 || cycles > 3600000u)
			cycles = 1000;
		rc = run_once(mode, (long)cycles);
		shm[ECOS_W_DIGEST] = 0;
		shm[ECOS_W_DIGEST] = g_digest;
		shm[ECOS_W_RESP_P50] = (uint32_t)plc_probe_pctl(&g_resp, 500000);
		shm[ECOS_W_RESP_MAX] = (uint32_t)(g_resp.max_ns / 1000ull);
		shm[ECOS_W_START_P50] = (uint32_t)plc_probe_pctl(&g_start, 500000);
		shm[ECOS_W_MISS] = (uint32_t)g_last_misses;
		shm[ECOS_W_EXEC_P50] = (uint32_t)plc_probe_pctl(&g_work, 500000);
		shm[ECOS_W_NREL] = (mode_id == ECOS_MODE_FLAT) ? MAX_STAGES : (int)(sizeof(kPlan)/sizeof(kPlan[0]));
		shm[ECOS_W_STATUS] = rc ? ECOS_ST_ERROR : ECOS_ST_DONE;
		up_clean_dcache(base, end);
	}
	return 0;
}
#endif

int main(int argc, char **argv)
{
	const char *mode = argc > 1 ? argv[1] : "plan";
	long cycles = argc > 2 ? atol(argv[2]) : 1L;
	if (strcmp(mode, "shmwait") == 0) {
#ifdef __NuttX__
		return shmwait();
#else
		fprintf(stderr, "shmwait 只在 NuttX inmate 上有意义\n");
		return 2;
#endif
	}
	return run_once(mode, cycles);
}
