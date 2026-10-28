/* PR-5D：对 /dev/kfusion_uku 做故障注入。不是性能测试。
 * 二次打开 EBUSY；坏 magic/n_in EINVAL；坏序号 EILSEQ 且不改 out 缓冲。
 * 合法序号与 gold 公式逐拍一致：g_in=seq*17+3, g_mid=g_in+1, g_out=g_mid<<1。
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include "kfusion_uku.h"

static int fail(const char *w)
{
	fprintf(stderr, "FAIL %s errno=%d %s\n", w, errno, strerror(errno));
	return 1;
}

static void fill(struct kfusion_uku_msg *m, uint32_t seq, int32_t gin)
{
	memset(m, 0, sizeof(*m));
	m->magic = KFUSION_UKU_MAGIC;
	m->seq = seq;
	m->n_in = 2;
	m->n_out = 2;
	m->in[0] = gin;
	m->in[1] = 0x7eee;
	m->out[0] = 0x11111111;
	m->out[1] = 0x22222222;
}

int main(void)
{
	struct kfusion_uku_msg m;
	int fd, fd2, i, mismatches = 0;
	int32_t gin, gmid, gout;

	fd = open("/dev/kfusion_uku", O_RDWR);
	if (fd < 0)
		return fail("open");
	fd2 = open("/dev/kfusion_uku", O_RDWR);
	if (fd2 >= 0) {
		fprintf(stderr, "FAIL second open succeeded\n");
		return 1;
	}
	if (errno != EBUSY)
		return fail("second open want EBUSY");
	printf("exclusive_ebusy_ok\n");

	fill(&m, 1, 20);
	m.magic = 0xdeadbeef;
	if (ioctl(fd, KFUSION_UKU_IOC, &m) == 0) {
		fprintf(stderr, "FAIL bad magic accepted\n");
		return 1;
	}
	if (errno != EINVAL)
		return fail("bad magic want EINVAL");
	printf("bad_magic_einval_ok\n");

	fill(&m, 1, 20);
	m.n_in = 3;
	if (ioctl(fd, KFUSION_UKU_IOC, &m) == 0) {
		fprintf(stderr, "FAIL bad n_in accepted\n");
		return 1;
	}
	if (errno != EINVAL)
		return fail("bad n_in want EINVAL");
	printf("bad_nin_einval_ok\n");

	fill(&m, 99, 999);
	if (ioctl(fd, KFUSION_UKU_IOC, &m) == 0) {
		fprintf(stderr, "FAIL bad seq accepted\n");
		return 1;
	}
	if (errno != EILSEQ)
		return fail("bad seq want EILSEQ");
	if (m.out[0] != 0x11111111 || m.out[1] != 0x22222222) {
		fprintf(stderr, "FAIL bad seq overwrote out\n");
		return 1;
	}
	printf("bad_seq_eilseq_ok\n");

	gin = (int32_t)(1u * 17u + 3u);
	fill(&m, 1, gin);
	if (ioctl(fd, KFUSION_UKU_IOC, &m) != 0)
		return fail("seq1");
	gmid = gin + 1;
	gout = gmid << 1;
	if (m.out[0] != gmid || m.out[1] != gout) {
		fprintf(stderr, "FAIL seq1 out %d %d want %d %d\n",
			m.out[0], m.out[1], gmid, gout);
		return 1;
	}
	printf("seq1_gold_ok g_in=%d g_mid=%d g_out=%d\n", gin, gmid, gout);

	fill(&m, 1, 999);
	if (ioctl(fd, KFUSION_UKU_IOC, &m) == 0) {
		fprintf(stderr, "FAIL replay seq1 accepted\n");
		return 1;
	}
	if (errno != EILSEQ)
		return fail("replay seq1");
	printf("replay_seq1_eilseq_ok\n");

	fill(&m, 2, 9999);
	m.seq = 2;
	/* poison then fail with wrong n_out so expect stays 2 */
	m.n_out = 7;
	if (ioctl(fd, KFUSION_UKU_IOC, &m) == 0) {
		fprintf(stderr, "FAIL poison n_out accepted\n");
		return 1;
	}
	printf("poison_nout_einval_ok\n");

	for (i = 2; i <= 40; i++) {
		gin = (int32_t)((uint32_t)i * 17u + 3u);
		fill(&m, (uint32_t)i, gin);
		if (ioctl(fd, KFUSION_UKU_IOC, &m) != 0) {
			fprintf(stderr, "FAIL seq %d errno=%d\n", i, errno);
			return 1;
		}
		gmid = gin + 1;
		gout = gmid << 1;
		if (m.out[0] != gmid || m.out[1] != gout) {
			fprintf(stderr, "FAIL seq %d out %d %d want %d %d\n",
				i, m.out[0], m.out[1], gmid, gout);
			mismatches++;
		}
	}
	if (mismatches) {
		fprintf(stderr, "FAIL cycle mismatches %d\n", mismatches);
		return 1;
	}
	printf("per_cycle_gold_ok n=40\n");
	close(fd);
	printf("all_ok\n");
	return 0;
}
