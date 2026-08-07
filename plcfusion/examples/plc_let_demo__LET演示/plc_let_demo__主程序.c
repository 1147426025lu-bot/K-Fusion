/*
 * plc_let_demo__主程序.c — 单 job STRICT LET 最小示例（P0）
 *
 * 用法:
 *   gcc -O2 -I../../include examples/plc_let_demo__LET演示/plc_let_demo__主程序.c \
 *       src/runtime/plc_let__LET.c -o /tmp/plc_let_demo && LET_RUN_LOOPS=500 /tmp/plc_let_demo
 */
#include <signal.h>
#include <stdio.h>
#include <stdint.h>

#include "plc_let__LET.h"

volatile int shutdown;

#ifndef LET_RUN_LOOPS
#define LET_RUN_LOOPS 500
#endif

#ifndef LET_PERIOD_MS
#define LET_PERIOD_MS 1
#endif

static unsigned long g_loop;

static void scan_job(void *ctx)
{
	(void)ctx;
	g_loop++;
#if LET_RUN_LOOPS > 0
	if (g_loop >= (unsigned long)LET_RUN_LOOPS)
		shutdown = 1;
#endif
}

static const struct plc_let_job g_jobs[] = {
	{
		.name = "scan",
		.fn = scan_job,
		.ctx = NULL,
		.period_ns = (uint64_t)LET_PERIOD_MS * 1000000ULL,
		.phase_ns = 0,
		.let_ns = (uint64_t)(LET_PERIOD_MS * 1000000ULL * 8 / 10),
		.prio = 1,
		.flags = 0,
	},
};

static void on_signal(int sig)
{
	(void)sig;
	shutdown = 1;
}

int main(void)
{
	struct plc_let_runtime rt;
	struct plc_let_stats st;

	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);

	if (plc_let_init(&rt, g_jobs, 1, PLC_LET_MODE_STRICT) != 0)
		return 1;

	printf("let: start 1 job period=%dms loops=%s\n", LET_PERIOD_MS,
#if LET_RUN_LOOPS > 0
	       "fixed");
#else
	       "until-shutdown");
#endif

	plc_let_run(&rt);

	plc_let_get_stats(&rt, 0, &st);
	printf("let: exit loops=%lu releases=%llu jitter_max_ns=%lld\n",
	       g_loop, (unsigned long long)st.releases,
	       (long long)st.release_jitter_max_ns);
	plc_let_print_summary(stdout, &rt, "app", 0);
	return 0;
}
