/*
 * plc_abi.h — PLCFusion 内核运行时 ABI
 *
 * 功能: 声明 fused .o 调用的 plc_* 接口；宿主模块（runner/host/stubs）实现
 */
#ifndef PLC_ABI_H
#define PLC_ABI_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stddef.h>
#include <stdint.h>
typedef long long s64;
#endif

struct plc_timespec {
	long tv_sec;
	long tv_nsec;
};

struct plc_itimerspec {
	struct plc_timespec it_interval;
	struct plc_timespec it_value;
};

#ifdef __KERNEL__
#include <linux/sched/types.h>
#else
struct sched_param {
	int sched_priority;
};
#endif

/* --- time / sleep --- */
int plc_ktime_get_ts(int clk_id, struct plc_timespec *ts);
int plc_gettimeofday(void *tv, void *tz);
int plc_nanosleep(const struct plc_timespec *req, struct plc_timespec *rem);
int plc_clock_nanosleep(int clockid, int flags,
			const struct plc_timespec *request,
			struct plc_timespec *remain);

/* --- memory --- */
void *plc_kmalloc(size_t size);
void *plc_krealloc(void *ptr, size_t size);
void *plc_kcalloc(size_t nmemb, size_t size);
char *plc_kstrdup(const char *s);
void plc_kfree(void *ptr);

/* --- print / diagnostics --- */
int plc_printk(const char *fmt, ...);
int plc_puts(const char *s);
int plc_fprintf(void *stream, const char *fmt, ...);
int plc_dprintf(int fd, const char *fmt, ...);
void plc_perror(const char *msg);
void plc_warn(const char *fmt, ...);
void plc_err_msg_n(int n, const char *fmt, ...);
void plc_info(int verbose, const char *fmt, ...);
void plc_fatal(const char *fmt, ...);
void plc_exit(int code);

/* --- timers / signals (hrtimer-backed in cyclictest host) --- */
int plc_timer_create(int clockid, void *sevp, void **timerid);
int plc_timer_settime(void *timerid, int flags,
		      const struct plc_itimerspec *new_value,
		      struct plc_itimerspec *old_value);
int plc_timer_getoverrun(void *timerid);
int plc_timer_delete(void *timerid);
int plc_sigemptyset(unsigned long *set);
int plc_sigaddset(unsigned long *set, int sig);
int plc_sigprocmask(int how, unsigned long *set, unsigned long *oldset);
int plc_sigwait(const unsigned long *set, int *sig);
void *plc_signal(int sig, void *handler);
void plc_signal_deliver(int sig);
void plc_fused_stats_tick(void);
int plc_sigaction(int sig, const void *act, void *oldact);
void plc_assert_fail(const char *expr, const char *file, unsigned int line,
		     const char *func);

/* --- threads / scheduling --- */
int plc_setscheduler(int pid, int policy, const struct sched_param *param);
unsigned long plc_pthread_self(void);
int plc_pthread_setaffinity_np(unsigned long thread, size_t cpusetsize,
			       const unsigned long *cpuset);
int plc_pthread_create(unsigned long *thread, void *attr,
		       void *(*start_routine)(void *), void *arg);
int plc_pthread_join(unsigned long thread, void **retval);
int plc_pthread_kill(unsigned long thread, int sig);
void plc_pthread_wake_all(void);
int plc_gettid(void);
int plc_getpid(void);
int plc_sched_setaffinity(int pid, unsigned long len, const unsigned long *mask);
int plc_sched_getaffinity(int pid, unsigned long len, unsigned long *mask);
int plc_mutex_lock(void *mutex);
int plc_mutex_unlock(void *mutex);
int plc_mutex_init(void *mutex, void *attr);
int plc_mutex_destroy(void *mutex);
int plc_cond_wait(void *cond, void *mutex);
int plc_cond_signal(void *cond);
int plc_cond_broadcast(void *cond);
int plc_cond_timedwait(void *cond, void *mutex, const struct plc_timespec *abstime);
int plc_barrier_init(void *barrier, void *attr, unsigned count);
int plc_barrier_wait(void *barrier);

/* --- memory lock / IO stubs --- */
int plc_mlockall(int flags);
int plc_munlockall(void);
int plc_mlock(const void *addr, size_t len);
int plc_open(const char *path, int flags, ...);
long plc_read(int fd, void *buf, unsigned long count);
long plc_write(int fd, const void *buf, unsigned long count);
int plc_close(int fd);
int plc_usleep(unsigned usec);

void *plc_mmap(void *addr, size_t len, int prot, int flags, int fd, long offset);
int plc_munmap(void *addr, size_t len);
int plc_shm_open(const char *name, int oflag, ...);
int plc_shm_unlink(const char *name);
long plc_lseek(int fd, long offset, int whence);
int plc_ftruncate(int fd, long length);
int plc_stat(const char *path, void *buf);
int plc_unlink(const char *path);
int plc_mkfifo(const char *path, unsigned mode);
void *plc_fopen(const char *path, const char *mode);
int plc_fclose(void *stream);
void *plc_fdopen(int fd, const char *mode);

int plc_snprintf(char *buf, size_t size, const char *fmt, ...);
int plc_fflush(void *stream);
int plc_fsync(int fd);
int plc_pthread_detach(unsigned long thread);
int plc_pthread_attr_setdetachstate(void *attr, int state);
int plc_pthread_attr_setinheritsched(void *attr, int inherit);
int plc_pthread_attr_setschedpolicy(void *attr, int policy);
int plc_sem_init(void *sem, int pshared, unsigned value);
int plc_sem_destroy(void *sem);
int plc_sem_wait(void *sem);
int plc_sem_post(void *sem);
int plc_sem_timedwait(void *sem, const struct plc_timespec *abs);
int plc_sem_getvalue(void *sem, int *sval);
int plc_sched_getparam(int pid, struct sched_param *param);
int plc_sched_getscheduler(int pid);

#endif /* PLC_ABI_H */
