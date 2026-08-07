/*
 * plc_multitask_demo__主程序.c — STRICT LET 多 job PLCFusion 演示
 *
 * 6 个 LET job（监督 1ms + 4 控制任务 + 看门狗）在单线程内按逻辑 release 调度。
 * 论文测量: MT_RUN_LOOPS=0 时长由测量脚本控制；打印 MtSummary + LetSummary。
 */
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>

#include "plc_multitask__多任务.h"
#include "plc_fixed__定点Q.h"

volatile int shutdown;
unsigned long g_mt_tick;
pthread_mutex_t g_state_lock;

extern void plc_fused_stats_tick(void);

#ifndef MT_PAPER_BASELINE
#if defined(PLC_FUSED_BUILD)
#define MT_PAPER_BASELINE "fused"
#else
#define MT_PAPER_BASELINE "app"
#endif
#endif

struct mt_period_stats {
	long long min_ns;
	long long max_ns;
	long long abs_max_ns;
	unsigned long cycles;
};

struct mt_period_stats g_mt_period;

static unsigned long g_supervisor_loop;

static void mt_sync_period_from_supervisor(void)
{
	struct plc_let_stats st;
	int idx;

	idx = plc_let_find_job(&g_mt_let, "supervisor");
	if (idx < 0)
		return;
	plc_let_get_stats(&g_mt_let, (unsigned)idx, &st);
	g_mt_period.abs_max_ns = st.release_jitter_max_ns;
	g_mt_period.min_ns = -st.release_jitter_max_ns;
	g_mt_period.max_ns = st.release_jitter_max_ns;
	g_mt_period.cycles = (unsigned long)st.releases;
}

static void mt_print_summary(int exit_code)
{
	mt_sync_period_from_supervisor();
	printf("MtSummary: baseline=%s abs_max_ns=%lld min_ns=%lld max_ns=%lld cycles=%lu "
	       "task_runs=%lu alarm_edges=%lu jobs=%d let=strict exit=%d\n",
	       MT_PAPER_BASELINE,
	       (long long)g_mt_period.abs_max_ns,
	       g_mt_period.min_ns, g_mt_period.max_ns,
	       g_mt_period.cycles, g_mt_task_runs, g_alarm_edges,
	       MT_LET_JOB_COUNT, exit_code);
	fflush(stdout);
	plc_let_print_summary(stdout, &g_mt_let, MT_PAPER_BASELINE, exit_code);
}

static void on_signal(int sig)
{
	(void)sig;
	shutdown = 1;
}

void mt_supervisor_job(void *ctx)
{
	(void)ctx;

	g_mt_tick = g_supervisor_loop;
	plc_fused_stats_tick();
	g_supervisor_loop++;
#if MT_RUN_LOOPS > 0
	if (g_supervisor_loop >= (unsigned long)MT_RUN_LOOPS)
		shutdown = 1;
#endif
}

int main(void)
{
	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);

	pthread_mutex_init(&g_state_lock, NULL);

	if (mt_let_setup() != 0) {
		mt_print_summary(1);
		return 1;
	}

	printf("mt: start STRICT LET %d jobs (loops=%s)\n", MT_LET_JOB_COUNT,
#if MT_RUN_LOOPS > 0
	       "fixed");
#else
	       "until-shutdown");
#endif

	if (mt_let_run() != 0 && !shutdown) {
		mt_print_summary(1);
		return 1;
	}

	printf("mt: exit tick=%lu task_runs=%lu alarm=%d temp=%d pid=%d\n",
	       g_mt_tick, g_mt_task_runs, g_alarm_active,
	       plc_fix32_to_int(g_fused_temp), plc_fix32_to_int(g_pid_output));
	mt_print_summary(0);
	return 0;
}
