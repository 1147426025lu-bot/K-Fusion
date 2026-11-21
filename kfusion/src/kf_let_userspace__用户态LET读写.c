/*
 * kf_let_userspace__用户态LET读写.c — A 组：用户态线程执行 LET 读写
 *
 * 与 C/D 组（kf_hand_let / kf_task）严格同构：
 *   同一周期 period_us，同一相位 0(计算) / 400(写) / 500(读) / 700(收尾)，
 *   同一个计算体、同一套读写桩语义，同一个 36 字节逐样本日志格式。
 *
 * 唯一变量：读写桩由「用户态 SCHED_FIFO 线程在 clock_nanosleep 唤醒后执行」，
 * 而不是由「内核 hrtimer 回调」执行。因此本臂的抖动包含完整的用户态唤醒延迟
 * （timer 到期 → 调度器选中 → 返回用户态 → 执行桩），这正是 A 组要暴露的量。
 *
 * 日志直接写文件，头部格式与 /dev/kf_let_log 一致（arm_path=3 标识用户态），
 * 可直接用 let_log_dump 导出并用 plot_let_jitter 出图。
 *
 * 构建（板端）：
 *   gcc -O2 -o kf_let_userspace kf_let_userspace__用户态LET读写.c
 *
 * 用法：
 *   sudo ./kf_let_userspace --cycles 60000 --out raw.bin
 *   ./kf_let_userspace --period-us 1000 --cpu 3 --prio 80 --cycles 60000 \
 *                      --phase-write-us 400 --phase-read-us 500 --out raw.bin
 *
 * --mode spin（诊断用，不是 A 组主口径）：
 *   用忙等代替 clock_nanosleep 等目标时刻。若 spin 的抖动远小于 sleep，
 *   说明 sleep 侧的抖动全部来自「唤醒 + 返回用户态」，而不是测量或读写桩本身。
 *   代价是 CPU 100% 占用，负载形态与主口径不同，因此只作机制分解，不替主口径。
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

#define KF_LET_LOG_MAGIC    0x4B464C54u
#define KF_LET_HDR_VERSION  1u
#define KF_LET_ARM_USER     3u   /* 0=thread 1=irq_hard 2=handwritten 3=userspace 4=userspace_spin */
#define KF_LET_ARM_SPIN     4u

#define KF_LET_OP_WRITE     1u
#define KF_LET_OP_READ      2u
#define KF_LET_ST_OK        0u
#define KF_LET_ST_SKIP      1u
#define KF_LET_CTX_TASK     0x04u

#define NS_PER_US 1000LL
#define NS_PER_S  1000000000LL

struct __attribute__((packed)) kf_let_sample {
	uint32_t cycle_id;
	uint8_t  op;
	uint8_t  status;
	uint8_t  ctx;
	uint8_t  cpu;
	int64_t  nominal_ns;
	int64_t  t_before_ns;
	int64_t  t_after_ns;
	uint32_t version;
};

struct __attribute__((packed)) kf_let_log_hdr {
	uint32_t magic;
	uint32_t hdr_version;
	uint32_t sample_bytes;
	uint32_t n_samples;
	uint32_t drops;
	uint32_t period_us;
	uint32_t arm_path;
	uint32_t reserved;
};

/* ---- 与被测模块同构的共享数据 ----
 * 类型必须与 D 组逐字节一致：历史上这里用的是 volatile long（ARM64 上 8 字节），
 * 而 D 组是 int32_t（4 字节），store 粒度不同会悄悄改变抖动来源。
 * g_seed 初值按规范取 1u（曾误用 12345，会让三组跑出不同的状态序列）。 */
static int32_t  g_state;
static uint32_t g_seed = 1u;
static int32_t  g_cmd_pending;
static int32_t  g_cmd;
static uint32_t g_cmd_ver;
static int32_t  g_cmd_snap;
static uint32_t g_cmd_snap_ver;
static int32_t  g_out;
static int32_t  g_ready;

static long n_skip_commit;
static long n_hold;

/* 任务计算只有一份定义，与 C/D 组共用 */
#include "let_pc_compute__产消计算体.h"

/* 生产者阶段 = 共用计算 + 本宿主的就绪标记。
 * 门禁（就绪标志）故意留在宿主侧而不是共用计算里：门禁口径本身是 D 组要修的
 * 对象，藏进共用计算会让四个宿主共享同一个错误。 */
static inline void stage_prod_hosted(void)
{
	kf_pc_prod();
	__atomic_store_n(&g_ready, 1, __ATOMIC_RELAXED);
}

/* 对应 hand_stub_write：未就绪则 skip（计一次 skip，不进抖动统计） */
static int stub_write(uint32_t *ver_out)
{
	if (!__atomic_load_n(&g_ready, __ATOMIC_RELAXED)) {
		n_skip_commit++;
		if (ver_out)
			*ver_out = __atomic_load_n(&g_cmd_ver, __ATOMIC_RELAXED);
		return KF_LET_ST_SKIP;
	}
	KF_PC_COMMIT_STMT();
	__atomic_store_n(&g_ready, 0, __ATOMIC_RELAXED);
	if (ver_out)
		*ver_out = __atomic_load_n(&g_cmd_ver, __ATOMIC_RELAXED);
	return KF_LET_ST_OK;
}

/* 对应 hand_stub_read */
static int stub_read(uint32_t *ver_out)
{
	KF_PC_FETCH_STMT();
	if (ver_out)
		*ver_out = __atomic_load_n(&g_cmd_snap_ver, __ATOMIC_RELAXED);
	return KF_LET_ST_OK;
}

static inline int64_t now_ns(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * NS_PER_S + (int64_t)ts.tv_nsec;
}

static inline void sleep_abs(int64_t at_ns)
{
	struct timespec ts;
	int rc;

	ts.tv_sec  = (time_t)(at_ns / NS_PER_S);
	ts.tv_nsec = (long)(at_ns % NS_PER_S);
	if (ts.tv_nsec < 0) {          /* at_ns 为正，理论上不会发生，防御一次 */
		ts.tv_nsec += NS_PER_S;
		ts.tv_sec  -= 1;
	}
	do {
		rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL);
	} while (rc == EINTR);
}

/* 诊断用：忙等到目标时刻。不含唤醒，只余「轮询粒度 + 被中断打断」的延迟。 */
static inline void spin_abs(int64_t at_ns)
{
	while (now_ns() < at_ns)
		;   /* 隔离核上无人抢占；每次 now_ns 是 vDSO 读，约 20ns */
}

static int g_spin;

static inline void wait_abs(int64_t at_ns)
{
	if (g_spin)
		spin_abs(at_ns);
	else
		sleep_abs(at_ns);
}

static void usage(const char *p)
{
	fprintf(stderr,
		"用法: %s [选项]\n"
		"  --period-us N     周期，默认 1000\n"
		"  --cycles N        周期数，默认 60000（1kHz 下 = 60s）\n"
		"  --phase-write-us N 写相位，默认 400\n"
		"  --phase-read-us  N 读相位，默认 500\n"
		"  --phase-g1-us    N 收尾相位，默认 700（占位，与 D 组 act@700 对齐）\n"
		"  --cpu N           绑核，默认 3\n"
		"  --prio N          SCHED_FIFO 优先级，默认 80\n"
		"  --out PATH        日志输出路径，默认 kf_let_userspace.bin\n"
		"  --mode sleep|spin 等待方式：sleep=clock_nanosleep(主口径)，"
		"spin=忙等(诊断用，100%% CPU)\n"
		"  --no-rt           不切 SCHED_FIFO（用于对照普通线程）\n",
		p);
}

int main(int argc, char **argv)
{
	int period_us = 1000;
	long cycles = 60000;
	int ph_wr_us = 400, ph_rd_us = 500, ph_g1_us = 700;
	int cpu = 3, prio = 80, use_rt = 1;
	const char *out_path = "kf_let_userspace.bin";
	struct kf_let_sample *buf;
	struct kf_let_log_hdr hdr;
	int64_t period_ns, base_ns, t_cycle, nom, tb, ta;
	long c, n = 0, miss_late = 0;
	int fd;

	for (int i = 1; i < argc; i++) {
		const char *a = argv[i];
		const char *v = (i + 1 < argc) ? argv[i + 1] : NULL;
		if (!strcmp(a, "--period-us") && v)        { period_us = atoi(v); i++; }
		else if (!strcmp(a, "--cycles") && v)      { cycles = atol(v); i++; }
		else if (!strcmp(a, "--phase-write-us") && v) { ph_wr_us = atoi(v); i++; }
		else if (!strcmp(a, "--phase-read-us") && v)  { ph_rd_us = atoi(v); i++; }
		else if (!strcmp(a, "--phase-g1-us") && v)    { ph_g1_us = atoi(v); i++; }
		else if (!strcmp(a, "--cpu") && v)         { cpu = atoi(v); i++; }
		else if (!strcmp(a, "--prio") && v)        { prio = atoi(v); i++; }
		else if (!strcmp(a, "--out") && v)         { out_path = v; i++; }
		else if (!strcmp(a, "--mode") && v) {
			if (!strcmp(v, "spin"))      g_spin = 1;
			else if (!strcmp(v, "sleep")) g_spin = 0;
			else { fprintf(stderr, "--mode 只接受 sleep|spin\n"); return 2; }
			i++;
		}
		else if (!strcmp(a, "--no-rt"))            { use_rt = 0; }
		else if (!strcmp(a, "-h") || !strcmp(a, "--help")) { usage(argv[0]); return 0; }
		else { fprintf(stderr, "未知参数: %s\n", a); usage(argv[0]); return 2; }
	}

	if (period_us <= 0 || cycles <= 0) {
		fprintf(stderr, "period/cycles 必须为正\n");
		return 2;
	}
	if (ph_wr_us >= period_us || ph_rd_us >= period_us || ph_g1_us >= period_us) {
		fprintf(stderr, "相位必须小于周期\n");
		return 2;
	}

	period_ns = (int64_t)period_us * NS_PER_US;

	/* 预分配 + 锁页：避免运行中缺页把抖动算进测量 */
	if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
		fprintf(stderr, "警告: mlockall 失败(%s)，抖动可能含缺页\n", strerror(errno));
	/* timer slack 置 1ns：否则内核会把 nanosleep 唤醒合并到默认 50us 窗口 */
	if (prctl(PR_SET_TIMERSLACK, 1UL, 0, 0, 0) != 0)
		fprintf(stderr, "警告: PR_SET_TIMERSLACK 失败(%s)\n", strerror(errno));

	if (use_rt) {
		struct sched_param sp = { .sched_priority = prio };
		if (sched_setscheduler(0, SCHED_FIFO, &sp) != 0)
			fprintf(stderr, "警告: SCHED_FIFO 失败(%s)，需 root 或 CAP_SYS_NICE；"
					"本臂将退化为 CFS\n", strerror(errno));
	}

	{
		cpu_set_t set;
		CPU_ZERO(&set);
		CPU_SET(cpu, &set);
		if (sched_setaffinity(0, sizeof(set), &set) != 0)
			fprintf(stderr, "警告: 绑核 CPU%d 失败(%s)\n", cpu, strerror(errno));
	}

	buf = calloc((size_t)cycles * 2, sizeof(*buf));
	if (!buf) {
		fprintf(stderr, "样本缓冲分配失败\n");
		return 1;
	}

	fprintf(stderr,
		"A 组用户态 LET: period=%dus cycles=%ld 相位 w=%d r=%d g1=%d cpu=%d "
		"policy=%s prio=%d wait=%s\n",
		period_us, cycles, ph_wr_us, ph_rd_us, ph_g1_us, cpu,
		use_rt ? "SCHED_FIFO" : "SCHED_OTHER", prio,
		g_spin ? "spin" : "nanosleep");

	/* 起点对齐到周期整数倍，并留 100ms 让调度器/页表稳定 */
	base_ns = now_ns() + 100 * 1000 * 1000LL;
	base_ns = ((base_ns + period_ns - 1) / period_ns) * period_ns;

	for (c = 0; c < cycles; c++) {
		uint32_t ver;

		t_cycle = base_ns + c * period_ns;

		/* slot0 @0us：生产者计算（与 C/D 组同一份共用计算） */
		wait_abs(t_cycle);
		stage_prod_hosted();

		/* slot1：写提交 */
		nom = t_cycle + (int64_t)ph_wr_us * NS_PER_US;
		wait_abs(nom);
		tb = now_ns();
		{
			int st = stub_write(&ver);
			ta = now_ns();
			buf[n].cycle_id   = (uint32_t)c;
			buf[n].op         = KF_LET_OP_WRITE;
			buf[n].status     = (uint8_t)st;
			buf[n].ctx        = KF_LET_CTX_TASK;
			buf[n].cpu        = (uint8_t)cpu;
			buf[n].nominal_ns = nom;
			buf[n].t_before_ns = tb;
			buf[n].t_after_ns  = ta;
			buf[n].version    = ver;
			n++;
		}

		/* slot2：读快照 */
		nom = t_cycle + (int64_t)ph_rd_us * NS_PER_US;
		wait_abs(nom);
		tb = now_ns();
		{
			int st = stub_read(&ver);
			ta = now_ns();
			buf[n].cycle_id   = (uint32_t)c;
			buf[n].op         = KF_LET_OP_READ;
			buf[n].status     = (uint8_t)st;
			buf[n].ctx        = KF_LET_CTX_TASK;
			buf[n].cpu        = (uint8_t)cpu;
			buf[n].nominal_ns = nom;
			buf[n].t_before_ns = tb;
			buf[n].t_after_ns  = ta;
			buf[n].version    = ver;
			n++;
		}

		/* slot3 @700us：消费者计算（吃本拍 500us 的快照，与 D 组 stage_act 同相位）
		 * 历史上这里是空占位，消费被并进 slot0 吃上一拍快照 —— 消费相位因此
		 * 与 D 组差一整拍，属于必须并掉的混杂。 */
		wait_abs(t_cycle + (int64_t)ph_g1_us * NS_PER_US);
		kf_pc_act();

		/* 超期：整周期所有 slot 都跑完了还没进下一周期起点之后 */
		if (now_ns() > t_cycle + period_ns)
			miss_late++;
	}

	memset(&hdr, 0, sizeof(hdr));
	hdr.magic        = KF_LET_LOG_MAGIC;
	hdr.hdr_version  = KF_LET_HDR_VERSION;
	hdr.sample_bytes = (uint32_t)sizeof(struct kf_let_sample);
	hdr.n_samples    = (uint32_t)n;
	hdr.drops        = 0;
	hdr.period_us    = (uint32_t)period_us;
	hdr.arm_path     = g_spin ? KF_LET_ARM_SPIN : KF_LET_ARM_USER;

	fd = open(out_path, O_CREAT | O_TRUNC | O_WRONLY, 0644);
	if (fd < 0) {
		fprintf(stderr, "打开 %s 失败: %s\n", out_path, strerror(errno));
		return 1;
	}
	if (write(fd, &hdr, sizeof(hdr)) != (ssize_t)sizeof(hdr) ||
	    write(fd, buf, (size_t)n * sizeof(*buf)) != (ssize_t)((size_t)n * sizeof(*buf))) {
		fprintf(stderr, "写日志失败: %s\n", strerror(errno));
		close(fd);
		return 1;
	}
	close(fd);

	printf("{\"arm\":\"userspace_A\",\"samples\":%ld,\"cycles\":%ld,"
	       "\"period_us\":%d,\"skip_commit\":%ld,\"hold\":%ld,"
	       "\"late_cycle\":%ld,\"out\":\"%s\"}\n",
	       n, cycles, period_us, n_skip_commit, n_hold, miss_late, out_path);
	return 0;
}
