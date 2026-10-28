/* PR-8：用户态金轨迹 vs 会话 ioctl；重复序号；关闭重开。 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include "pr8_pid_session.h"

#define N 40

static int fail(const char *w)
{
	fprintf(stderr, "FAIL %s errno=%d %s\n", w, errno, strerror(errno));
	return 1;
}

static void gold_traj(int32_t *out, int n)
{
	int32_t integ = 0;
	int i;

	for (i = 1; i <= n; i++) {
		int32_t gin = kfusion_pid_sample((uint32_t)i);
		int32_t gout = 0;

		kfusion_pid_step(&integ, gin, &gout);
		out[i] = gout;
	}
}

int main(void)
{
	int32_t gold[N + 1];
	struct kfusion_uku_pid_msg m;
	int fd, fd2, i;
	int32_t first_out = 0;

	gold_traj(gold, N);

	fd = open("/dev/kfusion_uku_pid", O_RDWR);
	if (fd < 0)
		return fail("open");
	fd2 = open("/dev/kfusion_uku_pid", O_RDWR);
	if (fd2 >= 0) {
		close(fd2);
		close(fd);
		fprintf(stderr, "FAIL second open accepted\n");
		return 1;
	}
	if (errno != EBUSY)
		return fail("second open want EBUSY");
	printf("second_open_ebusy_ok\n");

	memset(&m, 0, sizeof(m));
	m.magic = 0xdeadbeefu;
	m.seq = 1;
	m.g_in = 0;
	if (ioctl(fd, KFUSION_UKU_PID_IOC, &m) == 0) {
		fprintf(stderr, "FAIL bad magic accepted\n");
		return 1;
	}
	if (errno != EINVAL)
		return fail("bad magic want EINVAL");
	printf("bad_magic_einval_ok\n");

	for (i = 1; i <= N; i++) {
		memset(&m, 0, sizeof(m));
		m.magic = KFUSION_UKU_PID_MAGIC;
		m.seq = (uint32_t)i;
		m.g_in = kfusion_pid_sample((uint32_t)i);
		if (ioctl(fd, KFUSION_UKU_PID_IOC, &m) != 0)
			return fail("seq step");
		if (m.g_out != gold[i]) {
			fprintf(stderr, "FAIL traj seq=%d got=%d gold=%d\n",
			        i, m.g_out, gold[i]);
			return 1;
		}
		if (i == 1)
			first_out = m.g_out;
	}
	printf("traj_match_ok n=%d last_out=%d\n", N, gold[N]);

	memset(&m, 0, sizeof(m));
	m.magic = KFUSION_UKU_PID_MAGIC;
	m.seq = 1;
	m.g_in = kfusion_pid_sample(1);
	m.g_out = 123456;
	if (ioctl(fd, KFUSION_UKU_PID_IOC, &m) == 0) {
		fprintf(stderr, "FAIL replay seq1 accepted\n");
		return 1;
	}
	if (errno != EILSEQ)
		return fail("replay want EILSEQ");
	if (m.g_out != 123456) {
		fprintf(stderr, "FAIL replay mutated out\n");
		return 1;
	}
	printf("replay_eilseq_ok\n");

	memset(&m, 0, sizeof(m));
	m.magic = KFUSION_UKU_PID_MAGIC;
	m.seq = (uint32_t)(N + 1);
	m.g_in = kfusion_pid_sample((uint32_t)(N + 1));
	if (ioctl(fd, KFUSION_UKU_PID_IOC, &m) != 0)
		return fail("seq after replay");
	{
		int32_t extra[N + 2];

		gold_traj(extra, N + 1);
		if (m.g_out != extra[N + 1]) {
			fprintf(stderr, "FAIL post-replay traj got=%d gold=%d\n",
			        m.g_out, extra[N + 1]);
			return 1;
		}
	}
	printf("post_replay_traj_ok\n");

	close(fd);
	fd = open("/dev/kfusion_uku_pid", O_RDWR);
	if (fd < 0)
		return fail("reopen");
	memset(&m, 0, sizeof(m));
	m.magic = KFUSION_UKU_PID_MAGIC;
	m.seq = 1;
	m.g_in = kfusion_pid_sample(1);
	if (ioctl(fd, KFUSION_UKU_PID_IOC, &m) != 0)
		return fail("reopen seq1");
	if (m.g_out != first_out) {
		fprintf(stderr, "FAIL reopen state leaked got=%d want=%d\n",
		        m.g_out, first_out);
		return 1;
	}
	printf("reopen_resets_ok out=%d\n", m.g_out);
	close(fd);
	printf("all_ok\n");
	return 0;
}
