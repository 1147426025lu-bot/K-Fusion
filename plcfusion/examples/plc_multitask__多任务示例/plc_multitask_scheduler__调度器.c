/*
 * plc_multitask_scheduler__调度器.c — STRICT LET job 表与运行入口
 */
#include <stdio.h>

#include "plc_multitask__多任务.h"

struct plc_let_runtime g_mt_let;

static void let_wrap_sensor(void *ctx)
{
	(void)ctx;
	task_sensor_fusion();
	g_mt_task_runs++;
}

static void let_wrap_pid(void *ctx)
{
	(void)ctx;
	task_pid_control();
	g_mt_task_runs++;
}

static void let_wrap_alarm(void *ctx)
{
	(void)ctx;
	task_alarm_log();
	g_mt_task_runs++;
}

static void let_wrap_stats(void *ctx)
{
	(void)ctx;
	task_stats_heap();
	g_mt_task_runs++;
}

static void let_wrap_watchdog(void *ctx)
{
	(void)ctx;
	task_watchdog_poll();
}

static const struct plc_let_job mt_let_jobs[MT_LET_JOB_COUNT] = {
	{
		.name = "supervisor",
		.fn = mt_supervisor_job,
		.ctx = NULL,
		.period_ns = MT_MS_TO_NS(MT_LOOP_MS),
		.phase_ns = 0,
		.let_ns = MT_MS_TO_NS(MT_LOOP_MS * 8 / 10),
		.prio = 7,
		.flags = 0,
	},
	{
		.name = "sensor_fusion",
		.fn = let_wrap_sensor,
		.ctx = NULL,
		.period_ns = MT_MS_TO_NS(10),
		.phase_ns = 0,
		.let_ns = MT_MS_TO_NS(3),
		.prio = 6,
		.flags = PLC_LET_SKIP_MISSED,
	},
	{
		.name = "pid_control",
		.fn = let_wrap_pid,
		.ctx = NULL,
		.period_ns = MT_MS_TO_NS(20),
		.phase_ns = MT_MS_TO_NS(5),
		.let_ns = MT_MS_TO_NS(5),
		.prio = 5,
		.flags = PLC_LET_SKIP_MISSED,
	},
	{
		.name = "watchdog",
		.fn = let_wrap_watchdog,
		.ctx = NULL,
		.period_ns = MT_MS_TO_NS(5),
		.phase_ns = 0,
		.let_ns = MT_MS_TO_NS(2),
		.prio = 4,
		.flags = 0,
	},
	{
		.name = "alarm_log",
		.fn = let_wrap_alarm,
		.ctx = NULL,
		.period_ns = MT_MS_TO_NS(100),
		.phase_ns = 0,
		.let_ns = MT_MS_TO_NS(15),
		.prio = 3,
		.flags = PLC_LET_SKIP_MISSED,
	},
	{
		.name = "stats_heap",
		.fn = let_wrap_stats,
		.ctx = NULL,
		.period_ns = MT_MS_TO_NS(500),
		.phase_ns = 0,
		.let_ns = MT_MS_TO_NS(30),
		.prio = 1,
		.flags = PLC_LET_SKIP_MISSED,
	},
};

int mt_let_setup(void)
{
	mt_state_init();
	if (plc_let_init(&g_mt_let, mt_let_jobs, MT_LET_JOB_COUNT,
			  PLC_LET_MODE_STRICT) != 0) {
		printf("mt: plc_let_init failed\n");
		return -1;
	}
	return 0;
}

int mt_let_run(void)
{
	return plc_let_run(&g_mt_let);
}
