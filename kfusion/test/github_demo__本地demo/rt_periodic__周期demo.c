/*
 * rt_periodic.c — 1ms 周期循环（自包含，便于 PLCFusion 内核化试跑）
 * 模式类似 GitHub 上常见的 RT micro-benchmark / tick 程序。
 */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

volatile int shutdown;

extern long rt_periodic_record_worst(long prev, long cur);
extern void plc_fused_stats_tick(void);

static void on_signal(int sig)
{
	(void)sig;
	shutdown = 1;
}

int main(void)
{
	struct timespec period = { .tv_sec = 0, .tv_nsec = 1000000 };
	struct timespec prev, now;
	long loops = 0;
	long worst_ns = 0;

	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);

	clock_gettime(CLOCK_MONOTONIC, &prev);
	printf("rt_periodic: start 1ms loop (Ctrl+C to stop)\n");

	while (!shutdown) {
		long delta_ns;

		nanosleep(&period, NULL);
		clock_gettime(CLOCK_MONOTONIC, &now);
		delta_ns = (now.tv_sec - prev.tv_sec) * 1000000000L +
			   (now.tv_nsec - prev.tv_nsec);
		prev = now;
		worst_ns = rt_periodic_record_worst(worst_ns, delta_ns);
		loops++;
		plc_fused_stats_tick();
		if ((loops % 1000) == 0)
			printf("rt_periodic: loops=%ld worst=%ld ns\n",
			       loops, worst_ns);
	}

	printf("rt_periodic: exit loops=%ld worst=%ld ns\n", loops, worst_ns);
	return 0;
}
