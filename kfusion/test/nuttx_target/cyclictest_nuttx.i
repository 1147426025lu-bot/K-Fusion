# 0 "cyclictest.c"
# 0 "<built-in>"
# 0 "<command-line>"
# 1 "cyclictest.c"
# 46 "cyclictest.c"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h" 1
# 30 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/config.h" 1
# 31 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h" 2

# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h" 1
# 31 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/compiler.h" 1
# 32 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h" 2


# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/types.h" 1
# 55 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/types.h"
typedef signed char _int8_t;
typedef unsigned char _uint8_t;

typedef signed short _int16_t;
typedef unsigned short _uint16_t;

typedef signed int _int32_t;
typedef unsigned int _uint32_t;

typedef signed long _int64_t;
typedef unsigned long _uint64_t;


typedef _int64_t _intmax_t;
typedef _uint64_t _uintmax_t;


typedef unsigned int _wchar_t;




typedef int _wint_t;
typedef int _wctype_t;
# 87 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/types.h"
typedef long signed int _ssize_t;

typedef long unsigned int _size_t;
# 107 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/types.h"
typedef unsigned long irqstate_t;
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h" 2







typedef _wchar_t wchar_t;


# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdint.h" 1
# 37 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdint.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/inttypes.h" 1
# 38 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdint.h" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/limits.h" 1
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/limits.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/limits.h" 1
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/limits.h" 2
# 39 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdint.h" 2
# 169 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdint.h"
typedef _int8_t int8_t;
typedef _uint8_t uint8_t;

typedef _int16_t int16_t;
typedef _uint16_t uint16_t;






typedef _int32_t int32_t;
typedef _uint32_t uint32_t;


typedef _int64_t int64_t;
typedef _uint64_t uint64_t;




typedef _int8_t int_least8_t;
typedef _uint8_t uint_least8_t;

typedef _int16_t int_least16_t;
typedef _uint16_t uint_least16_t;





typedef _int32_t int_least24_t;
typedef _uint32_t uint_least24_t;


typedef _int32_t int_least32_t;
typedef _uint32_t uint_least32_t;


typedef _int64_t int_least64_t;
typedef _uint64_t uint_least64_t;




typedef _int8_t int_fast8_t;
typedef _uint8_t uint_fast8_t;

typedef int int_fast16_t;
typedef unsigned int uint_fast16_t;





typedef _int32_t int_fast24_t;
typedef _uint32_t uint_fast24_t;


typedef _int32_t int_fast32_t;
typedef _uint32_t uint_fast32_t;


typedef _int64_t int_fast64_t;
typedef _uint64_t uint_fast64_t;
# 242 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdint.h"
typedef _ssize_t intptr_t;
typedef _size_t uintptr_t;
# 256 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdint.h"
typedef _intmax_t intmax_t;
typedef _uintmax_t uintmax_t;
# 46 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h" 2
# 121 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h"
typedef unsigned int mode_t;
# 139 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h"
typedef _size_t size_t;
typedef _ssize_t ssize_t;
typedef _size_t rsize_t;







typedef unsigned int uid_t;
typedef unsigned int gid_t;



typedef uint32_t dev_t;



typedef uint32_t ino_t;



typedef uint16_t nlink_t;





typedef int pid_t;





typedef int id_t;






typedef int32_t key_t;



typedef intptr_t ptrdiff_t;





typedef _wint_t wint_t;






typedef _wctype_t wctype_t;
# 212 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h"
typedef uint32_t fsblkcnt_t;
typedef uint32_t fsfilcnt_t;
# 224 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h"
typedef uint32_t blkcnt_t;
typedef int32_t off_t;
typedef int32_t fpos_t;


typedef off_t loff_t;



typedef int32_t blksize_t;



typedef unsigned int socklen_t;

typedef uint16_t sa_family_t;
# 249 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h"
typedef int64_t clock_t;
typedef int64_t time_t;
typedef int clockid_t;
typedef void *timer_t;







typedef uint32_t useconds_t;
typedef int32_t suseconds_t;




typedef volatile uint32_t cpu_set_t;






typedef unsigned char u_char;
typedef unsigned short u_short;
typedef unsigned int u_int;
typedef unsigned long u_long;



typedef unsigned char unchar;
typedef unsigned short ushort;
typedef unsigned int uint;
typedef unsigned long ulong;
typedef signed char s_char;
typedef char *caddr_t;



typedef uint8_t u_int8_t;
typedef uint16_t u_int16_t;
typedef uint32_t u_int32_t;




typedef uint64_t u_int64_t;


struct fsid_s
{
  int val[2];
};



typedef int (*main_t)(int argc, char *argv[]);







enum
{
  ERROR = -1,
  OK = 0
};


struct pthread_attr_s;
typedef struct pthread_attr_s pthread_attr_t;




struct pthread_cond_s;
typedef struct pthread_cond_s pthread_cond_t;




typedef struct pthread_condattr_s pthread_condattr_t;




typedef int pthread_key_t;




struct pthread_mutex_s;
typedef struct pthread_mutex_s pthread_mutex_t;




struct pthread_mutexattr_s;
typedef struct pthread_mutexattr_s pthread_mutexattr_t;




typedef pid_t pthread_t;




struct pthread_once_s;
typedef struct pthread_once_s pthread_once_t;
# 380 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/types.h"
int nsh_main(int argc, char *argv[]);
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h" 2

# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdbool.h" 1
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/strings.h" 1
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/strings.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/string.h" 1
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/string.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stddef.h" 1
# 73 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stddef.h"
typedef struct
{
  long long max_align_i;

  long double max_align_f;





} max_align_t;
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/string.h" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/alloca.h" 1
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/string.h" 2
# 66 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/string.h"
 char *strdup( const char *s) __attribute__((__malloc__(__builtin_free, 1)));
 char *strndup( const char *s, size_t size) __attribute__((__malloc__(__builtin_free, 1)));
 char *strerror(int);
int strerror_r(int, char *, size_t);
size_t strlen( const char *);
size_t strnlen( const char *, size_t);
 char *strcat( char *, const char *);
size_t strlcat( char *, const char *, size_t);
 char *strncat( char *, const char *, size_t);
int strcmp( const char *, const char *);
int strncmp( const char *, const char *, size_t);





int strcasecmp( const char *, const char *);
int strncasecmp( const char *, const char *, size_t);

int strcoll( const char *, const char *s2);
 char *strcpy( char *dest, const char *src);
 char *stpcpy( char *dest, const char *src);
size_t strlcpy( char *dst, const char *src, size_t size);
 char *strncpy( char *, const char *, size_t);
 char *stpncpy( char *, const char *, size_t);
 char *strpbrk( const char *, const char *);
 char *strchr( const char *s, int c);
 char *strchrnul( const char *s, int c);
 char *strrchr( const char *s, int c);
size_t strspn( const char *, const char *);
size_t strcspn( const char *, const char *);
 char *strstr( const char *, const char *);
 char *strcasestr( const char *, const char *);
 char *strsep( char **, const char *);
 char *strsignal(int signum);
 char *strtok( char *, const char *);
 char *strtok_r( char *, const char *, char **);
size_t strxfrm( char *, const char *, size_t n);
int strverscmp( const char *s1, const char *s2);

 void *memchr( const void *s, int c, size_t n);
 void *memrchr( const void *s, int c, size_t n);
 void *rawmemchr( const void *s, int c);
 void *memccpy( void *s1, const void *s2, int c, size_t n);
int memcmp( const void *s1, const void *s2, size_t n);
 void *memcpy( void *dest, const void *src, size_t n) __attribute__((used));
 void *mempcpy( void *dest, const void *src, size_t n);
 void *memmove( void *dest, const void *src, size_t count);
 void *memset( void *s, int c, size_t n) __attribute__((used));
 void *memmem( const void *haystack, size_t haystacklen,
                  const void *needle, size_t needlelen);

void explicit_bzero( void *s, size_t n);
int timingsafe_bcmp( const void *b1, const void *b2, size_t n);






 char *nx_strdup( const char *s) __attribute__((__malloc__(__builtin_free, 1)));
 char *nx_strndup( const char *s, size_t size) __attribute__((__malloc__(__builtin_free, 1)));
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/strings.h" 2
# 79 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/strings.h"
int ffs(int j);







int ffsl(long j);







int ffsll(long long j);







int fls(int j);





int flsl(long j);





int flsll(long long j);





unsigned int popcount(unsigned int j);
unsigned int popcountl(unsigned long j);
unsigned int popcountll(unsigned long long j);

 char *index( const char *s, int c);
 char *rindex( const char *s, int c);

int strcasecmp( const char *, const char *);
int strncasecmp( const char *, const char *, size_t);
# 36 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/time.h" 1
# 115 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/time.h"
struct timespec
{
  time_t tv_sec;
  long tv_nsec;
};
# 130 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/time.h"
struct tm
{
  int tm_sec;
  int tm_min;
  int tm_hour;
  int tm_mday;
  int tm_mon;
  int tm_year;
  int tm_wday;
  int tm_yday;
  int tm_isdst;
  long tm_gmtoff;
  const char *tm_zone;
};



struct itimerspec
{
  struct timespec it_interval;
  struct timespec it_value;
};



struct sigevent;
# 191 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/time.h"
clock_t clock(void);

int clock_settime(clockid_t clockid, const struct timespec *tp);
int clock_gettime(clockid_t clockid, struct timespec *tp);
int clock_getres(clockid_t clockid, struct timespec *res);
int clock_getcpuclockid(pid_t pid, clockid_t *clockid);
int timespec_get( struct timespec *t, int b);

time_t timegm( struct tm *tp);
time_t mktime( struct tm *tp);

 struct tm *gmtime( const time_t *timep);
 struct tm *gmtime_r( const time_t *timep, struct tm *result);

 struct tm *localtime( const time_t *timep);
 struct tm *localtime_r( const time_t *timep, struct tm *result);

size_t strftime( char *s, size_t max, const char *format,
                const struct tm *tm) __attribute__((__format__(__strftime__, 3, 0)));
 char *strptime( const char *s, const char *format,
                   struct tm *tm);

 char *asctime( const struct tm *tp);
 char *asctime_r( const struct tm *tp, char *buf);
 char *ctime( const time_t *timep);
 char *ctime_r( const time_t *timep, char *buf);

time_t time( time_t *timep);


double difftime(time_t time1, time_t time0);




int timer_create(clockid_t clockid, struct sigevent *evp,
                 timer_t *timerid);
int timer_delete(timer_t timerid);
int timer_settime(timer_t timerid, int flags,
                  const struct itimerspec *value,
                  struct itimerspec *ovalue);
int timer_gettime(timer_t timerid, struct itimerspec *value);
int timer_getoverrun(timer_t timerid);

int clock_nanosleep(clockid_t clockid, int flags,
                    const struct timespec *rqtp,
                    struct timespec *rmtp);
int nanosleep( const struct timespec *rqtp, struct timespec *rmtp);

void tzset(void);
# 37 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h" 2
# 191 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h"
struct sched_param
{
  int sched_priority;
# 204 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h"
};
# 227 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h"
int task_create( const char *name, int priority, int stack_size,
                   main_t entry, char * const argv[]);
int task_create_with_stack( const char *name, int priority,
                              void *stack_addr, int stack_size,
                              main_t entry, char * const argv[]);

int task_delete(pid_t pid);
int task_restart(pid_t pid);

int task_setcancelstate(int state, int *oldstate);
int task_setcanceltype(int type, int *oldtype);
void task_testcancel(void);



int sched_getcpu(void);
int sched_setparam(pid_t pid, const struct sched_param *param);
int sched_getparam(pid_t pid, struct sched_param *param);
int sched_setscheduler(pid_t pid, int policy,
                          const struct sched_param *param);
int sched_getscheduler(pid_t pid);
int sched_yield(void);
int sched_get_priority_max(int policy);
int sched_get_priority_min(int policy);
int sched_rr_get_interval(pid_t pid, struct timespec *interval);
# 268 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sched.h"
void sched_lock(void);
void sched_unlock(void);
int sched_lockcount(void);



_Bool sched_idletask(void);
# 47 "cyclictest.c" 2


# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h" 1
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h"
# 1 "/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/lib/gcc/aarch64-none-elf/13.2.1/include/stdarg.h" 1 3 4
# 40 "/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/lib/gcc/aarch64-none-elf/13.2.1/include/stdarg.h" 3 4

# 40 "/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/lib/gcc/aarch64-none-elf/13.2.1/include/stdarg.h" 3 4
typedef __builtin_va_list __gnuc_va_list;
# 103 "/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/lib/gcc/aarch64-none-elf/13.2.1/include/stdarg.h" 3 4
typedef __gnuc_va_list va_list;
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h" 2


# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h" 1
# 30 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/assert.h" 1
# 186 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/assert.h"

# 186 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/assert.h"
void _assert( const char *filename, int linenum,
             const char *msg, void *regs);
# 197 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/assert.h"
void __assert( const char *filename, int linenum,
              const char *msg) __attribute__((noreturn));
# 31 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h" 2


# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h" 1
# 32 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/errno.h" 1
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/errno.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h" 1
# 39 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h" 1
# 39 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/syscall.h" 1
# 167 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/syscall.h"
struct arm64_smccc_res
{
  unsigned long a0;
  unsigned long a1;
  unsigned long a2;
  unsigned long a3;
  unsigned long a4;
  unsigned long a5;
  unsigned long a6;
  unsigned long a7;
};

typedef struct arm64_smccc_res arm64_smccc_res_t;

enum arm64_smccc_conduit
{
  SMCCC_CONDUIT_NONE,
  SMCCC_CONDUIT_SMC,
  SMCCC_CONDUIT_HVC,
};







static inline uintptr_t sys_call0(unsigned int nbr)
{
  register uint64_t reg0 __asm__("x0") = (uint64_t)(nbr);

  __asm__ __volatile__
  (
    "svc %1"
    : "=r"(reg0)
    : "i"(0x00), "r"(reg0)
    : "memory", "x30"
  );

  return reg0;
}



static inline uintptr_t sys_call1(unsigned int nbr, uintptr_t parm1)
{
  register uint64_t reg0 __asm__("x0") = (uint64_t)(nbr);
  register uint64_t reg1 __asm__("x1") = (uint64_t)(parm1);

  __asm__ __volatile__
  (
    "svc %1"
    : "=r"(reg0)
    : "i"(0x00), "r"(reg0), "r"(reg1)
    : "memory", "x30"
  );

  return reg0;
}



static inline uintptr_t sys_call2(unsigned int nbr, uintptr_t parm1,
                                  uintptr_t parm2)
{
  register uint64_t reg0 __asm__("x0") = (uint64_t)(nbr);
  register uint64_t reg2 __asm__("x2") = (uint64_t)(parm2);
  register uint64_t reg1 __asm__("x1") = (uint64_t)(parm1);

  __asm__ __volatile__
  (
    "svc %1"
    : "=r"(reg0)
    : "i"(0x00), "r"(reg0), "r"(reg1), "r"(reg2)
    : "memory", "x30"
  );

  return reg0;
}



static inline uintptr_t sys_call3(unsigned int nbr, uintptr_t parm1,
                                  uintptr_t parm2, uintptr_t parm3)
{
  register uint64_t reg0 __asm__("x0") = (uint64_t)(nbr);
  register uint64_t reg3 __asm__("x3") = (uint64_t)(parm3);
  register uint64_t reg2 __asm__("x2") = (uint64_t)(parm2);
  register uint64_t reg1 __asm__("x1") = (uint64_t)(parm1);

  __asm__ __volatile__
  (
    "svc %1"
    : "=r"(reg0)
    : "i"(0x00), "r"(reg0), "r"(reg1), "r"(reg2),
      "r"(reg3)
    : "memory", "x30"
  );

  return reg0;
}



static inline uintptr_t sys_call4(unsigned int nbr, uintptr_t parm1,
                                  uintptr_t parm2, uintptr_t parm3,
                                  uintptr_t parm4)
{
  register uint64_t reg0 __asm__("x0") = (uint64_t)(nbr);
  register uint64_t reg4 __asm__("x4") = (uint64_t)(parm4);
  register uint64_t reg3 __asm__("x3") = (uint64_t)(parm3);
  register uint64_t reg2 __asm__("x2") = (uint64_t)(parm2);
  register uint64_t reg1 __asm__("x1") = (uint64_t)(parm1);

  __asm__ __volatile__
  (
    "svc %1"
    : "=r"(reg0)
    : "i"(0x00), "r"(reg0), "r"(reg1), "r"(reg2),
      "r"(reg3), "r"(reg4)
    : "memory", "x30"
  );

  return reg0;
}



static inline uintptr_t sys_call5(unsigned int nbr, uintptr_t parm1,
                                  uintptr_t parm2, uintptr_t parm3,
                                  uintptr_t parm4, uintptr_t parm5)
{
  register uint64_t reg0 __asm__("x0") = (uint64_t)(nbr);
  register uint64_t reg5 __asm__("x5") = (uint64_t)(parm5);
  register uint64_t reg4 __asm__("x4") = (uint64_t)(parm4);
  register uint64_t reg3 __asm__("x3") = (uint64_t)(parm3);
  register uint64_t reg2 __asm__("x2") = (uint64_t)(parm2);
  register uint64_t reg1 __asm__("x1") = (uint64_t)(parm1);

  __asm__ __volatile__
  (
    "svc %1"
    : "=r"(reg0)
    : "i"(0x00), "r"(reg0), "r"(reg1), "r"(reg2),
      "r"(reg3), "r"(reg4), "r"(reg5)
    : "memory", "x30"
  );

  return reg0;
}



static inline uintptr_t sys_call6(unsigned int nbr, uintptr_t parm1,
                                  uintptr_t parm2, uintptr_t parm3,
                                  uintptr_t parm4, uintptr_t parm5,
                                  uintptr_t parm6)
{
  register uint64_t reg0 __asm__("x0") = (uint64_t)(nbr);
  register uint64_t reg6 __asm__("x6") = (uint64_t)(parm6);
  register uint64_t reg5 __asm__("x5") = (uint64_t)(parm5);
  register uint64_t reg4 __asm__("x4") = (uint64_t)(parm4);
  register uint64_t reg3 __asm__("x3") = (uint64_t)(parm3);
  register uint64_t reg2 __asm__("x2") = (uint64_t)(parm2);
  register uint64_t reg1 __asm__("x1") = (uint64_t)(parm1);

  __asm__ __volatile__
  (
    "svc %1"
    : "=r"(reg0)
    : "i"(0x00), "r"(reg0), "r"(reg1), "r"(reg2),
      "r"(reg3), "r"(reg4), "r"(reg5), "r"(reg6)
    : "memory", "x30"
  );

  return reg0;
}



static inline long smh_call(unsigned int nbr, void *param)
{
  register uint64_t reg0 __asm__("x0") = (uint64_t)(nbr);
  register uint64_t reg1 __asm__("x1") = (uint64_t)(param);

  __asm__ __volatile__
  (
  "hlt %1"
    : "=r"(reg0)
    : "i"(0xf000), "r"(reg0), "r"(reg1)
    : "memory", "x30"
  );

  return reg0;
}
# 378 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/syscall.h"
void arm64_smccc_hvc(unsigned long a0, unsigned long a1,
                     unsigned long a2, unsigned long a3,
                     unsigned long a4, unsigned long a5,
                     unsigned long a6, unsigned long a7,
                     struct arm64_smccc_res *res);
# 391 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/syscall.h"
void arm64_smccc_smc(unsigned long a0, unsigned long a1,
                     unsigned long a2, unsigned long a3,
                     unsigned long a4, unsigned long a5,
                     unsigned long a6, unsigned long a7,
                     struct arm64_smccc_res *res);
# 40 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/macro.h" 1
# 41 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h" 2




# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/chip/irq.h" 1
# 46 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h" 2



# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h" 1
# 50 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h" 2
# 264 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h"
struct xcptcontext
{



  uint64_t *saved_regs;
# 282 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h"
  uint64_t *regs;
# 303 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h"
  uint64_t *fpu_regs;
  uint64_t *saved_fpu_regs;
# 341 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h"
};
# 354 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/arch/irq.h"
static __attribute__((always_inline)) inline irqstate_t irqstate(void)
{
  irqstate_t flags;

  __asm__ __volatile__("mrs %0, daif" : "=r" (flags):: "memory");

  return flags;
}



static __attribute__((always_inline)) inline irqstate_t up_irq_save(void)
{
  irqstate_t flags;

  __asm__ __volatile__
    (
      "mrs %0, daif\n"
      "msr daifset, %1\n"
      : "=r" (flags)



      : "i" ((2))

      : "memory"
    );

  return flags;
}



static __attribute__((always_inline)) inline irqstate_t up_irq_enable(void)
{
  irqstate_t flags;

  __asm__ __volatile__
    (
      "mrs %0, daif\n"
      "msr daifclr, %1\n"
      : "=r" (flags)





      : "i" ((2))

      : "memory"
    );
  return flags;
}



static __attribute__((always_inline)) inline void up_irq_restore(irqstate_t flags)
{
  __asm__ __volatile__("msr daif, %0" :: "r" (flags): "memory");
}
# 40 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h" 2
# 179 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h"
typedef uint8_t irq_t;
# 202 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h"
typedef int (*xcpt_t)(int irq, void *context, void *arg);
# 252 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h"
int irq_attach(int irq, xcpt_t isr, void *arg);
# 277 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h"
int irq_attach_thread(int irq, xcpt_t isr, xcpt_t isrthread, void *arg,
                      int priority, int stack_size);
# 302 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/irq.h"
int irq_attach_wqueue(int irq, xcpt_t isr, xcpt_t isrwork,
                      void *arg, int priority);
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/errno.h" 2
# 382 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/errno.h"
 int *__errno(void);
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/semaphore.h" 1
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/semaphore.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/queue.h" 1
# 322 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/queue.h"
struct sq_entry_s
{
  struct sq_entry_s *flink;
};
typedef struct sq_entry_s sq_entry_t;

struct dq_entry_s
{
  struct dq_entry_s *flink;
  struct dq_entry_s *blink;
};
typedef struct dq_entry_s dq_entry_t;

struct sq_queue_s
{
  sq_entry_t *head;
  sq_entry_t *tail;
};
typedef struct sq_queue_s sq_queue_t;

struct dq_queue_s
{
  dq_entry_t *head;
  dq_entry_t *tail;
};
typedef struct dq_queue_s dq_queue_t;
# 367 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/queue.h"
static __attribute__((always_inline)) inline void sq_addafter( sq_entry_t *prev, sq_entry_t *node,
                               sq_queue_t *queue)
{
  if (!queue->head || prev == queue->tail)
    {
      do { sq_entry_t *tmp_node = (node); tmp_node->flink = ((void*)0); if (!(queue)->head) { (queue)->head = tmp_node; (queue)->tail = tmp_node; } else { (queue)->tail->flink = tmp_node; (queue)->tail = tmp_node; } } while (0);
    }
  else
    {
      node->flink = prev->flink;
      prev->flink = node;
    }
}






static __attribute__((always_inline)) inline void dq_addafter( dq_entry_t *prev, dq_entry_t *node,
                               dq_queue_t *queue)
{
  if (!queue->head || prev == queue->tail)
    {
      do { dq_entry_t *tmp_node = (node); tmp_node->flink = ((void*)0); tmp_node->blink = (queue)->tail; if (!(queue)->head) { (queue)->head = tmp_node; (queue)->tail = tmp_node; } else { (queue)->tail->flink = tmp_node; (queue)->tail = tmp_node; } } while (0);
    }
  else
    {
      dq_entry_t *next = prev->flink;
      node->blink = prev;
      node->flink = next;
      next->blink = node;
      prev->flink = node;
    }
}







static __attribute__((always_inline)) inline sq_entry_t *sq_remafter( sq_entry_t *node,
                                          sq_queue_t *queue)
{
  sq_entry_t *ret = node->flink;

  if (queue->head && ret)
    {
      if (queue->tail == ret)
        {
          queue->tail = node;
          node->flink = ((void*)0);
        }
      else
        {
          node->flink = ret->flink;
        }

      ret->flink = ((void*)0);
    }

  return ret;
}





static __attribute__((always_inline)) inline dq_entry_t *dq_remafter( dq_entry_t *node,
                                          dq_queue_t *queue)
{
  dq_entry_t *ret = node->flink;

  if (queue->head != ((void*)0) && ret != ((void*)0))
    {
      do { dq_entry_t *tmp_node = (ret); dq_entry_t *tmp_prev = tmp_node->blink; dq_entry_t *tmp_next = tmp_node->flink; if (!tmp_prev) { (queue)->head = tmp_next; } else { tmp_prev->flink = tmp_next; } if (!tmp_next) { (queue)->tail = tmp_prev; } else { tmp_next->blink = tmp_prev; } tmp_node->flink = ((void*)0); tmp_node->blink = ((void*)0); } while (0);
    }

  return ret;
}





static __attribute__((always_inline)) inline sq_entry_t *sq_remlast( sq_queue_t *queue)
{
  sq_entry_t *ret = queue->tail;

  if (ret)
    {
      if (queue->head == queue->tail)
        {
          queue->head = ((void*)0);
          queue->tail = ((void*)0);
        }
      else
        {
          sq_entry_t *prev;
          for (prev = queue->head;
              prev && prev->flink != ret;
              prev = prev->flink);

          if (prev)
            {
              prev->flink = ((void*)0);
              queue->tail = prev;
            }
        }

      ret->flink = ((void*)0);
    }

  return ret;
}





static __attribute__((always_inline)) inline dq_entry_t *dq_remlast( dq_queue_t *queue)
{
  dq_entry_t *ret = queue->tail;

  if (ret)
    {
      dq_entry_t *prev = ret->blink;
      if (!prev)
        {
          queue->head = ((void*)0);
          queue->tail = ((void*)0);
        }
      else
        {
          queue->tail = prev;
          prev->flink = ((void*)0);
        }

      ret->flink = ((void*)0);
      ret->blink = ((void*)0);
    }

  return ret;
}





static __attribute__((always_inline)) inline sq_entry_t *sq_remfirst( sq_queue_t *queue)
{
  sq_entry_t *ret = queue->head;

  if (ret)
    {
      queue->head = ret->flink;
      if (!queue->head)
        {
          queue->tail = ((void*)0);
        }

      ret->flink = ((void*)0);
    }

  return ret;
}





static __attribute__((always_inline)) inline dq_entry_t *dq_remfirst( dq_queue_t *queue)
{
  dq_entry_t *ret = queue->head;

  if (ret)
    {
      dq_entry_t *next = ret->flink;
      if (!next)
        {
          queue->head = ((void*)0);
          queue->tail = ((void*)0);
        }
      else
        {
          queue->head = next;
          next->blink = ((void*)0);
        }

      ret->flink = ((void*)0);
      ret->blink = ((void*)0);
    }

  return ret;
}







static __attribute__((always_inline)) inline size_t sq_count( sq_queue_t *queue)
{
  sq_entry_t *node;
  size_t count;

  for (node = queue->head, count = 0;
       node != ((void*)0);
       node = node->flink, count++);

  return count;
}





static __attribute__((always_inline)) inline size_t dq_count( dq_queue_t *queue)
{
  dq_entry_t *node;
  size_t count;

  for (node = queue->head, count = 0;
       node != ((void*)0);
       node = node->flink, count++);

  return count;
}
# 36 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/semaphore.h" 2
# 105 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/semaphore.h"
struct sem_s
{
  union
    {
      volatile int32_t semcount;

      volatile uint32_t mholder;




    } val;





  uint8_t flags;

  dq_queue_t waitlist;
# 141 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/semaphore.h"
};

typedef struct sem_s sem_t;
# 186 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/semaphore.h"
int sem_init( sem_t *sem, int pshared, unsigned int value);
int sem_destroy( sem_t *sem);
int sem_wait( sem_t *sem);
int sem_timedwait( sem_t *sem, const struct timespec *abstime);
int sem_clockwait( sem_t *sem, clockid_t clockid,
                         const struct timespec *abstime);
int sem_trywait( sem_t *sem);
int sem_post( sem_t *sem);
int sem_getvalue( sem_t *sem, int *sval);
# 239 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/semaphore.h"
int sem_setprotocol( sem_t *sem, int protocol);
# 260 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/semaphore.h"
int sem_getprotocol( sem_t *sem, int *protocol);
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h" 2

# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h" 1
# 32 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/timex.h" 1
# 30 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/timex.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h" 1
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/select.h" 1
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/select.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/signal.h" 1
# 320 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/signal.h"
struct sigset_s
{
  uint32_t _elem[(((63 + 1) + 31) >> 5)];
};

typedef struct sigset_s sigset_t;





typedef volatile int sig_atomic_t;



union sigval
{
  int sival_int;
  void *sival_ptr;
};






typedef void (*sigev_notify_function_t)(union sigval value);

typedef struct sigevent
{
  int sigev_notify;
  int sigev_signo;
  union sigval sigev_value;

  union
    {
# 368 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/signal.h"
      pid_t _tid;
    } _sigev_un;
} sigevent_t;







struct siginfo
{
  uint8_t si_signo;
  uint8_t si_code;
  uint8_t si_errno;
  union sigval si_value;







  void *si_user;
};

typedef struct siginfo siginfo_t;





typedef void (*_sa_handler_t)(int signo);
typedef void (*_sa_sigaction_t)(int signo, siginfo_t *siginfo,
                                     void *context);



typedef _sa_handler_t sighandler_t;



struct sigaction
{
  union
  {
    _sa_handler_t _sa_handler;
    _sa_sigaction_t _sa_sigaction;
  } sa_u;
  sigset_t sa_mask;
  int sa_flags;
  union
  {
    void (*sa_restorer)(void);
    void *sa_user;
  };
};
# 433 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/signal.h"
typedef struct
{
  void *ss_sp;
  int ss_flags;
  size_t ss_size;
} stack_t;

typedef void (*sig_t)(int);
# 454 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/signal.h"
int kill(pid_t pid, int signo);
int killpg(pid_t pgrp, int signo);
int tgkill(pid_t pid, pid_t tid, int signo);
void psignal(int signum, const char *message);
void psiginfo( const siginfo_t *pinfo, const char *message);
int raise(int signo);
int sigaction(int signo, const struct sigaction *act,
               struct sigaction *oact);
int sigaddset( sigset_t *set, int signo);
int sigandset( sigset_t *dest, const sigset_t *left,
               const sigset_t *right);
int sigdelset( sigset_t *set, int signo);
int sigemptyset( sigset_t *set);
int sigfillset( sigset_t *set);
int sighold(int signo);
int sigisemptyset( sigset_t *set);
int sigismember( const sigset_t *set, int signo);
int sigignore(int signo);
_sa_handler_t signal(int signo, _sa_handler_t func);
int sigorset( sigset_t *dest, const sigset_t *left,
              const sigset_t *right);
int sigpause(int signo);
int sigpending( sigset_t *set);
int sigprocmask(int how, const sigset_t *set, sigset_t *oset);
int sigqueue(int pid, int signo, union sigval value);
int sigrelse(int signo);
_sa_handler_t sigset(int signo, _sa_handler_t func);
int sigwait( const sigset_t *set, int *sig);
int sigtimedwait( const sigset_t *set, struct siginfo *value,
                  const struct timespec *timeout);
int sigsuspend( const sigset_t *sigmask);
int sigwaitinfo( const sigset_t *set, struct siginfo *value);
int sigaltstack( const stack_t *ss, stack_t *oss);
int siginterrupt(int signo, int flag);



int pthread_kill(pthread_t thread, int sig);
int pthread_sigmask(int how, const sigset_t *set, sigset_t *oset);
int pthread_sigqueue(pthread_t thread, int sig, const union sigval value);
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/select.h" 2

# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h" 1
# 37 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/select.h" 2
# 85 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/select.h"
struct fd_set_s
{
  uint32_t arr[((256 + 31) / 32)];
};

typedef struct fd_set_s fd_set;
# 105 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/select.h"
struct timeval;
int select(int nfds, fd_set *readfds, fd_set *writefds,
           fd_set *exceptfds, struct timeval *timeout);

int pselect(int nfds, fd_set *readfds, fd_set *writefds,
            fd_set *exceptfds, const struct timespec *timeout,
            const sigset_t *sigmask);
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h" 2
# 129 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
typedef clock_t hrtime_t;



struct timeval
{
  time_t tv_sec;
  long tv_usec;
};





struct itimerval
{
  struct timeval it_interval;
  struct timeval it_value;
};





struct timezone
{
  int tz_minuteswest;
  int tz_dsttime;
};
# 194 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
int gettimeofday( struct timeval *tv, struct timezone *tz);
# 217 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
int settimeofday( const struct timeval *tv,
                 const struct timezone *tz);
# 290 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
int getitimer(int which, struct itimerval *value);
# 340 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
int setitimer(int which, const struct itimerval *value,
              struct itimerval *ovalue);
# 377 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
int utimes( const char *path, const struct timeval times[2]);
int lutimes( const char *path, const struct timeval times[2]);
int futimesat(int dirfd, const char *path,
              const struct timeval times[2]);
# 400 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
int futimes(int fd, const struct timeval tv[2]);
# 413 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/time.h"
hrtime_t gethrtime(void);
# 31 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/timex.h" 2
# 56 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/timex.h"
struct timex
{
  unsigned int modes;
  long offset;
  long freq;
  long maxerror;
  long esterror;
  int status;
  long constant;
  long precision;
  long tolerance;
  struct timeval time;
  long tick;
  long ppsfreq;
  long jitter;
  int shift;
  long stabil;
  long jitcnt;
  long calcnt;
  long errcnt;
  long stbcnt;
  int tai;
};
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h" 2






# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h" 1
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/inttypes.h" 1
# 353 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/inttypes.h"
struct imaxdiv_s
{
  intmax_t quot;
  intmax_t rem;
};

typedef struct imaxdiv_s imaxdiv_t;
# 377 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/inttypes.h"
intmax_t imaxabs(intmax_t j);
imaxdiv_t imaxdiv(intmax_t number, intmax_t denom);
intmax_t strtoimax( const char *nptr, char **endptr, int base);
uintmax_t strtoumax( const char *nptr, char **endptr, int base);

intmax_t wcstoimax( const wchar_t *nptr, wchar_t **endptr, int base);
uintmax_t wcstoumax( const wchar_t *nptr, wchar_t **endptr, int base);
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h" 2
# 81 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
struct int64_s
{




  uint32_t ls;
  int32_t ms;

};

struct uint64_s
{




  uint32_t ls;
  uint32_t ms;

};

typedef struct invdiv_param32_s
{
  uint32_t mult;
  uint8_t shift;
} invdiv_param32_t;

typedef struct invdiv_param64_s
{
  uint64_t mult;
  uint8_t shift;
} invdiv_param64_t;
# 380 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
static __attribute__((always_inline)) inline
void invdiv_init_param32(uint32_t d, invdiv_param32_t *param)
{
  int l = (((d) > 0 && ((d) & (d - 1)) == 0) ? (((sizeof(d) <= sizeof(long)) ? ((8 * sizeof(long)) - __builtin_clzl(d)) : ((8 * sizeof(long long)) - __builtin_clzll(d))) - 1) : ((sizeof(d) <= sizeof(long)) ? ((8 * sizeof(long)) - __builtin_clzl(d)) : ((8 * sizeof(long long)) - __builtin_clzll(d))));
  uint64_t t1 = (uint64_t)1 << 32;
  uint64_t t2 = (uint64_t)1 << l;

  param->mult = (t1 * (t2 - d)) / d + 1;



  param->shift = l;

}
# 412 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
static __attribute__((always_inline)) inline
uint64_t invdiv_u32(uint32_t n, const invdiv_param32_t *param)
{
  uint8_t sh = param->shift;







  uint64_t m = param->mult;
  uint64_t t1 = (m * n) >> 32;
  uint32_t t2 = n;


  return (t1 + t2) >> sh;
}
# 469 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
static __attribute__((always_inline)) inline uint64_t invdiv_umulh64(uint64_t a, uint64_t b)
{
# 498 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
  __uint128_t a128 = a;
  __uint128_t res128 = a128 * b;
  return res128 >> 64;

}
# 519 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
static __attribute__((always_inline)) inline
void invdiv_init_param64(uint64_t d, invdiv_param64_t *param)
{
  int l = (((d) > 0 && ((d) & (d - 1)) == 0) ? (((sizeof(d) <= sizeof(long)) ? ((8 * sizeof(long)) - __builtin_clzl(d)) : ((8 * sizeof(long long)) - __builtin_clzll(d))) - 1) : ((sizeof(d) <= sizeof(long)) ? ((8 * sizeof(long)) - __builtin_clzl(d)) : ((8 * sizeof(long long)) - __builtin_clzll(d))));
  uint64_t t = ((uint64_t)1 << l) - d;
# 546 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
  __uint128_t one128 = 1;
  param->mult = (one128 << 64) * t / d + 1;

  param->shift = l - 1;
}
# 569 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
static __attribute__((always_inline)) inline
uint64_t invdiv_u64(uint64_t n, const invdiv_param64_t *param)
{
  uint64_t t1;
  uint64_t t2;
  uint64_t m = param->mult;
  uint8_t sh1 = 1;
  uint8_t sh2 = param->shift;






  t1 = invdiv_umulh64(n, m);
  t2 = (n - t1) >> sh1;

  return (t1 + t2) >> sh2;
}
# 628 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
void uadd32x64(uint32_t term1, const struct uint64_s *term2,
               struct uint64_s *sum);
# 644 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
void uadd64( const struct uint64_s *term1,
            const struct uint64_s *term2,
            struct uint64_s *sum);
# 664 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
void usub64x32( const struct uint64_s *minuend, uint32_t subtrahend,
               struct uint64_s *difference);
# 682 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
void usub64( const struct uint64_s *minuend,
            const struct uint64_s *subtrahend,
            struct uint64_s *difference);
# 699 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
void umul32(uint32_t factor1, uint32_t factor2,
            struct uint64_s *product);
# 715 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
void umul32x64(uint32_t factor1, const struct uint64_s *factor2,
              struct uint64_s *product);
# 731 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/lib/math32.h"
void umul64( const struct uint64_s *factor1,
            const struct uint64_s *factor2,
            struct uint64_s *product);
# 40 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h" 2
# 517 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
int clock_realtime2absticks( const struct timespec *reltime,
                            clock_t *absticks);
# 569 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
int clock_isleapyear(int year);
# 579 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
int clock_daysbeforemonth(int month, _Bool leap_year);
# 597 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
int clock_dayoftheweek(int mday, int month, int year);
# 610 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
time_t clock_calendar2utc(int year, int month, int day);
# 699 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
clock_t clock_systime_ticks(void);
# 718 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
void clock_systime_timespec( struct timespec *ts);
# 735 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
static __attribute__((always_inline)) inline uint64_t clock_systime_nsec(void)
{
  struct timespec ts;
  clock_systime_timespec(&ts);
  return ((&ts)->tv_sec * 1000000000L + (&ts)->tv_nsec);
}
# 813 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
clock_t perf_gettime(void);





void perf_convert(clock_t elapsed, struct timespec *ts);





unsigned long perf_getfreq(void);
# 835 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/clock.h"
int nxclock_gettime(clockid_t clock_id, struct timespec *tp);
# 36 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h" 2
# 165 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_init( sem_t *sem, int pshared, int32_t value);
# 190 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_destroy( sem_t *sem);
# 220 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_wait( sem_t *sem);
int nxsem_wait_slow( sem_t *sem);
# 247 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_trywait( sem_t *sem);
int nxsem_trywait_slow( sem_t *sem);
# 294 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_timedwait( sem_t *sem, const struct timespec *abstime);
# 341 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_clockwait( sem_t *sem, clockid_t clockid,
                    const struct timespec *abstime);
# 367 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_tickwait( sem_t *sem, uint32_t delay);
# 398 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_post( sem_t *sem);
int nxsem_post_slow( sem_t *sem);
# 427 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_get_value( sem_t *sem, int *sval);
# 490 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_open( sem_t **sem, const char *name, int oflags, ...);
# 518 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_close( sem_t *sem);
# 541 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_unlink( const char *name);
# 563 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_reset( sem_t *sem, int16_t count);
# 623 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_set_protocol( sem_t *sem, int protocol);
# 648 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_wait_uninterruptible( sem_t *sem);
# 679 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_timedwait_uninterruptible( sem_t *sem,
                                    const struct timespec *abstime);
# 712 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_clockwait_uninterruptible( sem_t *sem, clockid_t clockid,
                                    const struct timespec *abstime);
# 744 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_tickwait_uninterruptible( sem_t *sem, uint32_t delay);
# 764 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_getprioceiling( const sem_t *sem, int *prioceiling);
# 784 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/semaphore.h"
int nxsem_setprioceiling( sem_t *sem, int prioceiling,
                         int *old_ceiling);
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h" 2
# 48 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
struct mutex_s
{
  sem_t sem;



};

typedef struct mutex_s mutex_t;

struct rmutex_s
{
  mutex_t mutex;
  unsigned int count;
};

typedef struct rmutex_s rmutex_t;
# 117 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxmutex_init( mutex_t *mutex);
# 133 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
_Bool nxmutex_is_hold( mutex_t *mutex);
# 161 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxmutex_ticklock( mutex_t *mutex, uint32_t delay);
# 188 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxmutex_clocklock( mutex_t *mutex, clockid_t clockid,
                      const struct timespec *abstime);
# 215 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxmutex_timedlock( mutex_t *mutex, unsigned int timeout);
# 236 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxrmutex_lock( rmutex_t *rmutex);
# 261 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxrmutex_trylock( rmutex_t *rmutex);
# 289 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxrmutex_ticklock( rmutex_t *rmutex, uint32_t delay);
# 316 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxrmutex_clocklock( rmutex_t *rmutex, clockid_t clockid,
                       const struct timespec *abstime);
# 344 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxrmutex_timedlock( rmutex_t *rmutex, unsigned int timeout);
# 367 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxrmutex_unlock( rmutex_t *rmutex);
# 386 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxrmutex_breaklock( rmutex_t *rmutex, unsigned int *count);
# 405 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
int nxrmutex_restorelock( rmutex_t *rmutex, unsigned int count);
# 433 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline pid_t nxmutex_get_holder( mutex_t *mutex)
{
  uint32_t mholder = mutex->sem.val.mholder & ~((uint32_t)0x80000000);
  return (((mholder) & ((uint32_t)0x7ffffffe)) != ((uint32_t)0x7ffffffe)) ? (pid_t)mholder : -1;
}
# 454 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline _Bool nxmutex_is_locked( mutex_t *mutex)
{
  return (((mutex->sem.val.mholder) & ((uint32_t)0x7ffffffe)) != ((uint32_t)0x7ffffffe));
}
# 477 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxmutex_destroy( mutex_t *mutex)
{
  return nxsem_destroy(&mutex->sem);
}
# 502 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxmutex_lock( mutex_t *mutex)
{
  int ret;

  ret = nxsem_wait(&mutex->sem);
  if (ret >= 0)
    {
      ;
    }

  return ret;
}
# 536 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxmutex_trylock( mutex_t *mutex)
{
  int ret;

  ret = nxsem_trywait(&mutex->sem);
  if (ret >= 0)
    {
      ;
    }

  return ret;
}
# 569 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxmutex_unlock( mutex_t *mutex)
{
  return nxsem_post(&mutex->sem);
}
# 585 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline void nxmutex_reset( mutex_t *mutex)
{
  nxsem_reset(&mutex->sem, 1);
}
# 608 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxmutex_breaklock( mutex_t *mutex,
                                             unsigned int *locked)
{
  int ret = OK;

  *locked = 0;
  ret = nxmutex_unlock(mutex);
  if (ret >= 0)
    {
      *locked = 1;
    }

  return ret;
}
# 640 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxmutex_restorelock( mutex_t *mutex,
                                               unsigned int locked)
{
  return locked ? nxmutex_lock(mutex) : OK;
}
# 663 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxmutex_set_protocol( mutex_t *mutex,
                                                int protocol)
{
  return nxsem_set_protocol(&mutex->sem, protocol);
}
# 740 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxrmutex_init( rmutex_t *rmutex)
{
  rmutex->count = 0;
  return nxmutex_init(&rmutex->mutex);
}
# 761 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxrmutex_destroy( rmutex_t *rmutex)
{
  int ret = nxmutex_destroy(&rmutex->mutex);

  rmutex->count = 0;
  return ret;
}
# 783 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline _Bool nxrmutex_is_hold( rmutex_t *rmutex)
{
  return nxmutex_is_hold(&rmutex->mutex);
}
# 806 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline _Bool nxrmutex_is_recursive( rmutex_t *rmutex)
{
  return rmutex->count > 1;
}
# 826 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline int nxrmutex_get_holder( rmutex_t *rmutex)
{
  return nxmutex_get_holder(&rmutex->mutex);
}
# 847 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline _Bool nxrmutex_is_locked( rmutex_t *rmutex)
{
  return nxmutex_is_locked(&rmutex->mutex);
}
# 863 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mutex.h"
static __attribute__((always_inline)) inline void nxrmutex_reset( rmutex_t *rmutex)
{
  rmutex->count = 0;
  nxmutex_reset(&rmutex->mutex);
}
# 37 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h" 2
# 45 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 1
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/ioctl.h" 1
# 146 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/ioctl.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/serial/tioctl.h" 1
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/serial/tioctl.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/ioctl.h" 1
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/serial/tioctl.h" 2
# 215 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/serial/tioctl.h"
struct winsize
{
  uint16_t ws_row;
  uint16_t ws_col;

  uint16_t ws_xpixel;
  uint16_t ws_ypixel;
};



struct serial_rs485
{
  uint32_t flags;
  uint32_t delay_rts_before_send;
  uint32_t delay_rts_after_send;
};
# 147 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/ioctl.h" 2
# 859 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/ioctl.h"
struct geometry
{
  _Bool geo_available;
  _Bool geo_mediachanged;
  _Bool geo_writeenabled;
  blkcnt_t geo_nsectors;
  blksize_t geo_sectorsize;



  char geo_model[32 + 1];
};

struct partition_info_s
{
  size_t numsectors;
  size_t sectorsize;
  off_t startsector;






  char parent[32 + 1];
};

struct pipe_peek_s
{
  void *buf;
  size_t offset;
  size_t size;
};
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/uio.h" 1
# 44 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/uio.h"
struct uio
{
  const struct iovec *uio_iov;
  int uio_iovcnt;
  size_t uio_resid;
  size_t uio_offset_in_iov;




};
# 68 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/uio.h"
void uio_advance( struct uio *uio, size_t sz);
# 85 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/uio.h"
int uio_init( struct uio *uio, const struct iovec *iov, int iovcnt);
# 95 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/uio.h"
void uio_copyfrom( struct uio *uio, size_t offset, const void *buf,
                  size_t len);
# 106 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/uio.h"
void uio_copyto( struct uio *uio, size_t offset, void *buf,
                size_t len);
# 35 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 2

# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/uio.h" 1
# 61 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/uio.h"
struct iovec
{
  void *iov_base;
  size_t iov_len;
};
# 116 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/uio.h"
ssize_t readv(int fildes, const struct iovec *iov, int iovcnt);
# 158 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/uio.h"
ssize_t writev(int fildes, const struct iovec *iov, int iovcnt);

ssize_t preadv(int fildes, const struct iovec *iov, int iovcnt,
               off_t offset);

ssize_t pwritev(int fildes, const struct iovec *iov, int iovcnt,
                off_t offset);
# 37 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 2






# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/dirent.h" 1
# 113 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/dirent.h"
struct dirent
{
  ino_t d_ino;
  uint8_t d_type;
  char d_name[32 + 1];
};

typedef struct
{
  int fd;
  struct dirent entry;
} DIR;
# 145 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/dirent.h"
int closedir( DIR *dirp);
 DIR *opendir( const char *path);
 DIR *fdopendir(int fd);
 struct dirent *readdir( DIR *dirp);
int readdir_r( DIR *dirp, struct dirent *entry,
                     struct dirent **result);
void rewinddir( DIR *dirp);
void seekdir( DIR *dirp, off_t loc);
off_t telldir( DIR *dirp);
int scandir( const char *path, struct dirent ***namelist,
                   int (*filter)( const struct dirent *),
                   int (*compar)( const struct dirent **,
                                      const struct dirent **));
int alphasort( const struct dirent **a,
                     const struct dirent **b);
int versionsort( const struct dirent **a,
                       const struct dirent **b);

int dirfd( DIR *dirp);
# 44 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 2

# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/atomic.h" 1
# 162 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/atomic.h"
typedef _Atomic(int32_t) atomic_t;
typedef _Atomic(int64_t) atomic64_t;
# 46 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 2


# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h" 1
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/gran.h" 1
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h" 2





struct task_group_s;







struct mm_map_entry_s
{
  struct mm_map_entry_s *flink;
  void *vaddr;
  size_t length;
  off_t offset;
  int prot;
  int flags;
  union
  {
    void *p;
    int i;
  } priv;

  int (*msync)( struct mm_map_entry_s *entry, void *start,
               size_t length, int flags);
# 71 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
  int (*munmap)( struct task_group_s *group,
                struct mm_map_entry_s *entry,
                void *start,
                size_t length);
};



struct mm_map_s
{
  sq_queue_t mm_map_sq;
  size_t map_count;





  rmutex_t mm_map_mutex;
};
# 110 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
int mm_map_lock(void);
# 126 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
void mm_map_unlock(void);
# 143 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
void mm_map_initialize( struct mm_map_s *mm, _Bool kernel);
# 159 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
void mm_map_destroy( struct mm_map_s *mm);
# 261 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
int mm_map_add( struct mm_map_s *mm, struct mm_map_entry_s *entry);
# 281 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
 struct mm_map_entry_s *mm_map_next( struct mm_map_s *mm,
                           const struct mm_map_entry_s *entry);
# 300 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
 struct mm_map_entry_s *mm_map_find( struct mm_map_s *mm,
                                       const void *vaddr,
                                       size_t length);
# 327 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/mm/map.h"
int mm_map_remove( struct mm_map_s *mm,
                  struct mm_map_entry_s *entry);
# 49 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/spawn.h" 1
# 32 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/spawn.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/spawn.h" 1
# 77 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/spawn.h"
struct timespec;
struct posix_spawnattr_s
{


  uint8_t flags;
  uint8_t priority;
  uint8_t policy;






  sigset_t sigmask;
  size_t stacksize;


  void *stackaddr;






};

typedef struct posix_spawnattr_s posix_spawnattr_t;
# 114 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/spawn.h"
typedef void *posix_spawn_file_actions_t;
# 135 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/spawn.h"
int posix_spawn( pid_t *pid, const char *path,
      const posix_spawn_file_actions_t *file_actions,
      const posix_spawnattr_t *attr,
      char * const argv[], char * const envp[]);







int task_spawn( const char *name, main_t entry,
      const posix_spawn_file_actions_t *file_actions,
      const posix_spawnattr_t *attr,
      char * const argv[], char * const envp[]);






int posix_spawn_file_actions_init(
      posix_spawn_file_actions_t *file_actions);
int posix_spawn_file_actions_destroy(
      posix_spawn_file_actions_t *file_actions);



int posix_spawn_file_actions_addclose(
      posix_spawn_file_actions_t *file_actions,
      int fd);
int posix_spawn_file_actions_adddup2(
      posix_spawn_file_actions_t *file_actions,
      int fd1, int fd2);
int posix_spawn_file_actions_addopen(
      posix_spawn_file_actions_t *file_actions,
      int fd, const char *path, int oflags, mode_t mode);





int posix_spawnattr_init( posix_spawnattr_t *attr);

int posix_spawnattr_destroy( posix_spawnattr_t *attr);



int posix_spawnattr_getflags( const posix_spawnattr_t *attr,
                             short *flags);

int posix_spawnattr_getschedparam( const posix_spawnattr_t *attr,
                                  struct sched_param *param);
int posix_spawnattr_getschedpolicy( const posix_spawnattr_t *attr,
                                   int *policy);

int posix_spawnattr_getsigmask( const posix_spawnattr_t *attr,
                               sigset_t *sigmask);



int posix_spawnattr_setflags( posix_spawnattr_t *attr, short flags);

int posix_spawnattr_setschedparam( posix_spawnattr_t *attr,
                                  const struct sched_param *param);
int posix_spawnattr_setschedpolicy( posix_spawnattr_t *attr, int policy);

int posix_spawnattr_setsigmask( posix_spawnattr_t *attr,
                               const sigset_t *sigmask);



int posix_spawnattr_getstacksize( const posix_spawnattr_t *attr,
                                 size_t *stacksize);
int posix_spawnattr_setstacksize( posix_spawnattr_t *attr,
                                 size_t stacksize);
int posix_spawnattr_setpriority( posix_spawnattr_t *attr,
                                uint8_t priority);
uint8_t posix_spawnattr_getpriority( posix_spawnattr_t *attr);

int posix_spawnattr_getstackaddr( const posix_spawnattr_t *attr,
                                 void **stackaddr);
int posix_spawnattr_setstackaddr( posix_spawnattr_t *attr,
                                 void *stackaddr);
# 227 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/spawn.h"
void posix_spawn_file_actions_dump(
                          posix_spawn_file_actions_t *file_actions);
void posix_spawnattr_dump( posix_spawnattr_t *attr);
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/spawn.h" 2
# 44 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/spawn.h"
enum spawn_file_actions_e
{
  SPAWN_FILE_ACTION_NONE = 0,
  SPAWN_FILE_ACTION_CLOSE,
  SPAWN_FILE_ACTION_DUP2,
  SPAWN_FILE_ACTION_OPEN
};







struct spawn_general_file_action_s
{
  struct spawn_general_file_action_s *flink;
  enum spawn_file_actions_e action;
};

struct spawn_close_file_action_s
{
  struct spawn_general_file_action_s *flink;
  enum spawn_file_actions_e action;
  int fd;
};

struct spawn_dup2_file_action_s
{
  struct spawn_general_file_action_s *flink;
  enum spawn_file_actions_e action;
  int fd1;
  int fd2;
};

struct spawn_open_file_action_s
{
  struct spawn_general_file_action_s *flink;
  enum spawn_file_actions_e action;
  int fd;
  int oflags;
  mode_t mode;
  char path[1];

};
# 102 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/spawn.h"
void add_file_action( posix_spawn_file_actions_t *file_action,
                     struct spawn_general_file_action_s *entry);

struct tcb_s;
int spawn_file_actions( struct tcb_s *tcb,
                       const posix_spawn_file_actions_t *actions);

_Bool
spawn_file_is_duplicateable( const posix_spawn_file_actions_t *actions,
                            int fd, _Bool cloexec);
# 50 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 2


# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/spinlock_type.h" 1
# 70 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/spinlock_type.h"
typedef uint32_t spinlock_t;
# 80 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/spinlock_type.h"
typedef union rspinlock_u
{


  uint32_t val;
  struct
    {
      uint16_t owner;
      uint16_t count;
    };
} rspinlock_t;



typedef struct seqcount_s
{
  uint32_t sequence;
} seqcount_t;
# 53 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h" 2
# 181 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
struct file;
struct inode;
struct stat;
struct statfs;
struct pollfd;
struct mtd_dev_s;
struct uio;





struct fs_dirent_s
{
# 203 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
  struct inode *fd_root;



  char *fd_path;
};





struct file_operations
{


  int (*open)( struct file *filep);






  int (*close)( struct file *filep);
  ssize_t (*read)( struct file *filep, char *buffer,
                       size_t buflen);
  ssize_t (*write)( struct file *filep, const char *buffer,
                        size_t buflen);
  off_t (*seek)( struct file *filep, off_t offset, int whence);
  int (*ioctl)( struct file *filep, int cmd, unsigned long arg);
  int (*mmap)( struct file *filep,
                       struct mm_map_entry_s *map);
  int (*truncate)( struct file *filep, off_t length);

  int (*poll)( struct file *filep, struct pollfd *fds,
                       _Bool setup);
  ssize_t (*readv)( struct file *filep, struct uio *uio);
  ssize_t (*writev)( struct file *filep, struct uio *uio);




  int (*unlink)( struct inode *inode);

};
# 257 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
struct inode;
struct block_operations
{
  int (*open)( struct inode *inode);
  int (*close)( struct inode *inode);
  ssize_t (*read)( struct inode *inode, unsigned char *buffer,
                       blkcnt_t start_sector, unsigned int nsectors);
  ssize_t (*write)( struct inode *inode,
                        const unsigned char *buffer,
                        blkcnt_t start_sector, unsigned int nsectors);
  int (*geometry)( struct inode *inode,
                           struct geometry *geometry);
  int (*ioctl)( struct inode *inode, int cmd, unsigned long arg);

  int (*unlink)( struct inode *inode);

};







struct mountpt_operations
{






  int (*open)( struct file *filep, const char *relpath,
                       int oflags, mode_t mode);






  int (*close)( struct file *filep);
  ssize_t (*read)( struct file *filep, char *buffer,
                       size_t buflen);
  ssize_t (*write)( struct file *filep, const char *buffer,
                        size_t buflen);
  off_t (*seek)( struct file *filep, off_t offset, int whence);
  int (*ioctl)( struct file *filep, int cmd, unsigned long arg);
  int (*mmap)( struct file *filep,
                       struct mm_map_entry_s *map);
  int (*truncate)( struct file *filep, off_t length);
  int (*poll)( struct file *filep, struct pollfd *fds,
                       _Bool setup);
  ssize_t (*readv)( struct file *filep, struct uio *uio);
  ssize_t (*writev)( struct file *filep, struct uio *uio);
# 319 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
  int (*sync)( struct file *filep);
  int (*dup)( const struct file *oldp, struct file *newp);
  int (*fstat)( const struct file *filep, struct stat *buf);
  int (*fchstat)( const struct file *filep,
                          const struct stat *buf, int flags);



  int (*opendir)( struct inode *mountpt, const char *relpath,
                          struct fs_dirent_s **dir);
  int (*closedir)( struct inode *mountpt,
                           struct fs_dirent_s *dir);
  int (*readdir)( struct inode *mountpt,
                          struct fs_dirent_s *dir,
                          struct dirent *entry);
  int (*rewinddir)( struct inode *mountpt,
                            struct fs_dirent_s *dir);



  int (*bind)( struct inode *blkdriver, const void *data,
                       void **handle);
  int (*unbind)( void *handle, struct inode **blkdriver,
                         unsigned int flags);
  int (*statfs)( struct inode *mountpt, struct statfs *buf);



  int (*unlink)( struct inode *mountpt, const char *relpath);
  int (*mkdir)( struct inode *mountpt, const char *relpath,
                        mode_t mode);
  int (*rmdir)( struct inode *mountpt, const char *relpath);
  int (*rename)( struct inode *mountpt,
                         const char *oldrelpath,
                         const char *newrelpath);
  int (*stat)( struct inode *mountpt, const char *relpath,
                       struct stat *buf);
  int (*chstat)( struct inode *mountpt, const char *relpath,
                         const struct stat *buf, int flags);
  int (*syncfs)( struct inode *mountpt);
# 383 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
  int (*ioctldir)( struct inode *mountpt,
                           struct fs_dirent_s *dir,
                           int cmd, unsigned long arg);
# 396 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
  int (*permission)( struct inode *mountpt,
                             const char *relpath, int amode);
# 412 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
};
# 429 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
union inode_ops_u
{
  const struct file_operations *i_ops;

  const struct block_operations *i_bops;
  struct mtd_dev_s *i_mtd;
  const struct mountpt_operations *i_mops;
# 446 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
};



struct inode
{
  struct inode *i_parent;
  struct inode *i_peer;
  struct inode *i_child;
  atomic_t i_crefs;
  uint16_t i_flags;
  union inode_ops_u u;
  ino_t i_ino;
  size_t i_size;
# 468 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
  void *i_private;
  char i_name[1];
};
# 479 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
struct file
{
  int f_oflags;
  atomic_t f_refs;
  off_t f_pos;
  struct inode *f_inode;
  void *f_priv;



};

struct fd
{
  struct file *f_file;
  _Bool f_cloexec;
# 504 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
};
# 513 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
struct fdlist
{
  spinlock_t fl_lock;
  uint8_t fl_rows;
  struct fd **fl_fds;







  struct fd *fl_prefd;
  struct fd fl_prefds[8];
};
# 551 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
void fs_initialize(void);
# 576 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int register_driver( const char *path,
                    const struct file_operations *fops, mode_t mode,
                    void *priv);
# 605 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int register_driver_with_size( const char *path,
                              const struct file_operations *fops,
                              mode_t mode, void *priv, size_t size);
# 633 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int register_blockdriver( const char *path,
                         const struct block_operations *bops,
                         mode_t mode, void *priv);
# 663 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int register_merge_blockdriver( const char *merge, ...);
# 690 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int register_blockpartition( const char *partition,
                            mode_t mode, const char *parent,
                            off_t firstsector, off_t nsectors);
# 703 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int unregister_driver( const char *path);
# 713 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int unregister_blockdriver( const char *path);
# 841 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int nx_mount( const char *source, const char *target,
             const char *filesystemtype, unsigned long mountflags,
             const void *data);
# 862 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int nx_umount2( const char *target, unsigned int flags);
# 873 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
void fdlist_init( struct fdlist *list);
# 900 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
void fdlist_free( struct fdlist *list);
# 916 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int fdlist_count( struct fdlist *list);
# 930 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int fdlist_copy( struct fdlist *plist, struct fdlist *clist,
                const posix_spawn_file_actions_t *actions,
                _Bool cloexec);
# 953 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int fdlist_get2( struct fdlist *list, int fd,
                struct file **filep, struct fd **fdp);
# 989 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int fdlist_dupfile( struct fdlist *list, int oflags, int minfd,
                   struct file *filep);
# 1005 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_allocate_from_inode( struct inode *inode, int oflags, off_t pos,
                             void *priv, int minfd);
# 1021 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_dup( struct file *filep, int minfd, int flags);
# 1039 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_dup2( struct file *filep1, struct file *filep2);
# 1060 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int fdlist_dup3( struct fdlist *list, int fd1, int fd2, int flags);
# 1081 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int fdlist_dup2( struct fdlist *list, int fd1, int fd2);
# 1099 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int nx_dup2(int fd1, int fd2);
# 1122 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_open( struct file *filep, const char *path, int oflags, ...);
# 1147 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int fdlist_open( struct fdlist *list,
                const char *path, int oflags, ...);
# 1166 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int nx_open( const char *path, int oflags, ...);
# 1176 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
 struct file *file_allocate(void);
# 1186 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
void file_deallocate( struct file *filep);
# 1206 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_get2(int fd, struct file **filep, struct fd **fdp);
# 1242 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
void file_ref( struct file *filep);
# 1257 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_put( struct file *filep);
# 1275 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_close( struct file *filep);
# 1300 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int fdlist_close( struct fdlist *list, int fd);
# 1318 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int nx_close(int fd);
# 1334 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
 void *find_driver( const char *pathname);
# 1359 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int open_blockdriver( const char *pathname, int mountflags,
                     struct inode **ppinode);
# 1380 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int close_blockdriver( struct inode *inode);
# 1404 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int find_blockdriver( const char *pathname, int mountflags,
                     struct inode **ppinode);
# 1425 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int find_mtddriver( const char *pathname, struct inode **ppinode);
# 1443 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int close_mtddriver( struct inode *pinode);
# 1468 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
ssize_t file_read( struct file *filep, void *buf, size_t nbytes);
ssize_t file_readv( struct file *filep,
                   const struct iovec *iov, int iovcnt);
# 1493 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
ssize_t nx_read(int fd, void *buf, size_t nbytes);
ssize_t nx_readv(int fd, const struct iovec *iov, int iovcnt);
# 1522 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
ssize_t file_write( struct file *filep, const void *buf,
                   size_t nbytes);
ssize_t file_writev( struct file *filep,
                    const struct iovec *iov, int iovcnt);
# 1552 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
ssize_t nx_write(int fd, const void *buf, size_t nbytes);
ssize_t nx_writev(int fd, const struct iovec *iov, int iovcnt);
# 1565 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
ssize_t file_pread( struct file *filep, void *buf, size_t nbytes,
                   off_t offset);
# 1578 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
ssize_t file_pwrite( struct file *filep, const void *buf,
                    size_t nbytes, off_t offset);
# 1590 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
ssize_t file_sendfile( struct file *outfile, struct file *infile,
                      off_t *offset, size_t count);
# 1603 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
off_t file_seek( struct file *filep, off_t offset, int whence);
# 1619 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
off_t nx_seek(int fd, off_t offset, int whence);
# 1631 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_fsync( struct file *filep);
# 1643 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_syncfs( struct file *filep);
# 1655 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_truncate( struct file *filep, off_t length);
# 1667 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_mmap( struct file *filep, void *start, size_t length,
              int prot, int flags, off_t offset, void **mapped);
# 1679 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_munmap( void *start, size_t length);
# 1699 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_ioctl( struct file *filep, int req, ...);
# 1720 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_fcntl( struct file *filep, int cmd, ...);
# 1740 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_poll( struct file *filep, struct pollfd *fds, _Bool setup);
# 1765 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_fstat( struct file *filep, struct stat *buf);
# 1782 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int nx_fstat(int fd, struct stat *buf);
# 1799 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int nx_stat( const char *path, struct stat *buf, int resolve);
# 1824 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int file_fchstat( struct file *filep, struct stat *buf, int flags);
# 1841 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int nx_unlink( const char *pathname);
# 1925 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/fs/fs.h"
int map_anonymous( struct mm_map_entry_s *entry, _Bool kernel);
# 46 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h" 2
# 137 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h"
typedef ssize_t cookie_read_function_t( void *cookie, char *buf,
                                            size_t size);
typedef ssize_t cookie_write_function_t( void *cookie,
                                             const char *buf,
                                             size_t size);
typedef off_t cookie_seek_function_t( void *cookie,
                                          off_t *offset,
                                          int whence);
typedef int cookie_close_function_t( void *cookie);

typedef struct cookie_io_functions_t
{
  cookie_read_function_t *read;
  cookie_write_function_t *write;
  cookie_seek_function_t *seek;
  cookie_close_function_t *close;
} cookie_io_functions_t;
# 192 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h"
struct file_struct
{
  sq_entry_t fs_entry;
  rmutex_t fs_lock;
  cookie_io_functions_t fs_iofunc;
  void *fs_cookie;

  char *fs_bufstart;
  char *fs_bufend;
  char *fs_bufpos;
  char *fs_bufread;

  char fs_buffer[64];


  uint16_t fs_oflags;
  uint8_t fs_flags;

  uint8_t fs_nungotten;
  char fs_ungotten[2];

};




typedef struct file_struct FILE;
# 241 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h"
 FILE *lib_get_stream(int fd);



void clearerr( FILE *stream);
void clearerr_unlocked( FILE *stream);
int fclose( FILE *stream);
int fflush( FILE *stream);
int fflush_unlocked( FILE *stream);
int feof( FILE *stream);
int ferror( FILE *stream);
int fileno( FILE *stream);
int fgetc( FILE *stream);
int fgetc_unlocked( FILE *stream);
int fgetpos( FILE *stream, fpos_t *pos);
 char *fgets( char *s, int n, FILE *stream);
 char *fgets_unlocked( char *s, int n, FILE *stream);
 FILE *fopen( const char *path, const char *type) __attribute__((__malloc__(fclose, 1)));
int fprintf( FILE *stream, const char *format, ...)
       __attribute__((__format__(__printf__, 2, 3)));
int fputc(int c, FILE *stream);
int fputc_unlocked(int c, FILE *stream);
int fputs( const char *s, FILE *stream);
int fputs_unlocked( const char *s, FILE *stream);
size_t fread( void *ptr, size_t size, size_t n_items, FILE *stream);
size_t fread_unlocked( void *ptr, size_t size, size_t n_items,
                      FILE *stream);
 FILE *freopen( const char *path, const char *mode,
                  FILE *stream);
int fscanf( FILE *stream, const char *fmt, ...)
       __attribute__((__format__(__scanf__, 2, 3)));
int fseek( FILE *stream, long int offset, int whence);
int fseeko( FILE *stream, off_t offset, int whence);
int fsetpos( FILE *stream, const fpos_t *pos);
long ftell( FILE *stream);
off_t ftello( FILE *stream);
size_t fwrite( const void *ptr, size_t size, size_t n_items,
              FILE *stream);
size_t fwrite_unlocked( const void *ptr, size_t size, size_t n_items,
                       FILE *stream);
int getc( FILE *stream);
int getc_unlocked( FILE *stream);
int getchar(void);
int getchar_unlocked(void);
ssize_t getdelim( char **lineptr, size_t *n, int delimiter,
                 FILE *stream);
ssize_t getline( char **lineptr, size_t *n, FILE *stream);
 char *gets( char *s);
 char *gets_s( char *s, rsize_t n);
void rewind( FILE *stream);

void setbuf( FILE *stream, char *buf);
int setvbuf( FILE *stream, char *buffer, int mode, size_t size);
void setbuffer( FILE *stream, char *buf, size_t size);

int ungetc(int c, FILE *stream);

void flockfile( FILE *stream);
int ftrylockfile( FILE *stream);
void funlockfile( FILE *stream);





void perror( const char *s);
int printf( const char *fmt, ...) __attribute__((__format__(__printf__, 1, 2)));
int putc(int c, FILE *stream);
int putc_unlocked(int c, FILE *stream);
int putchar(int c);
int putchar_unlocked(int c);
int puts( const char *s);
int rename( const char *oldpath, const char *newpath);
int renameat(int olddirfd, const char *oldpath,
                int newdirfd, const char *newpath);
int sprintf( char *buf, const char *fmt, ...)
       __attribute__((__format__(__printf__, 2, 3)));
int asprintf( char **ptr, const char *fmt, ...)
       __attribute__((__format__(__printf__, 2, 3)));
int snprintf( char *buf, size_t size,
                const char *fmt, ...) __attribute__((__format__(__printf__, 3, 4)));
int sscanf( const char *buf, const char *fmt, ...)
       __attribute__((__format__(__scanf__, 2, 3)));

int scanf( const char *fmt, ...) __attribute__((__format__(__scanf__, 1, 2)));
int vasprintf( char **ptr, const char *fmt, va_list ap)
       __attribute__((__format__(__printf__, 2, 0)));
int vfprintf( FILE *stream, const char *fmt,
                va_list ap) __attribute__((__format__(__printf__, 2, 0)));
int vfscanf( FILE *stream, const char *fmt, va_list ap)
       __attribute__((__format__(__scanf__, 2, 0)));
int vprintf( const char *fmt, va_list ap) __attribute__((__format__(__printf__, 1, 0)));
int vscanf( const char *fmt, va_list ap) __attribute__((__format__(__scanf__, 1, 0)));
int vsnprintf( char *buf, size_t size, const char *fmt,
                 va_list ap) __attribute__((__format__(__printf__, 3, 0)));
int vsprintf( char *buf, const char *fmt, va_list ap)
       __attribute__((__format__(__printf__, 2, 0)));
int vsscanf( const char *buf, const char *fmt, va_list ap)
       __attribute__((__format__(__scanf__, 2, 0)));
# 348 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h"
 FILE *fdopen(int fd, const char *type) __attribute__((__malloc__(fclose, 1)));
int dprintf(int fd, const char *fmt, ...) __attribute__((__format__(__printf__, 2, 3)));
int vdprintf(int fd, const char *fmt, va_list ap)
       __attribute__((__format__(__printf__, 2, 0)));



 FILE *fopencookie( void *cookie, const char *mode,
                      cookie_io_functions_t io_funcs);



 FILE *fmemopen( void *buf, size_t size, const char *mode);
 FILE *open_memstream( char **bufp, size_t *sizep);



 FILE *tmpfile(void) __attribute__((__malloc__(fclose, 1)));
 char *tmpnam( char *s);
 char *tempnam( const char *dir, const char *pfx) __attribute__((__malloc__(__builtin_free, 1)));
int remove( const char *path);
# 382 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdio.h"
int nx_asprintf( char **ptr, const char *fmt, ...)
    __attribute__((__format__(__printf__, 2, 3)));
int nx_vasprintf( char **ptr, const char *fmt, va_list ap)
    __attribute__((__format__(__printf__, 2, 0)));
# 50 "cyclictest.c" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/getopt.h" 1
# 33 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/getopt.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/unistd.h" 1
# 357 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/unistd.h"
pid_t vfork(void);

pid_t getpid(void);
pid_t getpgid(pid_t pid);
pid_t getpgrp(void);
pid_t gettid(void);
pid_t getppid(void);
pid_t getsid(pid_t pid);
int setpgid(pid_t pid, pid_t pgid);
pid_t setsid(void);
pid_t tcgetpgrp(int fd);
int tcsetpgrp(int fd, pid_t pgrp);
void _exit(int status) __attribute__((noreturn));
unsigned int sleep(unsigned int seconds);
int usleep(useconds_t usec);
int pause(void);
int nice(int inc);

int daemon(int nochdir, int noclose);



int close(int fd);
int dup(int fd);
int dup2(int fd1, int fd2);
int dup3(int fd1, int fd2, int flags);
int fsync(int fd);
off_t lseek(int fd, off_t offset, int whence);
ssize_t read(int fd, void *buf, size_t nbytes);
ssize_t write(int fd, const void *buf, size_t nbytes);
ssize_t pread(int fd, void *buf, size_t nbytes, off_t offset);
ssize_t pwrite(int fd, const void *buf, size_t nbytes, off_t offset);
int ftruncate(int fd, off_t length);
int fchown(int fd, uid_t owner, gid_t group);
int lockf(int fd, int cmd, off_t len);



int isatty(int fd);

 char *ttyname(int fd);
int ttyname_r(int fd, char *buf, size_t buflen);



int getdtablesize(void);
int getpagesize(void);



 char *getlogin(void);
int getlogin_r( char *buf, size_t bufsize);
# 419 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/unistd.h"
int pipe(int pipefd[2]);
int pipe2(int pipefd[2], int flags);
# 433 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/unistd.h"
unsigned int alarm(unsigned int seconds);



int chdir( const char *path);
int fchdir(int fd);



 char *getcwd( char *buf, size_t size);
 char *get_current_dir_name(void);



int access( const char *path, int amode);
int faccessat(int dirfd, const char *path, int mode, int flags);
int rmdir( const char *pathname);
int unlink( const char *pathname);
int unlinkat(int dirfd, const char *pathname, int flags);
int truncate( const char *path, off_t length);
int link( const char *path1, const char *path2);
int linkat(int olddirfd, const char *path1,
               int newdirfd, const char *path2, int flags);
int symlink( const char *path1, const char *path2);
int symlinkat( const char *path1, int dirfd,
                  const char *path2);
ssize_t readlink( const char *path, char *buf, size_t bufsize);
ssize_t readlinkat(int dirfd, const char *path, char *buf,
                   size_t bufsize);
int chown( const char *path, uid_t owner, gid_t group);
int lchown( const char *path, uid_t owner, gid_t group);
int fchownat(int dirfd, const char *path, uid_t owner,
                 gid_t group, int flags);
# 479 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/unistd.h"
void swab( const void *src, void *dest, ssize_t nbytes);



int getopt(int argc, char * const argv[], const char *optstring);



 char **getoptargp(void);
 int *getopterrp(void);
 int *getoptindp(void);
 int *getoptoptp(void);

int gethostname( char *name, size_t namelen);
int sethostname( const char *name, size_t namelen);



long sysconf(int name);
long fpathconf(int fildes, int name);
long pathconf( const char *path, int name);
size_t confstr(int name, char *buf, size_t len);



int setuid(uid_t uid);
uid_t getuid(void);
int setgid(gid_t gid);
gid_t getgid(void);

int seteuid(uid_t uid);
uid_t geteuid(void);
int setegid(gid_t gid);
gid_t getegid(void);

int setreuid(uid_t ruid, uid_t euid);
int setregid(gid_t rgid, gid_t egid);

int getresuid( uid_t *ruid, uid_t *euid, uid_t *suid);
int getresgid( gid_t *rgid, gid_t *egid, gid_t *sgid);

int setresuid(uid_t ruid, uid_t euid, uid_t suid);
int setresgid(gid_t rgid, gid_t egid, gid_t sgid);

int getgroups(int, gid_t[]);
int issetugid(void);
int setgroups(int, const gid_t *);

int getentropy( void *buffer, size_t length);

void sync(void);
int syncfs(int fd);

int profil( unsigned short *buf, size_t bufsiz,
               size_t offset, unsigned int scale);

 char *getpass( const char *prompt);
# 34 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/getopt.h" 2
# 56 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/getopt.h"
struct option
{
  const char *name;
  int has_arg;
  int *flag;
  int val;
};
# 80 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/getopt.h"
int getopt_long(int argc, char * const argv[],
                const char *optstring,
                const struct option *longopts,
                int *longindex);
# 96 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/getopt.h"
int getopt_long_only(int argc, char * const argv[],
                     const char *optstring,
                     const struct option *longopts,
                     int *longindex);
# 51 "cyclictest.c" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdlib.h" 1
# 107 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdlib.h"
struct div_s
{
  int quot;
  int rem;
};

typedef struct div_s div_t;



struct ldiv_s
{
  long quot;
  long rem;
};

typedef struct ldiv_s ldiv_t;



struct lldiv_s
{
  long long quot;
  long long rem;
};

typedef struct lldiv_s lldiv_t;
# 151 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdlib.h"
void srand(unsigned int seed);
int rand(void);
int rand_r( unsigned int *seedp);
void lcong48( unsigned short int param[7]);
 unsigned short int *seed48( unsigned short int seed16v[3]);
void srand48(long int seedval);
long int jrand48( unsigned short int xsubi[3]);
long int lrand48(void);
long int mrand48(void);
long int nrand48( unsigned short int xsubi[3]);

double drand48(void);
double erand48( unsigned short int xsubi[3]);



long random(void);

void arc4random_buf( void *bytes, size_t nbytes);
uint32_t arc4random(void);



 char **get_environ_ptr(void);
 char *getenv( const char *name);
 char *secure_getenv( const char *name);
int putenv( const char *string);
int clearenv(void);
int setenv( const char *name, const char *value, int overwrite);
int unsetenv( const char *name);



void exit(int status) __attribute__((noreturn));
void quick_exit(int status) __attribute__((noreturn));
void abort(void) __attribute__((noreturn));
int atexit( void (*func)(void));
int at_quick_exit( void (*func)(void));
int on_exit( void (*func)(int, void *), void *arg);



void _Exit(int status) __attribute__((noreturn));
# 202 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdlib.h"
int system( const char *cmd);


 char *realpath( const char *path, char *resolved);



long strtol( const char *nptr, char **endptr, int base);
unsigned long strtoul( const char *nptr, char **endptr, int base);
long long strtoll( const char *nptr, char **endptr, int base);
unsigned long long strtoull( const char *nptr, char **endptr,
                            int base);
float strtof( const char *str, char **endptr);

double strtod( const char *str, char **endptr);


long double strtold( const char *str, char **endptr);


int atoi( const char *nptr);
long atol( const char *nptr);
long long atoll( const char *nptr);

double atof( const char *nptr);




 char *itoa(int val, char *str, int base);



int mblen( const char *s, size_t n);
int mbtowc( wchar_t *pwc, const char *s, size_t n);
size_t mbstowcs( wchar_t *dst, const char *src, size_t len);
int wctomb( char *s, wchar_t wchar);
size_t wcstombs( char *dst, const wchar_t *src, size_t len);



 void *malloc(size_t) __attribute__((__malloc__(__builtin_free, 1))) __attribute__((__alloc_size__(1)));
 void *valloc(size_t) __attribute__((__malloc__(__builtin_free, 1))) __attribute__((__alloc_size__(1)));
void free( void *);
 void *realloc( void *, size_t) __attribute__((__alloc_size__(2)));
 void *reallocarray( void *, size_t, size_t) __attribute__((__alloc_size__(2, 3)));
 void *memalign(size_t, size_t) __attribute__((__malloc__(__builtin_free, 1))) __attribute__((__alloc_size__(2)));
 void *zalloc(size_t) __attribute__((__malloc__(__builtin_free, 1))) __attribute__((__alloc_size__(1)));
 void *calloc(size_t, size_t) __attribute__((__malloc__(__builtin_free, 1))) __attribute__((__alloc_size__(1, 2)));
 void *aligned_alloc(size_t, size_t) __attribute__((__malloc__(__builtin_free, 1))) __attribute__((__alloc_size__(2)));
int posix_memalign( void **, size_t, size_t);
# 269 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/stdlib.h"
int abs(int j);
long int labs(long int j);
long long int llabs(long long int j);

div_t div(int number, int denom);
ldiv_t ldiv(long number, long denom);
lldiv_t lldiv(long long number, long long denom);



 char *mktemp( char *path_template);
int mkstemp( char *path_template);
 char *mkdtemp( char *path_template);



void qsort( void *base, size_t nel, size_t width,
                int (*compar)( const void *, const void *));



 void *bsearch( const void *key, const void *base, size_t nel,
                   size_t width, int (*compar)( const void *,
                   const void *));



 const char *getprogname(void);



int __cxa_atexit( void (*func)( void *), void *arg,
                 void *dso_handle);
# 52 "cyclictest.c" 2




# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/ioctl.h" 1
# 123 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/ioctl.h"
int ioctl(int fd, int req, ...);
# 57 "cyclictest.c" 2


# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h" 1
# 215 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
typedef void *pthread_addr_t;



typedef pthread_addr_t (*pthread_startroutine_t)(pthread_addr_t);
typedef pthread_startroutine_t pthread_func_t;

typedef void (*pthread_trampoline_t)(pthread_startroutine_t, pthread_addr_t);

struct pthread_attr_s
{
  uint8_t priority;
  uint8_t policy;
  uint8_t inheritsched;
  uint8_t detachstate;
# 239 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
  void *stackaddr;
  size_t stacksize;
  size_t guardsize;





};
# 259 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
struct pthread_condattr_s
{
  int pshared;
  clockid_t clockid;
};






struct pthread_cond_s
{
  sem_t sem;
  clockid_t clockid;
  int wait_count;
};
# 284 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
struct pthread_mutexattr_s
{
  uint8_t pshared : 1;
# 297 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
  uint8_t robust : 1;

};






struct pthread_mutex_s
{



  struct pthread_mutex_s *flink;
  uint8_t flags;
# 321 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
  mutex_t mutex;

};
# 380 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
struct pthread_barrierattr_s
{
  int pshared;
};


typedef struct pthread_barrierattr_s pthread_barrierattr_t;



struct pthread_barrier_s
{
  sem_t sem;
  unsigned int count;
  unsigned int wait_count;
  mutex_t mutex;
};


typedef struct pthread_barrier_s pthread_barrier_t;



struct pthread_once_s
{
  volatile _Bool done;
  pthread_mutex_t mutex;
};






struct pthread_rwlockattr_s
{
  int pshared;
};


typedef struct pthread_rwlockattr_s pthread_rwlockattr_t;



struct pthread_rwlock_s
{
  pthread_mutex_t lock;
  pthread_cond_t cv;
  unsigned int num_readers;
  unsigned int num_writers;
  _Bool write_in_progress;
};


typedef struct pthread_rwlock_s pthread_rwlock_t;
# 462 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
typedef void (*pthread_cleanup_t)( void *arg);



struct sched_param;
# 476 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
int pthread_attr_init( pthread_attr_t *attr);



int pthread_attr_destroy( pthread_attr_t *attr);



int pthread_attr_setschedpolicy( pthread_attr_t *attr, int policy);
int pthread_attr_getschedpolicy( const pthread_attr_t *attr,
                                int *policy);
int pthread_attr_setschedparam( pthread_attr_t *attr,
                               const struct sched_param *param);
int pthread_attr_getschedparam( const pthread_attr_t *attr,
                               struct sched_param *param);
int pthread_attr_setinheritsched( pthread_attr_t *attr,
                                 int inheritsched);
int pthread_attr_getinheritsched( const pthread_attr_t *attr,
                                 int *inheritsched);
int pthread_attr_getdetachstate( const pthread_attr_t *attr,
                                int *detachstate);
int pthread_attr_setdetachstate( pthread_attr_t *attr,
                                int detachstate);
# 512 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
int pthread_attr_setstackaddr( pthread_attr_t *attr, void *stackaddr);
int pthread_attr_getstackaddr( const pthread_attr_t *attr,
                              void **stackaddr);



int pthread_attr_setstacksize( pthread_attr_t *attr, size_t stacksize);
int pthread_attr_getstacksize( const pthread_attr_t *attr,
                              size_t *stacksize);



int pthread_attr_setstack( pthread_attr_t *attr,
                          void *stackaddr, size_t stacksize);
int pthread_attr_getstack( const pthread_attr_t *attr,
                          void **stackaddr, size_t *stacksize);



int pthread_attr_setscope( pthread_attr_t *attr, int scope);
int pthread_attr_getscope( const pthread_attr_t *attr, int *scope);



int pthread_attr_setguardsize( pthread_attr_t *attr, size_t guardsize);
int pthread_attr_getguardsize( const pthread_attr_t *attr,
                              size_t *guardsize);



int pthread_setname_np(pthread_t thread, const char *name);
int pthread_getname_np(pthread_t thread, char *name, size_t len);



 void *pthread_get_stackaddr_np(pthread_t thread);
ssize_t pthread_get_stacksize_np(pthread_t thread);
# 557 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
int pthread_create( pthread_t *thread, const pthread_attr_t *attr,
                   pthread_startroutine_t startroutine, pthread_addr_t arg);





int pthread_detach(pthread_t thread);





void pthread_exit(pthread_addr_t value) __attribute__((noreturn));
int pthread_cancel(pthread_t thread);
int pthread_setcancelstate(int state, int *oldstate);
int pthread_setcanceltype(int type, int *oldtype);
void pthread_testcancel(void);
# 592 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
int pthread_join(pthread_t thread, pthread_addr_t *value);





void pthread_yield(void);



pthread_t pthread_self(void);
pid_t pthread_gettid_np(pthread_t thread);



int pthread_equal(pthread_t t1, pthread_t t2);



int pthread_getschedparam(pthread_t thread, int *policy,
                          struct sched_param *param);
int pthread_setschedparam(pthread_t thread, int policy,
                          const struct sched_param *param);
int pthread_setschedprio(pthread_t thread, int prio);
# 631 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
int pthread_setconcurrency(int new_level);
int pthread_getconcurrency(void);



int pthread_key_create( pthread_key_t *key,
                       void (*destructor)( void *));
int pthread_setspecific(pthread_key_t key, const void *value);
 void *pthread_getspecific(pthread_key_t key);
int pthread_key_delete(pthread_key_t key);



int pthread_mutexattr_init( pthread_mutexattr_t *attr);
int pthread_mutexattr_destroy( pthread_mutexattr_t *attr);
int pthread_mutexattr_getpshared( const pthread_mutexattr_t *attr,
                                 int *pshared);
int pthread_mutexattr_setpshared( pthread_mutexattr_t *attr,
                                 int pshared);
int pthread_mutexattr_gettype( const pthread_mutexattr_t *attr,
                              int *type);
int pthread_mutexattr_settype( pthread_mutexattr_t *attr, int type);
int pthread_mutexattr_getprotocol( const pthread_mutexattr_t *attr,
                                  int *protocol);
int pthread_mutexattr_setprotocol( pthread_mutexattr_t *attr,
                                  int protocol);
int pthread_mutexattr_getrobust( const pthread_mutexattr_t *attr,
                                int *robust);
int pthread_mutexattr_setrobust( pthread_mutexattr_t *attr,
                                int robust);
int pthread_mutexattr_getprioceiling( const pthread_mutexattr_t *attr,
                                     int *prioceiling);
int pthread_mutexattr_setprioceiling( pthread_mutexattr_t *attr,
                                     int prioceiling);
int pthread_mutex_getprioceiling( const pthread_mutex_t *mutex,
                                 int *prioceiling);
int pthread_mutex_setprioceiling( pthread_mutex_t *mutex,
                                 int prioceiling, int *old_ceiling);



int pthread_mutex_init( pthread_mutex_t *mutex,
                       const pthread_mutexattr_t *attr);
int pthread_mutex_destroy( pthread_mutex_t *mutex);
int pthread_mutex_lock( pthread_mutex_t *mutex);
int pthread_mutex_timedlock( pthread_mutex_t *mutex,
                            const struct timespec *abs_timeout);
int pthread_mutex_trylock( pthread_mutex_t *mutex);
int pthread_mutex_unlock( pthread_mutex_t *mutex);




int pthread_mutex_consistent( pthread_mutex_t *mutex);




int pthread_condattr_init( pthread_condattr_t *attr);
int pthread_condattr_destroy( pthread_condattr_t *attr);
int pthread_condattr_getpshared( const pthread_condattr_t *attr,
                                int *pshared);
int pthread_condattr_setpshared( pthread_condattr_t *attr, int pshared);
int pthread_condattr_getclock( const pthread_condattr_t *attr,
                              clockid_t *clock_id);
int pthread_condattr_setclock( pthread_condattr_t *attr,
                              clockid_t clock_id);



int pthread_cond_init( pthread_cond_t *cond,
                      const pthread_condattr_t *attr);
int pthread_cond_destroy( pthread_cond_t *cond);



int pthread_cond_broadcast( pthread_cond_t *cond);
int pthread_cond_signal( pthread_cond_t *cond);



int pthread_cond_wait( pthread_cond_t *cond, pthread_mutex_t *mutex);



int pthread_cond_timedwait( pthread_cond_t *cond,
                           pthread_mutex_t *mutex,
                           const struct timespec *abstime);

int pthread_cond_clockwait( pthread_cond_t *cond,
                           pthread_mutex_t *mutex,
                           clockid_t clockid,
                           const struct timespec *abstime);



int pthread_barrierattr_destroy( pthread_barrierattr_t *attr);
int pthread_barrierattr_init( pthread_barrierattr_t *attr);
int pthread_barrierattr_getpshared( const pthread_barrierattr_t *attr,
                                   int *pshared);
int pthread_barrierattr_setpshared( pthread_barrierattr_t *attr,
                                   int pshared);



int pthread_barrier_destroy( pthread_barrier_t *barrier);
int pthread_barrier_init( pthread_barrier_t *barrier,
                         const pthread_barrierattr_t *attr,
                         unsigned int count);
int pthread_barrier_wait( pthread_barrier_t *barrier);



int pthread_once( pthread_once_t *once_control,
                 void (*init_routine)(void));



int pthread_rwlockattr_init( pthread_rwlockattr_t *attr);
int pthread_rwlockattr_destroy( pthread_rwlockattr_t *attr);
int pthread_rwlockattr_getpshared( const pthread_rwlockattr_t *attr,
                                  int *pshared);
int pthread_rwlockattr_setpshared( pthread_rwlockattr_t *attr,
                                  int pshared);



int pthread_rwlock_destroy( pthread_rwlock_t *rw_lock);
int pthread_rwlock_init( pthread_rwlock_t *rw_lock,
                        const pthread_rwlockattr_t *attr);
int pthread_rwlock_rdlock(pthread_rwlock_t *lock);
int pthread_rwlock_timedrdlock( pthread_rwlock_t *lock,
                               const struct timespec *abstime);
int pthread_rwlock_clockrdlock( pthread_rwlock_t *lock,
                               clockid_t clockid,
                               const struct timespec *abstime);
int pthread_rwlock_tryrdlock( pthread_rwlock_t *lock);
int pthread_rwlock_wrlock( pthread_rwlock_t *lock);
int pthread_rwlock_timedwrlock( pthread_rwlock_t *lock,
                               const struct timespec *abstime);
int pthread_rwlock_clockwrlock( pthread_rwlock_t *lock,
                               clockid_t clockid,
                               const struct timespec *abstime);
int pthread_rwlock_trywrlock( pthread_rwlock_t *lock);
int pthread_rwlock_unlock( pthread_rwlock_t *lock);
# 787 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/pthread.h"
int pthread_getcpuclockid(pthread_t thread_id, clockid_t *clock_id);

int pthread_atfork( void (*prepare)(void),
                   void (*parent)(void),
                   void (*child)(void));
# 60 "cyclictest.c" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/poll.h" 1
# 30 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/poll.h"
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/poll.h" 1
# 96 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/poll.h"
typedef unsigned int nfds_t;






typedef uint32_t pollevent_t;



struct pollfd;
typedef void (*pollcb_t)( struct pollfd *fds);
# 119 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/poll.h"
struct pollfd
{


  int fd;
  pollevent_t events;
  pollevent_t revents;



  void *arg;
  pollcb_t cb;
  void *priv;
};
# 151 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/sys/poll.h"
int poll( struct pollfd *fds, nfds_t nfds, int timeout);

int ppoll( struct pollfd *fds, nfds_t nfds,
          const struct timespec *timeout_ts,
          const sigset_t *sigmask);

void poll_default_cb( struct pollfd *fds);
void poll_notify( struct pollfd **afds, int nfds, pollevent_t eventset);
# 31 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/poll.h" 2
# 61 "cyclictest.c" 2
# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/fcntl.h" 1
# 204 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/fcntl.h"
struct flock
{
  int16_t l_type;
  int16_t l_whence;
  off_t l_start;
  off_t l_len;
  pid_t l_pid;
};
# 232 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/fcntl.h"
int creat( const char *pathname, mode_t mode);
int open( const char *path, int oflag, ...);
int openat(int dirfd, const char *path, int oflag, ...);
int fcntl(int fd, int cmd, ...);

int posix_fallocate(int fd, off_t offset, off_t len);
# 62 "cyclictest.c" 2

# 1 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/timers/timer.h" 1
# 145 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/timers/timer.h"
typedef _Bool (*tccb_t)( uint32_t *next_interval, void *arg);





struct timer_status_s
{
  uint32_t flags;
  uint32_t timeout;
  uint32_t timeleft;

};



struct timer_notify_s
{
  struct sigevent event;
  pid_t pid;
  _Bool periodic;
};





struct timer_lowerhalf_s;
struct timer_ops_s
{




  int (*start)( struct timer_lowerhalf_s *lower);



  int (*stop)( struct timer_lowerhalf_s *lower);



  int (*getstatus)( struct timer_lowerhalf_s *lower,
                        struct timer_status_s *status);



  int (*settimeout)( struct timer_lowerhalf_s *lower,
                         uint32_t timeout);






  void (*setcallback)( struct timer_lowerhalf_s *lower,
                           tccb_t callback, void *arg);





  int (*ioctl)( struct timer_lowerhalf_s *lower, int cmd,
                    unsigned long arg);



  int (*maxtimeout)( struct timer_lowerhalf_s *lower,
                         uint32_t *maxtimeout);



  int (*tick_getstatus)( struct timer_lowerhalf_s *lower,
                             struct timer_status_s *status);



  int (*tick_settimeout)( struct timer_lowerhalf_s *lower,
                               uint32_t timeout);



  int (*tick_maxtimeout)( struct timer_lowerhalf_s *lower,
                              uint32_t *maxtimeout);
};







struct timer_lowerhalf_s
{


  const struct timer_ops_s *ops;




};
# 264 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/timers/timer.h"
static inline
int timer_getstatus( struct timer_lowerhalf_s *lower,
                    struct timer_status_s *status)
{
  int ret;

  if (lower->ops->tick_getstatus == ((void*)0))
    {
      return -138;
    }

  ret = lower->ops->tick_getstatus(lower, status);
  if (ret >= 0)
    {
      status->timeout = ((status->timeout) * (0L + 1));
      status->timeleft = ((status->timeleft) * (0L + 1));
    }

  return ret;
}

static inline
int timer_settimeout( struct timer_lowerhalf_s *lower,
                     uint32_t timeout)
{
  if (lower->ops->tick_settimeout == ((void*)0))
    {
      return -138;
    }

  return lower->ops->tick_settimeout(lower, (((timeout) + ((0L + 1)) - 1) / ((0L + 1))));
}

static inline
int timer_maxtimeout( struct timer_lowerhalf_s *lower,
                     uint32_t *maxtimeout)
{
  int ret;

  if (lower->ops->tick_maxtimeout == ((void*)0))
    {
      return -138;
    }

  ret = lower->ops->tick_maxtimeout(lower, maxtimeout);
  if (ret >= 0)
    {
      *maxtimeout = ((*maxtimeout) * (0L + 1));
    }

  return ret;
}

static inline
int timer_tick_getstatus( struct timer_lowerhalf_s *lower,
                         struct timer_status_s *status)
{
  int ret;

  if (lower->ops->getstatus == ((void*)0))
    {
      return -138;
    }

  ret = lower->ops->getstatus(lower, status);
  if (ret >= 0)
    {
      status->timeout = (((status->timeout) + ((0L + 1)) - 1) / ((0L + 1)));
      status->timeleft = (((status->timeleft) + ((0L + 1)) - 1) / ((0L + 1)));
    }

  return ret;
}

static inline
int timer_tick_settimeout( struct timer_lowerhalf_s *lower,
                          uint32_t timeout)
{
  if (lower->ops->settimeout == ((void*)0))
    {
      return -138;
    }

  return lower->ops->settimeout(lower, ((timeout) * (0L + 1)));
}

static inline
int timer_tick_maxtimeout( struct timer_lowerhalf_s *lower,
                          uint32_t *maxtimeout)
{
  int ret;

  if (lower->ops->maxtimeout == ((void*)0))
    {
      return -138;
    }

  ret = lower->ops->maxtimeout(lower, maxtimeout);
  if (ret >= 0)
    {
      *maxtimeout = (((*maxtimeout) + ((0L + 1)) - 1) / ((0L + 1)));
    }

  return ret;
}
# 404 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/timers/timer.h"
 void *timer_register( const char *path,
                         struct timer_lowerhalf_s *lower);
# 422 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/timers/timer.h"
void timer_unregister( void *handle);
# 447 "/root/kfusion-pi4/build/nuttx-build/nuttx/include/nuttx/timers/timer.h"
int timer_setcallback( void *handle, tccb_t callback, void *arg);
# 64 "cyclictest.c" 2
# 73 "cyclictest.c"
enum meas_method_e
{
  M_GETTIME = 0,
  M_TIMER_API,
  M_COUNT
};

enum wait_method_e
{
  W_NANOSLEEP = 0,
  W_DEVTIMER,
  W_COUNT
};

struct cyclictest_config_s
{
  int clock;
  int distance;
  int duration;
  int histogram;
  int histofall;
  unsigned long interval;
  unsigned long loops;
  int threads;
  int policy;
  int prio;
  _Bool quiet;
  char *timer_dev;
  enum meas_method_e meas_method;
  enum wait_method_e wait_method;
};

struct thread_param_s
{
  int prio;
  int policy;
  unsigned long interval;
  unsigned long max_cycles;
  struct thread_stats_s *stats;
  int clock;
};

struct thread_stats_s
{
  long *hist_array;
  long hist_overflow;
  long min;
  long max;
  long act;
  double avg;
  unsigned long cycles;
  pthread_t id;
  int tid;
  _Bool ended;
};

static _Bool running;
static struct cyclictest_config_s config;
static int timerfd;
static struct pollfd polltimer[1];

static const struct option optargs[] =
{
  {"clock", 2, 0, 'c'},
  {"distance", 2, 0, 'd'},
  {"duration", 2, 0, 'D'},
  {"help", 2, 0, 'e'},
  {"histogram", 2, 0, 'h'},
  {"histofall", 2, 0, 'H'},
  {"interval", 2, 0, 'i'},
  {"loops", 2, 0, 'l'},
  {"measurement", 2, 0, 'm'},
  {"nanosleep", 2, 0, 'n'},
  {"prio", 2, 0, 'p'},
  {"quiet", 2, 0, 'q'},
  {"threads", 2, 0, 't'},
  {"timer-device", 2, 0, 'T'},
  {"policy", 2, 0, 'y'},
  {0, 0, 0, 0}
};





static void print_help(void)
{
  puts(
    "The Cyclictest Benchmark Utility\n"
    "Usage:\n"
    "  -c --clock [CLOCK]: selects the clock: 0 selects CLOCK_REALTIME, "
    "1 selects CLOCK_MONOTONIC (default).\n"
    "  -d --distance [US]: The distance of thread intervals. "
    "Default is 500us.\n"
    "  -D --duration [TIME]: Test duration length in seconds. "
    "Default is 0 (endless).\n"
    "  -e --help: Display this help and quit.\n"
    "  -h --histogram [US]: Output the histogram data to stdout. "
    "US is the maximum value to be printed.\n"
    "  -H --histofall: Same as -h except that an additional histogram "
    "column\n"
    "     is displayed at the right that contains summary data of all thread"
    " histograms.\n"
    "     If cyclictest runs a single thread only, the -H option is "
    "equivalent to -h.\n"
    "  -i --interval [US]: The thread interval. Default is 1000us.\n"
    "  -l --loops [N]: The number of measurement loops. Default is 0 "
    "(endless).\n"
    "  -m --measurement [METHOD]: Set the time measurement method:\n"
    "     0 selects clock_gettime, 1 uses the NuttX timer API.\n"
    "     WARNING:\n"
    "       If METHOD 1 is selected, you need to specify a timer device "
    "(e.g. /dev/timer0) in -T.\n"
    "  -n --nanosleep [METHOD]: Set the waiting method: 0 selects "
    "clock_nanosleep,\n"
    "     1 waits for the POLLIN flag on a timer device. Default is 0.\n"
    "     WARNING:\n"
    "       Choosing 1 works only with one thread, "
    "the -t value is therefore set to 1.\n"
    "       If METHOD 1 is selected, you need to specify a timer device "
    "(e.g. /dev/timer0) in -T.\n"
    "  -p --prio: Set the priority of the first thread.\n"
    "  -q --quiet: Print a summary only on exit.\n"
    "  -t --threads [N]: The number of test threads to be created. "
    "Default is 1.\n"
    "  -T --timer-device [DEV]: The measuring timer device.\n"
    "     Must be specified when -m=1 or -n=1.\n"
    "  -y --policy [NAME]: Set the scheduler policy, where NAME is \n"
    "     fifo, rr, batch, idle, normal, other.\n"
  );
}

static long arg_decimal(char *arg)
{
  long ret;
  char *endptr;
  ret = strtol(arg, &endptr, 10);
  if (endptr == arg)
    {
      return -1;
    }

  return ret;
}

static _Bool parse_args(int argc, char * const argv[])
{
  int longindex;
  int opt;
  long decimal;
  while ((opt = getopt_long(argc, argv, "c:d:D:h:Hi:l:m:n:p:qt:T:",
                  optargs, &longindex)) != -1)
    {
      switch (opt)
        {
          case 'c':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal < 0)
              {
                return 0;
              }
            else if (decimal == 1 || decimal == 0)
              {
                config.clock = decimal;
              }
            break;
          case 'd':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal >= 0)
              {
                config.distance = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'D':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal >= 0)
              {
                config.duration = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'e':
            return 1;
          case 'h':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal >= 0)
              {
                config.histogram = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'H':
            config.histofall = 1;
            break;
          case 'i':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal >= 0)
              {
                config.interval = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'l':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal >= 0)
              {
                config.loops = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'm':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal >= 0)
              {
                config.meas_method = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'n':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal >= 0)
              {
                config.wait_method = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'p':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal >= 0 && decimal <= 255)
              {
                config.prio = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'q':
            config.quiet = 1;
            break;
          case 't':
            decimal = arg_decimal((*(getoptargp())));
            if (decimal > 0)
              {
                config.threads = decimal;
              }
            else
              {
                return 0;
              }
            break;
          case 'T':
            config.timer_dev = (*(getoptargp()));
            break;
          case 'y':
            if (strcmp((*(getoptargp())), "other") == 0)
              {
                config.policy = 0;
              }
            else if (strcmp((*(getoptargp())), "normal") == 0)
              {
                config.policy = 0;
              }
            else if (strcmp((*(getoptargp())), "batch") == 0)
              {
                config.policy = 4;
              }
            else if (strcmp((*(getoptargp())), "idle") == 0)
              {
                config.policy = 5;
              }
            else if (strcmp((*(getoptargp())), "fifo") == 0)
              {
                config.policy = 1;
              }
            else if (strcmp((*(getoptargp())), "rr") == 0)
              {
                config.policy = 2;
              }
            else
              {
                return 0;
              }
            break;
          case '?':
            return 0;
          default:
            break;
        }
    }

  if ((*(getoptindp())) < argc)
    {
      return 0;
    }

  return 1;
}

static _Bool check_args_logic(void)
{


  if (config.wait_method == W_DEVTIMER || config.meas_method == M_TIMER_API)
    {
      if (config.timer_dev == ((void*)0))
        {
          fprintf(lib_get_stream(2), "Specify timer device!\n");
          return 0;
        }
    }



  if (config.wait_method == W_DEVTIMER)
    {
      config.threads = 1;
    }



  if (config.prio == 0)
    {
      config.prio = config.threads;
    }

  if (config.wait_method >= W_COUNT)
    {
      return 0;
    }

  if (config.meas_method >= M_COUNT)
    {
      return 0;
    }

  return 1;
}

static inline void tsnorm(struct timespec *ts)
{
  while (ts->tv_nsec >= 1000000000L)
    {
      ts->tv_nsec -= 1000000000L;
      ts->tv_sec++;
    }
}

static inline int64_t timediff_us(struct timespec t1, struct timespec t2)
{
  int64_t ret;
  ret = 1000000 * (t1.tv_sec - t2.tv_sec);
  ret += (t1.tv_nsec - t2.tv_nsec) / 1000;
  return ret;
}

static inline int64_t timediff_us_timer(struct timer_status_s after,
                                        struct timer_status_s before)
{
  int64_t ret = 0;
  uint32_t t1;
  uint32_t t2;
  t1 = before.timeleft;
  t2 = after.timeleft;
  if (t2 < t1)
    {
      ret = t1 - t2;
    }
  else
    {
      ret = after.timeout - (t2 - t1);
    }

  return ret;
}

static inline int timerdev_getstatus(struct timer_status_s *st)
{
  return ioctl(timerfd, (((0x1600))|(0x0003)), (unsigned long)((uintptr_t)st));
}

static void *testthread(void *arg)
{
  int ret;
  int32_t newint;
  int64_t diff = 0;
  struct timer_status_s stamp1;
  struct timer_status_s stamp2;
  struct timespec now;
  struct timespec next;
  struct timespec interval;
  struct timespec endtime;
  struct sched_param schedp;
  struct thread_param_s *param = (struct thread_param_s *)arg;
  struct thread_stats_s *stats = param->stats;

  stats->tid = gettid();
  stats->min = 9223372036854775807LL;
  interval.tv_sec = param->interval / 1000000;
  interval.tv_nsec = (param->interval % 1000000) * 1000;



  schedp.sched_priority = param->prio;
  ret = pthread_setschedparam(pthread_self(), param->policy, &schedp);
  if (ret < 0)
    {
      goto threadend;
    }

  if (config.wait_method == W_DEVTIMER)
    {


      ret = ioctl(timerfd, (((0x1600))|(0x0001)));
      if (ret < 0)
        {
          perror("TCIOC_START");
          goto threadend;
        }
    }



  if ((ret = clock_gettime(param->clock, &now)) < 0)
    {
      goto threadend;
    }

  endtime.tv_sec = now.tv_sec + config.duration;
  endtime.tv_nsec = now.tv_nsec;

  while (running)
    {


      if (config.meas_method == M_TIMER_API)
        {
          ret = timerdev_getstatus(&stamp1);
          if (ret < 0)
            {
              perror("TCIOC_GETSTAUS");
              goto threadend;
            }
        }

      switch (config.wait_method)
        {
          case W_NANOSLEEP:
            if (config.meas_method == M_TIMER_API)
              {




                newint = stamp1.timeleft - param->interval;
                if (newint < 0)
                  {
                    stamp1.timeleft = stamp1.timeout + newint;
                  }
              }

            next = now;
            next.tv_sec += interval.tv_sec;
            next.tv_nsec += interval.tv_nsec;
            tsnorm(&next);
            ret = clock_nanosleep(param->clock, 1, &next, ((void*)0));
            if (ret < 0)
              {
                goto threadend;
              }
            break;
          case W_DEVTIMER:
            if (config.meas_method == M_TIMER_API)
              {




                stamp1.timeleft = stamp1.timeout;
              }
            else if (config.meas_method == M_GETTIME)
              {






                ret = timerdev_getstatus(&stamp1);
                if (ret < 0)
                  {
                    perror("TCIOC_GETSTATUS");
                    goto threadend;
                  }

                ret = clock_gettime(param->clock, &next);
                if (ret < 0)
                  {
                    goto threadend;
                  }

                next.tv_sec += (stamp1.timeleft) / 1000000;
                next.tv_nsec += (stamp1.timeleft % 1000000) * 1000;
                tsnorm(&next);
              }

            if ((ret = poll(polltimer, 1, -1)) < 0)
              {
                goto threadend;
              }
            break;
          default:
            break;
        }



      switch (config.meas_method)
        {
          case M_GETTIME:
            if ((ret = clock_gettime(param->clock, &now)) < 0)
              {
                goto threadend;
              }

            diff = timediff_us(now, next);
            break;
          case M_TIMER_API:
            ret = timerdev_getstatus(&stamp2);
            if (ret < 0)
              {
                perror("TCIOC_GETSTAUS");
                goto threadend;
              }

            diff = timediff_us_timer(stamp2, stamp1);
            break;
          default:
            break;
        }

      stats->act = diff;
      if (diff < stats->min)
        {
          stats->min = diff;
        }

      if (diff > stats->max)
        {
          stats->max = diff;
        }

      stats->avg += (double) diff;

      if (config.histogram)
        {
          if (diff < config.histogram && diff >= 0)
            {
              stats->hist_array[diff] += 1;
            }
          else
            {
              stats->hist_overflow += 1;
            }
        }

      ++stats->cycles;
      if (param->max_cycles != 0 && stats->cycles >= param->max_cycles)
        {
          stats->ended = 1;
          break;
        }

      if (config.duration != 0)
        {
          if ((ret = clock_gettime(param->clock, &now)) < 0)
            {
              goto threadend;
            }

          if (timediff_us(now, endtime) >= 0)
            {
              stats->ended = 1;
              break;
            }
        }
    }

threadend:
  return ((void*)0);
}

static inline void init_thread_param(struct thread_param_s *param,
                                     unsigned long interval,
                                     unsigned long max_cycles,
                                     int policy,
                                     int prio,
                                     struct thread_stats_s *stats,
                                     int clock)
{
  stats->avg = 0.0;
  stats->cycles = 0;
  stats->max = -9223372036854775807LL;
  stats->min = 9223372036854775807LL;
  stats->hist_overflow = 0;
  stats->ended = 0;

  param->interval = interval;
  param->max_cycles = max_cycles;
  param->policy = policy;
  param->prio = prio;
  param->stats = stats;
  param->clock = clock;
}





static void print_hist(struct thread_param_s *par[], int nthreads)
{
  int i;
  int j;
  unsigned long long int log_entries[nthreads + 1];
  unsigned long maxmax;
  unsigned long alloverflows;

  memset(log_entries,0,sizeof(log_entries));

  printf("# Histogram\n");
  for (i = 0; i < config.histogram; i++)
    {
      unsigned long long int allthreads = 0;

      printf("%06d ", i);

      for (j = 0; j < nthreads; j++)
        {
          unsigned long curr_latency = par[j]->stats->hist_array[i];
          printf("%06lu", curr_latency);
          if (j < nthreads - 1)
            {
              printf("\t");
            }

          log_entries[j] += curr_latency;
          allthreads += curr_latency;
        }

      if (config.histofall && nthreads > 1)
        {
          printf("\t%06llu", allthreads);
          log_entries[nthreads] += allthreads;
        }

      printf("\n");
    }

  printf("# Total:");

  for (j = 0; j < nthreads; j++)
    {
      printf(" %09llu", log_entries[j]);
    }

  if (config.histofall && nthreads > 1)
    {
      printf(" %09llu", log_entries[nthreads]);
    }

  printf("\n");
  printf("# Min Latencies:");

  for (j = 0; j < nthreads; j++)
    {
      printf(" %05lu", par[j]->stats->min);
    }

  printf("\n");
  printf("# Avg Latencies:");

  for (j = 0; j < nthreads; j++)
    {
      printf(" %05lu", par[j]->stats->cycles ?
             (long)(par[j]->stats->avg / par[j]->stats->cycles) : 0);
    }

  printf("\n");
  printf("# Max Latencies:");

  maxmax = 0;
  for (j = 0; j < nthreads; j++)
    {
      printf(" %05lu", par[j]->stats->max);
      if (par[j]->stats->max > maxmax)
        {
          maxmax = par[j]->stats->max;
        }
    }

  if (config.histofall && nthreads > 1)
    {
      printf(" %05lu", maxmax);
    }

  printf("\n");
  printf("# Histogram Overflows:");

  alloverflows = 0;
  for (j = 0; j < nthreads; j++)
    {
      printf(" %05lu", par[j]->stats->hist_overflow);
      alloverflows += par[j]->stats->hist_overflow;
    }

  if (config.histofall && nthreads > 1)
    {
      printf(" %05lu", alloverflows);
    }

  printf("\n");
}





static void print_stat(struct thread_param_s *par, int index)
{
  struct thread_stats_s *stat = par->stats;
  char *fmt;
  fmt = "T:%2d (%5d) P:%2d I:%ld C:%7lu "
        "Min:%7ld Act:%5ld Avg:%5ld Max:%8ld\n";
  printf(fmt, index, stat->tid, par->prio, par->interval, stat->cycles,
         stat->min, stat->act,
         stat->cycles ? (long)(stat->avg / stat->cycles) : 0, stat->max);
}





int main(int argc, char *argv[])
{
  int i;
  int ret;
  struct thread_param_s **params = ((void*)0);
  struct thread_stats_s **stats = ((void*)0);
  struct sigevent event;
  struct timer_notify_s tnotify;
  uint32_t maxtimeout_timer;
  uint32_t reqtimeout_timer;

  running = 1;
  config.clock = 1;
  config.distance = 500;
  config.duration = 0;
  config.histogram = 0;
  config.histofall = 0;
  config.interval = 1000;
  config.loops = 0;
  config.threads = 1;
  config.prio = 0;
  config.policy = 1;
  config.meas_method = M_GETTIME;
  config.wait_method = W_NANOSLEEP;
  config.timer_dev = ((void*)0);
  config.quiet = 0;

  if (!parse_args(argc, argv))
    {
      print_help();
      return ERROR;
    }

  if (!check_args_logic())
    {
      print_help();
      return ERROR;
    }



  if (config.wait_method == W_DEVTIMER || config.meas_method == M_TIMER_API)
    {
      timerfd = open(config.timer_dev, (2U << 0));
      if (timerfd < 0)
        {
          perror("Failed to open the device timer");
          return ERROR;
        }



      polltimer[0].fd = timerfd;
      polltimer[0].events = (0x01);






      memset(&event, 0, sizeof(event));
      event.sigev_notify = 0;

      tnotify.periodic = 1;
      tnotify.pid = getpid();
      tnotify.event = event;
# 925 "cyclictest.c"
      if (config.wait_method == W_DEVTIMER)
        {
          reqtimeout_timer = config.interval;
        }
      else
        {


          reqtimeout_timer = 3 * (config.interval +
                             (config.threads - 1) * config.distance);
        }

      ret = ioctl(timerfd, (((0x1600))|(0x0006)),
                  (unsigned long)((uintptr_t)&maxtimeout_timer));
      if (ret < 0)
        {
          perror("TCIOC_MAXTIMEOUT");
          goto errtimer;
        }

      if (reqtimeout_timer > maxtimeout_timer)
        {
          fprintf(lib_get_stream(2), "The timer cannot measure such periods!\n");
          goto errtimer;
        }

      ret = ioctl(timerfd, (((0x1600))|(0x0004)),
                  (unsigned long)reqtimeout_timer);
      if (ret < 0)
        {
          perror("TCIOC_SETTIMEOUT");
          goto errtimer;
        }

      ret = ioctl(timerfd, (((0x1600))|(0x0005)),
                  (unsigned long)((uintptr_t)&tnotify));
      if (ret < 0)
        {
          perror("TCIOC_NOTIFICATION");
          goto errtimer;
        }





      if (config.wait_method != W_DEVTIMER)
        {
          ret = ioctl(timerfd, (((0x1600))|(0x0001)));
          if (ret < 0)
            {
              perror("TCIOC_START");
              goto errtimer;
            }
        }
    }

  params = calloc(config.threads, sizeof(struct thread_param_s *));
  if (params == ((void*)0))
    {
      perror("params");
      ret = ERROR;
      goto main_error;
    }

  stats = calloc(config.threads, sizeof(struct thread_stats_s *));
  if (stats == ((void*)0))
    {
      perror("stats");
      ret = ERROR;
      goto main_error;
    }

  for (i = 0; i < config.threads; ++i)
    {
      params[i] = malloc(sizeof(struct thread_param_s));
      if (params == ((void*)0))
        {
          perror("params[i]");
          ret = ERROR;
          goto main_error;
        }

      stats[i] = malloc(sizeof(struct thread_stats_s));
      if (params == ((void*)0))
        {
          perror("stats[i]");
          ret = ERROR;
          goto main_error;
        }

      stats[i]->hist_array = calloc(config.histogram, sizeof(long));
      if (stats[i]->hist_array == ((void*)0))
        {
          perror("hist_array");
          ret = ERROR;
          goto main_error;
        }

      init_thread_param(params[i], config.interval, config.loops,
                 config.policy, config.prio, stats[i], config.clock);

      pthread_create(&stats[i]->id, ((void*)0), testthread, params[i]);
      config.interval += config.distance;
      if (config.prio > 1)
        {
          config.prio--;
        }
    }

  while (running)
    {


      usleep(100 * 1000);
      int ended = 0;
      for (i = 0; i < config.threads; ++i)
        {
          if (!config.quiet)
            {
              print_stat(params[i], i);
            }

          if (stats[i]->ended)
            {
              ended += 1;
            }
        }

      if (ended == config.threads)
        {
          running = 0;
        }
      else if (!config.quiet)
        {
          printf("\x1B[%dA", config.threads);
        }
    }

  for (i = 0; i < config.threads; ++i)
    {
      pthread_join(stats[i]->id, ((void*)0));
    }

  if (config.histogram)
    {
      print_hist(params, config.threads);
    }

  ret = OK;
  if (config.wait_method == W_DEVTIMER || config.meas_method == M_TIMER_API)
    {
      ret = ioctl(timerfd, (((0x1600))|(0x0002)));
      if (ret < 0)
        {
          perror("TCIOC_STOP");
          ret = ERROR;
        }

      close(timerfd);
    }

main_error:
  if (stats != ((void*)0))
    {
      for (i = 0; i < config.threads; ++i)
        {
          if (params[i] != ((void*)0))
            {
              free(params[i]);
            }

          if (stats[i] != ((void*)0))
            {
              if (stats[i]->hist_array != ((void*)0))
                {
                  free(stats[i]->hist_array);
                }

              free(stats[i]);
            }
        }
    }

  free(stats);
  return ret;

errtimer:
  close(timerfd);
  return ERROR;
}
