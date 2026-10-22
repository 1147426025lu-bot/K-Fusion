/*
 * plc_runtime_stubs__NuttX桩.c — K-Fusion 运行时 ABI 的 NuttX 实现
 *
 * 用途：让经 PLCFusionPass 内核化后的 .o 能直接在 NuttX 用户态运行。
 *
 * 背景：fused .o 不再直接调用 malloc/open/pthread_create，而是调用 plc_* 这一层
 *       （见 include/plc_abi__运行时ABI.h）。已有两套实现：
 *         - src/plc_runtime_stubs__POSIX桩.c   ← 实为 Linux 内核桩（<linux/module.h>）
 *         - src/plc_runner_official__cyclictest宿主.c ← 内核 kthread 宿主
 *       本文件是第三套：NuttX 用户态。
 *
 * 实测依据（2026-10-19，Pi4B + LLVM 19）：
 *   把 NuttX 版 cyclictest 内核化后，.o 的未定义符号从 27 个 POSIX 变成
 *   17 个 plc_* + 5 个纯 libc（getopt_long/memset/strcmp/strtol/usleep）。
 *   本文件只补齐这 17 个 plc_*，其余仍由 NuttX libc 提供。
 *
 * ⚠ 为什么不用 plc_abi.h：该头在非 __KERNEL__ 分支里
 *   `struct sched_param { int sched_priority; };` 无条件重定义，
 *   与 NuttX <sched.h> 冲突。故此处按 ABI 原样手写声明。
 *   若 plc_abi.h 将来加了 __NUTTX__ 分支，可改回 include。
 *
 * ⚠ 测量纪律：plc_printk / plc_fprintf / plc_puts 走 NuttX stdio，
 *   在 1 kHz 周期任务里调用会显著抬高抖动（实测打印会把 max 从 9µs 抬到 64µs）。
 *   内核化产物用 `-q` 跑时不会进这三个函数；调试打印请勿留在测量路径上。
 */

#include <nuttx/config.h>

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* ---- 与 plc_abi__运行时ABI.h 保持一致的本地声明 ---- */
struct plc_timespec {
	long tv_sec;
	long tv_nsec;
};

int plc_ktime_get_ts(int clk_id, struct plc_timespec *ts);
int plc_clock_nanosleep(int clockid, int flags,
			const struct plc_timespec *request,
			struct plc_timespec *remain);
void *plc_kmalloc(size_t size);
void *plc_kcalloc(size_t nmemb, size_t size);
void plc_kfree(void *ptr);
int plc_printk(const char *fmt, ...);
int plc_puts(const char *s);
int plc_fprintf(void *stream, const char *fmt, ...);
void plc_perror(const char *msg);
unsigned long plc_pthread_self(void);
int plc_pthread_create(unsigned long *thread, void *attr,
		       void *(*start_routine)(void *), void *arg);
int plc_pthread_join(unsigned long thread, void **retval);
int plc_gettid(void);
int plc_getpid(void);
int plc_open(const char *path, int flags, ...);
int plc_close(int fd);
int64_t plc_fix_div_i64(long long a, long long b, int frac_bits);

/* =====================================================================
 * 时间：NuttX 的 clock_gettime / clock_nanosleep 与 POSIX 同形，直接转
 *
 * 定义 KF_NO_TIME_STUBS 可不编这两个函数，改用
 *   objcopy --redefine-sym plc_ktime_get_ts=clock_gettime
 *   objcopy --redefine-sym plc_clock_nanosleep=clock_nanosleep
 * 直接别名到 NuttX 原函数 —— 用于量化「plc_ ABI 中间层」本身的代价。
 * ===================================================================== */

#ifndef KF_NO_TIME_STUBS

int plc_ktime_get_ts(int clk_id, struct plc_timespec *ts)
{
	struct timespec t;

	if (!ts)
		return -EINVAL;
	if (clock_gettime(clk_id, &t) != 0)
		return -errno;
	ts->tv_sec = (long)t.tv_sec;
	ts->tv_nsec = t.tv_nsec;
	return 0;
}

int plc_clock_nanosleep(int clockid, int flags,
			const struct plc_timespec *request,
			struct plc_timespec *remain)
{
	struct timespec req;
	struct timespec rem;

	if (!request)
		return 0;

	req.tv_sec = (time_t)request->tv_sec;
	req.tv_nsec = request->tv_nsec;

	if (clock_nanosleep(clockid, flags, &req, &rem) != 0)
		return -errno;

	if (remain) {
		remain->tv_sec = (long)rem.tv_sec;
		remain->tv_nsec = rem.tv_nsec;
	}
	return 0;
}

#endif /* KF_NO_TIME_STUBS */

/* =====================================================================
 * 内存：NuttX 用户态就是 malloc 族
 * ===================================================================== */

void *plc_kmalloc(size_t size)
{
	return malloc(size);
}

void *plc_kcalloc(size_t nmemb, size_t size)
{
	return calloc(nmemb, size);
}

void plc_kfree(void *ptr)
{
	free(ptr);
}

/* =====================================================================
 * 打印 / 诊断
 * ===================================================================== */

int plc_printk(const char *fmt, ...)
{
	va_list ap;
	int ret;

	if (!fmt)
		return 0;
	va_start(ap, fmt);
	ret = vfprintf(stdout, fmt, ap);
	va_end(ap);
	return ret;
}

int plc_puts(const char *s)
{
	if (!s)
		return 0;
	return puts(s);
}

int plc_fprintf(void *stream, const char *fmt, ...)
{
	va_list ap;
	int ret;

	if (!fmt)
		return 0;
	va_start(ap, fmt);
	ret = vfprintf((FILE *)(stream ? stream : stdout), fmt, ap);
	va_end(ap);
	return ret;
}

void plc_perror(const char *msg)
{
	perror(msg);
}

/* =====================================================================
 * 进程 / 线程
 * ===================================================================== */

int plc_getpid(void)
{
	return (int)getpid();
}

int plc_gettid(void)
{
	return (int)gettid();
}

/* ⚠ ABI 宽度纪律（2026-10-22 修）
 *
 * Pass 只把被调符号替换成 plc_*，**不改变实参类型**；所以桩的参数类型必须与
 * 目标平台的原函数一致。NuttX 里 pthread_t = pid_t = int（32 位）。
 * 旧实现写成 `unsigned long *thread` 并 `*thread = (unsigned long)tid;`，
 * 在 AArch64 上是 `str x9,[x8]` —— 8 字节写，会越过 4 字节的 pthread_t 覆盖相邻字段
 * （cyclictest 的 thread_stats_s 里 id 后面紧邻 int tid，实测哨兵被清零）。
 */
pthread_t plc_pthread_self(void)
{
	return pthread_self();
}

int plc_pthread_create(pthread_t *thread, void *attr,
		       void *(*start_routine)(void *), void *arg)
{
	int ret;

	if (!thread || !start_routine)
		return EINVAL;

	/* POSIX 与 NuttX 都**返回错误码本身**，不经过 errno。
	 * 旧实现 `if (pthread_create(...) != 0) return -errno;`
	 * 在 errno 恰好为 0 时会把失败报成成功（线程没建出来，调用者以为成功了）。 */
	ret = pthread_create(thread, (const pthread_attr_t *)attr,
			     start_routine, arg);
	return ret;
}

int plc_pthread_join(pthread_t thread, void **retval)
{
	return pthread_join(thread, retval);
}

/* =====================================================================
 * 文件
 * ===================================================================== */

int plc_open(const char *path, int flags, ...)
{
	mode_t mode = 0;

	/* O_CREAT 时才消费可变参数，和 POSIX open 语义一致 */
	if (flags & O_CREAT) {
		va_list ap;

		va_start(ap, flags);
		mode = (mode_t)va_arg(ap, int);
		va_end(ap);
	}
	return open(path, flags, mode);
}

int plc_close(int fd)
{
	return close(fd);
}

/* =====================================================================
 * 定点除法运行时（FixedPoint Pass 生成）
 *
 * 语义与 src/plc_runtime_stubs__POSIX桩.c 中的实现逐行一致：
 * 结果是 Q(frac_bits) 定点数，内部用 128/64 位除法避免溢出。
 * ===================================================================== */

static uint64_t plc_udiv128_by_64(uint64_t hi, uint64_t lo, uint64_t div)
{
	uint64_t rem;
	uint64_t quot = 0;
	int i;

	if (!div)
		return 0;
	if (!hi)
		return lo / div;
	if (hi >= div)
		return ~0ULL;

	rem = hi;
	for (i = 0; i < 64; ++i) {
		rem = (rem << 1) | (lo >> 63);
		lo <<= 1;
		quot <<= 1;
		if (rem >= div) {
			rem -= div;
			quot |= 1;
		}
	}
	return quot;
}

int64_t plc_fix_div_i64(long long a, long long b, int frac_bits)
{
	uint64_t ua;
	uint64_t ub;
	uint64_t lo;
	uint64_t hi;
	uint64_t q;
	int neg;

	if (!b || frac_bits <= 0 || frac_bits >= 63)
		return 0;

	neg = (a < 0) ^ (b < 0);
	ua = (a < 0) ? (uint64_t)(-(unsigned long long)a) : (uint64_t)a;
	ub = (b < 0) ? (uint64_t)(-(unsigned long long)b) : (uint64_t)b;

	lo = ua << (unsigned)frac_bits;
	hi = ua >> (64 - (unsigned)frac_bits);
	q = plc_udiv128_by_64(hi, lo, ub);
	return neg ? -(int64_t)q : (int64_t)q;
}
