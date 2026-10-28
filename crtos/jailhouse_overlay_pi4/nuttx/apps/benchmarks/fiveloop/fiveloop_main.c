/* 由 fusion_codegen__生成融合代码.py 生成，不要手改。
 *
 * 方案：1 组 / 5 阶段  平台 linux_kernel
 *   g0   stage_sample + stage_fir + stage_pid + stage_clamp + stage_plant
 *
 * plan 模式：1 次释放/周期；flat 模式：5 次释放/周期。
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
#include "plc_probe__统一测量探针.h"
#include "five_stage_loop.c"

#define PERIOD_NS  1000000LL
#define DEADLINE_NS 1000000LL
#define STEP_NS    200000LL
#define MAX_STAGES 5

static struct plc_probe_series g_start, g_work, g_span, g_resp;
static struct plc_probe_series g_exec_stage[MAX_STAGES];
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
static void call_stage_fir(void) { stage_fir(); }
static void call_stage_pid(void) { stage_pid(1000); }
static void call_stage_clamp(void) { stage_clamp(); }
static void call_stage_plant(void) { stage_plant(); }

struct stage_ent { const char *name; const char *series; void (*fn)(void); };
static const struct stage_ent kStages[MAX_STAGES] = {
	{ "stage_sample", "exec_stage_sample", call_stage_sample },
	{ "stage_fir", "exec_stage_fir", call_stage_fir },
	{ "stage_pid", "exec_stage_pid", call_stage_pid },
	{ "stage_clamp", "exec_stage_clamp", call_stage_clamp },
	{ "stage_plant", "exec_stage_plant", call_stage_plant },
};

struct release_ent { int n; int idx[MAX_STAGES]; };

static const struct release_ent kPlan[] = {
	{ 5, { 0, 1, 2, 3, 4 } },   /* g0 */
};

static const struct release_ent kFlat[] = {
	{ 1, { 0 } },   /* stage_sample */
	{ 1, { 1 } },   /* stage_fir */
	{ 1, { 2 } },   /* stage_pid */
	{ 1, { 3 } },   /* stage_clamp */
	{ 1, { 4 } },   /* stage_plant */
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
		if (i < 5)
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
		DIGEST_MIX(g_out);
		DIGEST_MIX(g_plant);
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
	for (i = 0; i < 5; i++)
		plc_probe_line(&g_exec_stage[i]);
	plc_probe_hist(&g_start);
	plc_probe_hist(&g_resp);
	g_last_misses = misses;
	return 0;
}

#ifdef __NuttX__
#include "ecos_shm_cmd.h"
#include <unistd.h>
#include <nuttx/cache.h>

static int shmwait(void)
{
	volatile uint32_t *shm = (volatile uint32_t *)(ECOS_SHM_BASE + ECOS_SHM_OFF);
	uint32_t last_cmd_seq = 0;
	printf("# shmwait: polling IVSHMEM @ 0x%08x\n",
	       (unsigned)(ECOS_SHM_BASE + ECOS_SHM_OFF));
	/* 心跳：Linux 看不到这一字，说明 init 没跑到这儿（或这页没映射） */
	shm[ECOS_W_ALIVE] = ECOS_ALIVE;
	shm[ECOS_W_STATUS] = ECOS_ST_IDLE;
	up_clean_dcache_all();
	for (;;) {
		uint32_t mode_id, cycles, cmd_seq;
		const char *mode;
		int rc;

		up_invalidate_dcache_all();
		if (shm[ECOS_W_MAGIC] != ECOS_SHM_MAGIC ||
		    shm[ECOS_W_CMD] != ECOS_CMD_RUN) {
			usleep(1000);
			continue;
		}
		cmd_seq = shm[ECOS_W_CMD_SEQ];
		if (cmd_seq == 0 || cmd_seq == last_cmd_seq) {
			shm[ECOS_W_ERROR] = 1; /* duplicate or invalid command */
			shm[ECOS_W_CMD] = ECOS_CMD_IDLE;
			up_clean_dcache_all();
			continue;
		}
		last_cmd_seq = cmd_seq;
		mode_id = shm[ECOS_W_MODE];
		cycles = shm[ECOS_W_CYCLES];
		shm[ECOS_W_CMD] = ECOS_CMD_IDLE;
		shm[ECOS_W_STATUS] = ECOS_ST_RUNNING;
		up_clean_dcache_all();
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
		shm[ECOS_W_ERROR] = rc ? 2 : 0;
		/* 结果字段已经全部可见后，发布本次序号；Linux 以此配对。 */
		shm[ECOS_W_RESULT_SEQ] = cmd_seq;
		shm[ECOS_W_STATUS] = rc ? ECOS_ST_ERROR : ECOS_ST_DONE;
		shm[ECOS_W_ALIVE] = ECOS_ALIVE;
		up_clean_dcache_all();
	}
	return 0;
}
#endif

int fiveloop_main(int argc, char **argv)
{
	const char *mode = argc > 1 ? argv[1] : "plan";
	long cycles = argc > 2 ? atol(argv[2]) : 60000L;
#ifdef __NuttX__
	/* init 的 argv[0] 可能是 shmwait；无参数时也进命令循环（无串口） */
	if (argc < 2 || strcmp(mode, "shmwait") == 0 ||
	    (argv[0] && strcmp(argv[0], "shmwait") == 0))
		return shmwait();
#else
	if (strcmp(mode, "shmwait") == 0) {
		fprintf(stderr, "shmwait 只在 NuttX inmate 上有意义\n");
		return 2;
	}
#endif
	return run_once(mode, cycles);
}
