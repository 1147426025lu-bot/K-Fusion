/*
 * plc_runtime_stubs.c — 融合 .o 的 POSIX / rt-tests 运行时桩
 *
 * 功能: 为 kernel.ll 中未映射的 external 提供弱符号实现（sleep、printf、
 *       getopt、hist/mmap 等）；per-app 桩见 test/${FUSE_NAME}_runtime_stubs.c
 * 链接: 与 fused .o + (plc_fused_host | plc_runner_official) 一起编入 .ko
 */
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/sched.h>
#include <linux/cpumask.h>
#include <linux/delay.h>
#include <linux/spinlock.h>
#include <linux/atomic.h>
#include <linux/delay.h>
#include <linux/stdarg.h>
#include <linux/errno.h>
#include <linux/err.h>

#include "../include/plc_abi__运行时ABI.h"

/* --- libc string helpers (resolve fused .o externals) --- */

size_t strlen(const char *s)
{
	const char *p = s;

	while (*p)
		p++;
	return p - s;
}

int strcmp(const char *a, const char *b)
{
	unsigned char c1, c2;

	while (*a && *a == *b) {
		a++;
		b++;
	}
	c1 = *a;
	c2 = *b;
	return c1 - c2;
}

int strncmp(const char *a, const char *b, size_t n)
{
	if (!n)
		return 0;
	while (--n && *a && *a == *b) {
		a++;
		b++;
	}
	return *(unsigned char *)a - *(unsigned char *)b;
}

int strncasecmp(const char *a, const char *b, size_t n)
{
	while (n-- && *a && *b) {
		unsigned char c1 = *a;
		unsigned char c2 = *b;

		if (c1 >= 'A' && c1 <= 'Z')
			c1 += 'a' - 'A';
		if (c2 >= 'A' && c2 <= 'Z')
			c2 += 'a' - 'A';
		if (c1 != c2)
			return c1 - c2;
		a++;
		b++;
	}
	return 0;
}

char *strncpy(char *dst, const char *src, size_t n)
{
	char *ret = dst;

	while (n && (*dst++ = *src++))
		n--;
	while (n--)
		*dst++ = '\0';
	return ret;
}

size_t strnlen(const char *s, size_t maxlen)
{
	size_t i;

	for (i = 0; i < maxlen && s[i]; i++)
		;
	return i;
}

char *strerror(int err)
{
	static char buf[32];

	scnprintf(buf, sizeof(buf), "errno=%d", err);
	return buf;
}

int puts(const char *s)
{
	if (s)
		printk("%s\n", s);
	return 0;
}

static int plc_stub_errno;

int *__errno_location(void)
{
	return &plc_stub_errno;
}

/* --- rt-tests helpers (main/setup paths) --- */

void __weak rt_init(int argc, char **argv)
{
	(void)argc;
	(void)argv;
}

void __weak warn(const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

void __weak info(int verbose, const char *fmt, ...)
{
	va_list args;

	if (!verbose)
		return;
	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

void __weak fatal(const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

void __weak err_msg(const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

void __weak err_msg_n(int n, const char *fmt, ...)
{
	va_list args;

	(void)n;
	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

int __weak check_privs(void)
{
	return 0;
}

void __weak enable_trace_mark(void) {}
void __weak disable_trace_mark(void) {}
void __weak tracing_stop(void) {}
void __weak tracemark(char *fmt, ...) { (void)fmt; }

int __weak numa_initialize(void) { return 0; }
int __weak numa_available(void) { return 0; }
void *__weak numa_parse_cpustring_all(const char *s) { (void)s; return NULL; }
void *__weak numa_allocate_cpumask(void) { return NULL; }
int numa_sched_setaffinity(int pid, void *mask)
{
	(void)pid;
	(void)mask;
	return 0;
}
int __weak numa_sched_getaffinity(int pid, void *mask)
{
	(void)pid;
	(void)mask;
	return 0;
}
int numa_node_of_cpu(int cpu) { (void)cpu; return 0; }
void *numa_alloc_onnode(unsigned long size, int node)
{
	(void)node;
	return kmalloc(size, GFP_KERNEL);
}
void numa_free(void *ptr, unsigned long size)
{
	(void)size;
	kfree(ptr);
}
int numa_run_on_node(int node) { (void)node; return 0; }
int numa_bitmask_weight(void *mask) { (void)mask; return 0; }
int numa_bitmask_isbitset(void *mask, int n) { (void)mask; (void)n; return 0; }
void __weak numa_bitmask_clearbit(void *mask, int bit) { (void)mask; (void)bit; }
void numa_bitmask_free(void *mask) { (void)mask; }

int __weak __sched_cpucount(size_t setsize, const unsigned long *set)
{
	(void)setsize;
	(void)set;
	return 1;
}

long sysconf(int name)
{
	if (name == 84) /* _SC_NPROCESSORS_ONLN on glibc aarch64 */
		return nr_cpu_ids;
	return 0;
}

long __sysconf(int name) { return sysconf(name); }

int __weak parse_time_string(char *val) { (void)val; return 0; }
int __weak parse_cpumask(char *s, int max, void *mask)
{
	(void)s;
	(void)max;
	(void)mask;
	return 0;
}
int __weak get_available_cpus(void *mask) { (void)mask; return nr_cpu_ids; }
int __weak cpu_for_thread_sp(int i, int max, void *mask)
{
	(void)max;
	(void)mask;
	return i % nr_cpu_ids;
}
int __weak cpu_for_thread_ua(int i, int max)
{
	(void)max;
	return i % nr_cpu_ids;
}

typedef void (*plc_sighandler_t)(int);

static plc_sighandler_t plc_sig_handlers[32];

void *plc_signal(int sig, void *handler)
{
	int idx = sig & 31;
	plc_sighandler_t old = plc_sig_handlers[idx];

	plc_sig_handlers[idx] = (plc_sighandler_t)handler;
	return (void *)old;
}

void plc_signal_deliver(int sig)
{
	int idx = sig & 31;

	if (plc_sig_handlers[idx])
		plc_sig_handlers[idx](sig);
}

int plc_sigaction(int sig, const void *act, void *oldact)
{
	(void)oldact;
	if (act) {
		/* Linux struct sigaction: sa_handler at offset 0 on all arches we target */
		void *handler = *(void * const *)act;
		plc_signal(sig, handler);
	}
	return 0;
}

void plc_assert_fail(const char *expr, const char *file, unsigned int line,
		     const char *func)
{
	printk(KERN_ERR "PLCFusion assert fail: %s at %s:%u in %s\n",
	       expr ? expr : "?", file ? file : "?", line, func ? func : "?");
	WARN_ON(1);
}

void *signal(int sig, void *handler)
{
	return plc_signal(sig, handler);
}

int sched_get_priority_max(int policy)
{
	(void)policy;
	return 99;
}

int getrlimit(int resource, void *rlim)
{
	(void)resource;
	(void)rlim;
	return 0;
}

int setrlimit(int resource, const void *rlim)
{
	(void)resource;
	(void)rlim;
	return 0;
}

unsigned int alarm(unsigned int seconds)
{
	(void)seconds;
	return 0;
}

unsigned int sleep(unsigned int seconds)
{
	if (seconds)
		msleep(seconds * 1000);
	return 0;
}

int pause(void)
{
	schedule();
	return 0;
}

char *__weak optarg;
int __weak optind = 1;

int __weak getopt_long(int argc, char *const argv[], const char *optstring,
		       const void *longopts, int *longindex)
{
	(void)argc;
	(void)argv;
	(void)optstring;
	(void)longopts;
	(void)longindex;
	return -1;
}

int __weak usleep(unsigned int usec)
{
	if (!usec)
		return 0;
	usleep_range(usec, usec + 1);
	return 0;
}

int fprintf(void *stream, const char *fmt, ...)
{
	va_list args;

	(void)stream;
	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
	return 0;
}

int dprintf(int fd, const char *fmt, ...)
{
	va_list args;

	(void)fd;
	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
	return 0;
}

void *__weak stderr = (void *)2;
void *__weak stdout = (void *)1;

/* --- plc_* runtime (generic; timer host overrides weak symbols) --- */

void plc_kfree(void *ptr)
{
	kfree(ptr);
}

void *plc_kcalloc(size_t nmemb, size_t size)
{
	if (nmemb && size > (~(size_t)0) / nmemb)
		return NULL;
	return kcalloc(nmemb, size, GFP_KERNEL);
}

void *__weak plc_krealloc(void *ptr, size_t size)
{
	return krealloc(ptr, size, GFP_KERNEL);
}

char *__weak plc_kstrdup(const char *s)
{
	if (!s)
		return NULL;
	return kstrdup(s, GFP_KERNEL);
}

int __weak sched_yield(void)
{
	yield();
	return 0;
}

int __weak atexit(void (*fn)(void))
{
	(void)fn;
	return 0;
}

char *__weak getenv(const char *name)
{
	(void)name;
	return NULL;
}

int plc_fprintf(void *stream, const char *fmt, ...)
{
	va_list args;

	(void)stream;
	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
	return 0;
}

int plc_dprintf(int fd, const char *fmt, ...)
{
	va_list args;

	(void)fd;
	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
	return 0;
}

int plc_puts(const char *s)
{
	if (s)
		printk(KERN_INFO "%s\n", s);
	return 0;
}

void plc_perror(const char *msg)
{
	if (msg)
		printk(KERN_INFO "PLCFusion: %s (stub)\n", msg);
}

void plc_warn(const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

void plc_err_msg_n(int n, const char *fmt, ...)
{
	va_list args;

	(void)n;
	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

void plc_info(int verbose, const char *fmt, ...)
{
	va_list args;

	if (!verbose)
		return;
	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

void plc_fatal(const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
}

void plc_exit(int code)
{
	(void)code;
	/* 模块上下文不能 do_exit；交给宿主 kthread 自然返回 */
}

int plc_getpid(void)
{
	return task_pid_nr(current);
}

int plc_sched_setaffinity(int pid, unsigned long len, const unsigned long *mask)
{
	struct cpumask cpumask;
	unsigned long i;
	int ret;

	(void)pid;
	if (!mask || len < sizeof(unsigned long))
		return -EINVAL;
	cpumask_clear(&cpumask);
	for (i = 0; i < 8 * sizeof(unsigned long) && i < nr_cpu_ids; i++) {
		if (mask[0] & (1UL << i))
			cpumask_set_cpu(i, &cpumask);
	}
	if (cpumask_empty(&cpumask))
		return -EINVAL;
	ret = set_cpus_allowed_ptr(current, &cpumask);
	return ret ? ret : 0;
}

int plc_sched_getaffinity(int pid, unsigned long len, unsigned long *mask)
{
	unsigned long i;

	(void)pid;
	if (!mask || len < sizeof(unsigned long))
		return -EINVAL;
	mask[0] = 0;
	for_each_online_cpu(i) {
		if (i < 8 * sizeof(unsigned long))
			mask[0] |= 1UL << i;
	}
	return 0;
}

int plc_gettimeofday(void *tv, void *tz)
{
	struct {
		long tv_sec;
		long tv_usec;
	} *t = tv;
	u64 ns;

	(void)tz;
	if (!t)
		return -EINVAL;
	ns = ktime_get_ns();
	t->tv_sec = (long)div_u64(ns, NSEC_PER_SEC);
	t->tv_usec = (long)div_u64(ns % NSEC_PER_SEC, NSEC_PER_USEC);
	return 0;
}

int plc_mlockall(int flags)
{
	(void)flags;
	return 0;
}

int plc_munlockall(void) { return 0; }

int plc_mlock(const void *addr, size_t len)
{
	(void)addr;
	(void)len;
	return 0;
}

#define PLC_FD_TRACE_BASE 1000
#define PLC_FD_TRACE_LIMIT 16
#define PLC_FD_DMA_LATENCY 2000

static int plc_dma_latency_us = -1;

static bool plc_path_is_tracefs(const char *path)
{
	if (!path)
		return false;
	return strstr(path, "tracing_on") || strstr(path, "tracing_enabled") ||
	       strstr(path, "trace_marker") || strstr(path, "trace_clock");
}

static bool plc_path_is_dma_latency(const char *path)
{
	return path && strstr(path, "cpu_dma_latency");
}

static bool plc_fd_is_trace(int fd)
{
	return fd >= PLC_FD_TRACE_BASE &&
	       fd < PLC_FD_TRACE_BASE + PLC_FD_TRACE_LIMIT;
}

int plc_open(const char *path, int flags, ...)
{
	static atomic_t plc_trace_fd_seq = ATOMIC_INIT(PLC_FD_TRACE_BASE);

	(void)flags;
	if (plc_path_is_dma_latency(path))
		return PLC_FD_DMA_LATENCY;
	if (plc_path_is_tracefs(path))
		return atomic_inc_return(&plc_trace_fd_seq);
	return -ENOENT;
}

long plc_read(int fd, void *buf, unsigned long count)
{
	if (plc_fd_is_trace(fd)) {
		if (buf && count)
			*(char *)buf = '0';
		return count ? 1 : 0;
	}
	(void)buf;
	(void)count;
	return -ENOSYS;
}

long plc_write(int fd, const void *buf, unsigned long count)
{
	if (fd == PLC_FD_DMA_LATENCY && buf && count >= sizeof(int)) {
		plc_dma_latency_us = *(const int *)buf;
		return (long)count;
	}
	(void)buf;
	if (plc_fd_is_trace(fd))
		return (long)count;
	(void)count;
	return -ENOSYS;
}

int plc_close(int fd)
{
	(void)fd;
	return 0;
}

int plc_usleep(unsigned usec)
{
	if (!usec)
		return 0;
	usleep_range(usec, usec + 1);
	return 0;
}

void *plc_mmap(void *addr, size_t len, int prot, int flags, int fd, long offset)
{
	void *p;

	(void)addr;
	(void)prot;
	(void)flags;
	(void)fd;
	(void)offset;
	if (!len)
		return ERR_PTR(-EINVAL);
	p = kmalloc(len, GFP_KERNEL);
	return p ? p : ERR_PTR(-ENOMEM);
}

int plc_munmap(void *addr, size_t len)
{
	if (addr && (long)addr > 0)
		kfree(addr);
	(void)len;
	return 0;
}

int plc_shm_open(const char *name, int oflag, ...)
{
	(void)name;
	(void)oflag;
	return -ENOSYS;
}

int plc_shm_unlink(const char *name)
{
	(void)name;
	return 0;
}

long plc_lseek(int fd, long offset, int whence)
{
	(void)fd;
	(void)offset;
	(void)whence;
	return 0;
}

int plc_ftruncate(int fd, long length)
{
	(void)fd;
	(void)length;
	return 0;
}

int plc_stat(const char *path, void *buf)
{
	(void)path;
	(void)buf;
	return -ENOENT;
}

int plc_unlink(const char *path)
{
	(void)path;
	return 0;
}

int plc_mkfifo(const char *path, unsigned mode)
{
	(void)path;
	(void)mode;
	return -ENOSYS;
}

void *plc_fopen(const char *path, const char *mode)
{
	(void)path;
	(void)mode;
	return NULL;
}

int plc_fclose(void *stream)
{
	(void)stream;
	return 0;
}

void *plc_fdopen(int fd, const char *mode)
{
	(void)fd;
	(void)mode;
	return NULL;
}

/* rt-tests histogram / JSON helpers（weak：多 TU 时由 kernel.o 强符号覆盖） */
#define PLC_WEAK_HIST __attribute__((weak))

PLC_WEAK_HIST int hist_init(void *h, unsigned long width, unsigned long num)
{
	(void)h;
	(void)width;
	(void)num;
	return 0;
}

PLC_WEAK_HIST int hist_init_oflow(void *h, unsigned long num)
{
	(void)h;
	(void)num;
	return 0;
}

PLC_WEAK_HIST void hist_destroy(void *h) { (void)h; }

PLC_WEAK_HIST int hist_sample(void *h, u64 sample)
{
	(void)h;
	(void)sample;
	return 0;
}

PLC_WEAK_HIST void hist_print_json(void *h, void *f) { (void)h; (void)f; }
PLC_WEAK_HIST void hist_print_oflows(void *h, void *f) { (void)h; (void)f; }

PLC_WEAK_HIST int hset_init(void *hs, unsigned long histos, unsigned long bucket_width,
	      unsigned long num_buckets, unsigned long overflow)
{
	(void)hs;
	(void)histos;
	(void)bucket_width;
	(void)num_buckets;
	(void)overflow;
	return 0;
}

PLC_WEAK_HIST void hset_destroy(void *hs) { (void)hs; }

PLC_WEAK_HIST void hset_print_bucket(void *hs, void *f, const char *pre,
		       unsigned long bucket, unsigned long flags)
{
	(void)hs;
	(void)f;
	(void)pre;
	(void)bucket;
	(void)flags;
}

PLC_WEAK_HIST void rt_write_json(const char *filename, int return_code,
		   void *write_fn, void *par)
{
	(void)filename;
	(void)return_code;
	(void)write_fn;
	(void)par;
}

char *__weak get_tracefs_prefix(void)
{
	return (char *)"/sys/kernel/tracing/";
}

static int plc_gpio_shadow[32];

static uint64_t plc_udiv128_by_64(uint64_t hi, uint64_t lo, uint64_t div)
{
	if (!div)
		return 0;
	if (!hi)
		return lo / div;
	if (hi >= div)
		return ~0ULL;
	uint64_t rem = hi;
	uint64_t quot = 0;
	for (int i = 0; i < 64; ++i) {
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

int64_t plc_fix_mul_i64(long long a, long long b, int frac_bits)
{
	if (frac_bits <= 0 || frac_bits >= 63)
		return 0;
#if defined(__SIZEOF_INT128__)
	__int128 p = (__int128)a * (__int128)b;
	return (int64_t)(p >> frac_bits);
#else
	return 0;
#endif
}

int64_t plc_fix_div_i64(long long a, long long b, int frac_bits)
{
	uint64_t ua, ub;
	int neg;

	if (!b || frac_bits <= 0 || frac_bits >= 63)
		return 0;

	neg = (a < 0) ^ (b < 0);
	if (a < 0)
		ua = (uint64_t)(-(unsigned long long)a);
	else
		ua = (uint64_t)a;
	if (b < 0)
		ub = (uint64_t)(-(unsigned long long)b);
	else
		ub = (uint64_t)b;

	uint64_t lo = ua << (unsigned)frac_bits;
	uint64_t hi = ua >> (64 - (unsigned)frac_bits);
	uint64_t q = plc_udiv128_by_64(hi, lo, ub);
	return neg ? -(int64_t)q : (int64_t)q;
}

/* Q 定点 → double：仅用户态 printf 变参边界（内核 -mgeneral-regs-only 下不提供） */
#ifndef __KERNEL__
double plc_fix_to_double(long long fixed, int frac_bits)
{
	if (frac_bits <= 0 || frac_bits > 62)
		return 0.0;
	return (double)fixed / (double)(1ULL << frac_bits);
}
#endif

void plc_gpio_set(int pin, int value)
{
	if (pin >= 0 && pin < 32)
		plc_gpio_shadow[pin] = value ? 1 : 0;
}

long __isoc23_strtol(const char *nptr, char **endptr, int base)
{
	return simple_strtol(nptr, endptr, base);
}

long strtol(const char *nptr, char **endptr, int base)
{
	return simple_strtol(nptr, endptr, base);
}

int setitimer(int which, const void *new_value, void *old_value)
{
	(void)which;
	(void)new_value;
	(void)old_value;
	return 0;
}

int setvbuf(void *stream, char *buf, int mode, size_t size)
{
	(void)stream;
	(void)buf;
	(void)mode;
	(void)size;
	return 0;
}

long syscall(long number, ...)
{
	(void)number;
	return -ENOSYS;
}

int pthread_attr_init(void *attr)
{
	(void)attr;
	return 0;
}

int pthread_attr_getstack(void *attr, void **stackaddr, size_t *stacksize)
{
	(void)attr;
	if (stackaddr)
		*stackaddr = NULL;
	if (stacksize)
		*stacksize = 0;
	return 0;
}

int pthread_attr_setstack(void *attr, void *stackaddr, size_t stacksize)
{
	(void)attr;
	(void)stackaddr;
	(void)stacksize;
	return 0;
}

char *strcpy(char *dst, const char *src)
{
	char *ret = dst;

	while ((*dst++ = *src++))
		;
	return ret;
}

char *strcat(char *dst, const char *src)
{
	char *ret = dst;

	while (*dst)
		dst++;
	while ((*dst++ = *src++))
		;
	return ret;
}

int snprintf(char *buf, size_t size, const char *fmt, ...)
{
	va_list args;
	int n;

	if (!buf || !size)
		return 0;
	va_start(args, fmt);
	n = vscnprintf(buf, size, fmt, args);
	va_end(args);
	return n;
}

static spinlock_t plc_mutex_locks[32];
static bool plc_mutex_table_ready;

static spinlock_t *plc_mutex_slot(void *mutex)
{
	unsigned long idx = (unsigned long)mutex;

	if (!plc_mutex_table_ready) {
		int i;

		for (i = 0; i < 32; i++)
			spin_lock_init(&plc_mutex_locks[i]);
		plc_mutex_table_ready = true;
	}
	return &plc_mutex_locks[idx % 32];
}

int plc_mutex_lock(void *mutex)
{
	spin_lock(plc_mutex_slot(mutex));
	return 0;
}

int plc_mutex_unlock(void *mutex)
{
	spin_unlock(plc_mutex_slot(mutex));
	return 0;
}

int plc_mutex_init(void *mutex, void *attr)
{
	(void)attr;
	(void)mutex;
	return 0;
}

int plc_mutex_destroy(void *mutex)
{
	(void)mutex;
	return 0;
}

int plc_cond_wait(void *cond, void *mutex)
{
	(void)cond;
	plc_mutex_unlock(mutex);
	schedule();
	plc_mutex_lock(mutex);
	return 0;
}

int plc_cond_signal(void *cond)
{
	(void)cond;
	return 0;
}

int plc_cond_broadcast(void *cond)
{
	(void)cond;
	return 0;
}

int plc_cond_timedwait(void *cond, void *mutex,
		       const struct plc_timespec *abstime)
{
	u64 now_ns, deadline_ns, remain_ns;

	(void)abstime;
	if (abstime) {
		now_ns = ktime_get_ns();
		deadline_ns = (u64)abstime->tv_sec * NSEC_PER_SEC +
			      (u64)abstime->tv_nsec;
		if (now_ns < deadline_ns) {
			remain_ns = deadline_ns - now_ns;
			usleep_range(div_u64(remain_ns, NSEC_PER_USEC) ?: 1,
				     div_u64(remain_ns, NSEC_PER_USEC) + 1);
		}
	}
	plc_mutex_unlock(mutex);
	schedule();
	plc_mutex_lock(mutex);
	return 0;
}

static atomic_t plc_barrier_gen;
static atomic_t plc_barrier_count;
static unsigned plc_barrier_target;

int plc_barrier_init(void *barrier, void *attr, unsigned count)
{
	(void)barrier;
	(void)attr;
	atomic_set(&plc_barrier_count, 0);
	plc_barrier_target = count ? count : 1;
	atomic_inc(&plc_barrier_gen);
	return 0;
}

int plc_barrier_wait(void *barrier)
{
	int gen;

	(void)barrier;
	gen = atomic_read(&plc_barrier_gen);
	if (atomic_inc_return(&plc_barrier_count) >= (int)plc_barrier_target) {
		atomic_set(&plc_barrier_count, 0);
		atomic_inc(&plc_barrier_gen);
		return 1;
	}
	while (atomic_read(&plc_barrier_gen) == gen)
		cpu_relax();
	return 0;
}

/* weak: cyclictest host provides real hrtimer-backed implementations */
int __weak plc_timer_create(int clockid, void *sevp, void **timerid)
{
	(void)clockid;
	(void)sevp;
	if (timerid)
		*timerid = (void *)1;
	return 0;
}

int __weak plc_timer_settime(void *timerid, int flags,
			     const struct plc_itimerspec *new_value,
			     struct plc_itimerspec *old_value)
{
	(void)timerid;
	(void)flags;
	(void)new_value;
	(void)old_value;
	return 0;
}

int __weak plc_timer_getoverrun(void *timerid)
{
	(void)timerid;
	return 0;
}

int __weak plc_timer_delete(void *timerid)
{
	(void)timerid;
	return 0;
}

int __weak plc_sigemptyset(unsigned long *set)
{
	if (!set)
		return -EINVAL;
	*set = 0;
	return 0;
}

int __weak plc_sigaddset(unsigned long *set, int sig)
{
	if (!set)
		return -EINVAL;
	*set |= (1UL << (sig - 1));
	return 0;
}

int __weak plc_sigprocmask(int how, unsigned long *set, unsigned long *oldset)
{
	(void)how;
	(void)set;
	(void)oldset;
	return 0;
}

int __weak plc_sigwait(const unsigned long *set, int *sig)
{
	(void)set;
	if (sig)
		*sig = 0;
	schedule();
	return 0;
}

int __weak plc_ktime_get_ts(int clk_id, struct plc_timespec *ts)
{
	u64 ns = ktime_get_ns();

	(void)clk_id;
	if (!ts)
		return -EINVAL;
	ts->tv_sec = div_u64(ns, NSEC_PER_SEC);
	ts->tv_nsec = (long)(ns - (u64)ts->tv_sec * NSEC_PER_SEC);
	return 0;
}

int __weak plc_nanosleep(const struct plc_timespec *req, struct plc_timespec *rem)
{
	u64 ns;

	(void)rem;
	if (!req)
		return 0;
	ns = (u64)req->tv_sec * NSEC_PER_SEC + (u64)req->tv_nsec;
	if (ns)
		usleep_range(div_u64(ns, NSEC_PER_USEC) ?: 1,
			     div_u64(ns, NSEC_PER_USEC) + 1);
	return 0;
}

int __weak plc_clock_nanosleep(int clockid, int flags,
			       const struct plc_timespec *request,
			       struct plc_timespec *remain)
{
	(void)clockid;
	(void)flags;
	return plc_nanosleep(request, remain);
}

void __weak plc_fused_stats_tick(void) { }

void *__weak plc_kmalloc(size_t size)
{
	return kmalloc(size, in_interrupt() ? GFP_ATOMIC : GFP_KERNEL);
}

int __weak plc_printk(const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	vprintk(fmt, args);
	va_end(args);
	return 0;
}

int __weak plc_setscheduler(int pid, int policy, const struct sched_param *param)
{
	(void)pid;
	(void)policy;
	if (param)
		sched_set_fifo(current);
	return 0;
}

unsigned long __weak plc_pthread_self(void)
{
	return (unsigned long)current;
}

int __weak plc_pthread_setaffinity_np(unsigned long thread, size_t cpusetsize,
				      const unsigned long *cpuset)
{
	struct cpumask cpumask;
	unsigned long i;

	(void)cpusetsize;
	if ((unsigned long)current != thread && thread != 0)
		return -EINVAL;
	if (!cpuset)
		return -EINVAL;
	cpumask_clear(&cpumask);
	for (i = 0; i < 8 * sizeof(unsigned long) && i < nr_cpu_ids; i++) {
		if (cpuset[0] & (1UL << i))
			cpumask_set_cpu(i, &cpumask);
	}
	if (cpumask_empty(&cpumask))
		return -EINVAL;
	return set_cpus_allowed_ptr(current, &cpumask);
}

int __weak plc_gettid(void)
{
	return task_pid_nr(current);
}
