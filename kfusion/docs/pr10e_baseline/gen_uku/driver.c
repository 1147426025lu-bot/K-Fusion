/* UKU 混合驱动：gold=全用户态；mixed=中间组走 /dev/kfusion_uku */
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
#include "/root/K-Fusion/kfusion/include/plc_probe__统一测量探针.h"
#include "kfusion_uku.h"
#include "/root/K-Fusion/kfusion/examples/ecos/mixed_pid_stateful__积分混合.c"

#define PERIOD_NS 1000000LL
#define DEADLINE_NS PERIOD_NS
static uint32_t g_digest;
#define DIGEST_MIX(v) do { uint32_t _x=(uint32_t)(v); g_digest^=_x; g_digest*=16777619u; } while (0)

static long long now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}
static void bump_ns(struct timespec *t, long long ns)
{
	t->tv_nsec += ns;
	while (t->tv_nsec >= 1000000000L) { t->tv_sec++; t->tv_nsec -= 1000000000L; }
}

static int uku_fd = -1;
static int sess_val = 0, sess_ok = 0;
/* PR-10E：csv 模式逐拍输出原始样本（k,start_lat_ns,exec_ns,resp_ns），
 * 供统计基线用。默认关闭，避免污染既有聚合日志格式。 */
static int g_csv = 0;
static int do_kernel_group(uint32_t seq, long long *h_ns)
{
	struct kfusion_uku_msg msg;
	memset(&msg, 0, sizeof(msg));
	msg.magic = KFUSION_UKU_MAGIC;
	msg.seq = seq;
	msg.n_in = 1;
	msg.n_out = 1;
	msg.in[0] = g_in;
	if (ioctl(uku_fd, KFUSION_UKU_IOC, &msg) != 0)
		return errno ? -errno : -1;
	if (msg.seq != seq) {
		errno = EILSEQ;
		return -EILSEQ;
	}
	g_out = msg.out[0];
	if (h_ns)
		*h_ns = msg.k_end_ns - msg.k_start_ns;
	return 0;
}

static int run_mode(const char *mode, long cycles)
{
	int mixed = (strcmp(mode, "mixed") == 0);
	long k, miss = 0, bad_seq = 0, bad_ioctl = 0;
	struct timespec next;
	struct plc_probe_series g_start, g_work, g_resp, g_hand, g_rt;
	struct plc_probe_series g_exec_stage[3];
	loop_reset();
	g_digest = 2166136261u;
	if (mixed) {
		uku_fd = open("/dev/kfusion_uku", O_RDWR);
		if (uku_fd < 0) { perror("/dev/kfusion_uku"); return 2; }
	}
	plc_probe_reset(&g_start, "start_lat");
	plc_probe_reset(&g_work, "exec");
	plc_probe_reset(&g_resp, "response");
	plc_probe_reset(&g_hand, "kernel_compute");
	plc_probe_reset(&g_rt, "ioctl_roundtrip");
	plc_probe_reset(&g_exec_stage[0], "exec_stage_stage_sample");
	plc_probe_reset(&g_exec_stage[1], "exec_stage_stage_pid");
	plc_probe_reset(&g_exec_stage[2], "exec_stage_stage_sink");
	if (clock_gettime(CLOCK_MONOTONIC, &next) != 0) return 1;
	for (k = 0; k < cycles; k++) {
		long long r, s, f, hns = 0, _a;
		int rc;
		bump_ns(&next, PERIOD_NS);
		r = (long long)next.tv_sec * 1000000000LL + next.tv_nsec;
		if (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) != 0)
			return 1;
		s = now_ns();
		plc_probe_add(&g_start, s - r);
		_a = now_ns();
		stage_sample();
		plc_probe_add(&g_exec_stage[0], now_ns() - _a);
		if (mixed) {
			long long t0 = now_ns();
			rc = do_kernel_group(g_seq, &hns);
			plc_probe_add(&g_rt, now_ns() - t0);
			if (rc != 0) {
				fprintf(stderr,
				        "# uku_abort: cycle=%ld seq=%u rc=%d errno=%d\n",
				        k, g_seq, rc, errno);
				if (rc == -EILSEQ) bad_seq++;
				else bad_ioctl++;
				goto uku_fail;
			}
			plc_probe_add(&g_hand, hns);
		} else {
			_a = now_ns();
			stage_pid();
			plc_probe_add(&g_exec_stage[1], now_ns() - _a);
		}
		_a = now_ns();
		stage_sink();
		plc_probe_add(&g_exec_stage[2], now_ns() - _a);
		f = now_ns();
		plc_probe_add(&g_work, f - s);
		plc_probe_add(&g_resp, f - r);
		if (g_csv)
			printf("%ld,%lld,%lld,%lld\n", k, s - r, f - s, f - r);
		DIGEST_MIX(g_in);
		DIGEST_MIX(g_out);
		DIGEST_MIX(g_seq);
		if (f - r > DEADLINE_NS) miss++;
	}
	if (mixed) {
		struct kfusion_uku_state st;
		memset(&st, 0, sizeof(st));
		if (uku_fd >= 0 &&
		    ioctl(uku_fd, KFUSION_UKU_STATE_IOC, &st) == 0 &&
		    st.magic == KFUSION_UKU_MAGIC) {
			sess_val = st.value;
			sess_ok = 1;
		}
	} else {
		sess_val = g_integ;
		sess_ok = 1;
	}
	if (uku_fd >= 0) { close(uku_fd); uku_fd = -1; }
	printf("\n# mode: %s  cycles=%ld\n", mode, cycles);
	plc_probe_head(PERIOD_NS, DEADLINE_NS, (unsigned long long)cycles);
	plc_probe_line(&g_start);
	plc_probe_line(&g_work);
	plc_probe_line(&g_resp);
	if (mixed) {
		plc_probe_line(&g_hand);
		plc_probe_line(&g_rt);
	}
	plc_probe_line(&g_exec_stage[0]);
	plc_probe_line(&g_exec_stage[1]);
	plc_probe_line(&g_exec_stage[2]);
	printf("# deadline_miss: %ld/%ld\n", miss, cycles);
	printf("# uku_seq_mismatch: %ld/%ld\n", bad_seq, cycles);
	printf("# uku_ioctl_error: %ld/%ld\n", bad_ioctl, cycles);
	printf("# digest: %08x\n", g_digest);
	printf("# last_seq: %u g_in=%d g_out=%d\n", g_seq, g_in, g_out);
	printf("# session: g_integ=%d readback=%d\n", sess_val, sess_ok);
	return 0;
uku_fail:
	if (uku_fd >= 0) { close(uku_fd); uku_fd = -1; }
	printf("# uku_fail_closed: cycle=%ld seq_err=%ld ioctl_err=%ld\n",
	       k, bad_seq, bad_ioctl);
	return 3;
}

int main(int argc, char **argv)
{
	const char *mode = (argc > 1) ? argv[1] : "gold";
	long cycles = (argc > 2) ? strtol(argv[2], NULL, 10) : 1;
	if (strcmp(mode, "gold") && strcmp(mode, "mixed")) {
		fprintf(stderr, "usage: %s gold|mixed [cycles] [csv]\n", argv[0]);
		return 2;
	}
	if (argc > 3 && !strcmp(argv[3], "csv"))
		g_csv = 1;
	return run_mode(mode, cycles);
}
