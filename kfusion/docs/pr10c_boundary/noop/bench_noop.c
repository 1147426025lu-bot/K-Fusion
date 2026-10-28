/* bench_noop.c — 与真实驱动同构：每拍一次 ioctl，但不跑任何阶段。
 * 打印与 plc_probe 一致的 stat 行，便于与 mixed 日志并列比较。 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sched.h>
#include "kfusion_uku.h"
#include "/root/K-Fusion/kfusion/include/plc_probe__统一测量探针.h"

static int fd = -1;
static struct plc_probe_series g_rt, g_resp;

static void bump_ns(struct timespec *t, long long ns)
{
	t->tv_nsec += (long)ns;
	while (t->tv_nsec >= 1000000000L) {
		t->tv_nsec -= 1000000000L;
		t->tv_sec += 1;
	}
}

static long long now_ns(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (long long)t.tv_sec * 1000000000LL + t.tv_nsec;
}

int main(int argc, char **argv)
{
	long cycles = (argc > 1) ? atol(argv[1]) : 5000;
	long k, bad = 0, miss = 0;
	struct timespec next, res;
	struct kfusion_uku_msg msg;
	const long long period = 1000000LL, deadline = 1000000LL;

	fd = open("/dev/kfusion_noop", O_RDWR);
	if (fd < 0) { perror("/dev/kfusion_noop"); return 2; }
	plc_probe_reset(&g_rt, "ioctl_roundtrip");
	plc_probe_reset(&g_resp, "response");
	clock_gettime(CLOCK_MONOTONIC, &next);
	for (k = 0; k < cycles; k++) {
		long long r, s, f;
		memset(&msg, 0, sizeof(msg));
		msg.magic = KFUSION_UKU_MAGIC;
		msg.seq = (uint32_t)(k + 1);
		msg.n_in = 1; msg.n_out = 1;
		bump_ns(&next, period);
		if (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) != 0)
			break;
		r = (long long)next.tv_sec * 1000000000LL + next.tv_nsec;
		s = now_ns();
		if (ioctl(fd, KFUSION_UKU_IOC, &msg) != 0) { bad++; continue; }
		f = now_ns();
		plc_probe_add(&g_rt, f - s);
		plc_probe_add(&g_resp, f - r);
		if (f - r > deadline) miss++;
	}
	(void)res;
	close(fd);
	printf("\n# mode: noop  cycles=%ld\n", cycles);
	plc_probe_head(period, deadline, (unsigned long long)cycles);
	plc_probe_line(&g_rt);
	plc_probe_line(&g_resp);
	printf("# ioctl_error: %ld/%ld\n", bad, cycles);
	printf("# deadline_miss: %ld/%ld\n", miss, cycles);
	return 0;
}
