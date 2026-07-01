/*
 * plc_multitask_demo__主程序.c — 多任务优先级调度 PLCFusion 综合演示
 *
 * 展示：main 入口、多 TU、hrtimer 周期、pthread 监视器、mutex、
 *       double/float 定点 Pass、malloc 桩、协作式优先级调度表。
 *
 * 内核化:
 *   bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_plc_multitask__多任务优先级.env
 *   bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_multitask__多任务优先级.env
 *   sudo insmod test/plc_multitask_mod.ko
 */
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#include "plc_multitask__多任务.h"
#include "plc_fixed__定点Q.h"

volatile int shutdown;
unsigned long g_mt_tick;
pthread_mutex_t g_state_lock;

extern void plc_fused_stats_tick(void);

#ifndef __KERNEL__
void plc_fused_stats_tick(void) { }
#endif

static void on_signal(int sig)
{
	(void)sig;
	shutdown = 1;
}

int main(void)
{
	struct timespec period = { 0, MT_LOOP_MS * 1000000L };
	pthread_t wd;
	unsigned long loop = 0;

	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);

	pthread_mutex_init(&g_state_lock, NULL);
	mt_scheduler_init();

	if (pthread_create(&wd, NULL, watchdog_thread, NULL) != 0) {
		printf("mt: pthread_create watchdog failed\n");
		return 1;
	}

	printf("mt: start %d tasks prio-sched + watchdog (loops=%d)\n",
	       MT_TASK_COUNT, MT_RUN_LOOPS);

	while (!shutdown && loop < (unsigned long)MT_RUN_LOOPS) {
		g_mt_tick = loop;
		mt_scheduler_tick((unsigned)loop);
		plc_fused_stats_tick();
		nanosleep(&period, NULL);
		loop++;
	}

	shutdown = 1;
	pthread_join(wd, NULL);

	printf("mt: exit tick=%lu task_runs=%lu alarm=%d temp=%d pid=%d\n",
	       g_mt_tick, g_mt_task_runs, g_alarm_active,
	       plc_fix32_to_int(g_fused_temp), plc_fix32_to_int(g_pid_output));
	return 0;
}
