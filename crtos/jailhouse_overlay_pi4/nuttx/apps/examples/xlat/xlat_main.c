/****************************************************************************
 * apps/examples/xlat/xlat_main.c
 *
 * 跨域延迟测量（inmate 侧）：周期唤醒 + 打时间戳 + 经 IVSHMEM 发布
 *
 * 目的：在 NuttX（Jailhouse 非根 cell）里做两件事
 *   1) 测「inmate 内核内工作」的抖动      —— t_wake − t_deadline
 *   2) 发布时间戳，供 root cell 测「传回 Linux」的延迟与抖动
 *
 * 设计约束（重要）
 *   - **全程不 printf**：NuttX 串口是中断驱动 + 阻塞写，一旦在测量循环里打印
 *     就会污染被测对象（rtlat 的注释同样强调）。所有数据只走共享内存。
 *   - 样本不在 inmate 侧缓冲：1kHz × 8h = 2880 万样本，128MB cell 存不下。
 *     root 侧 busy-poll 实时收取并落盘，inmate 侧零内存增长。
 *   - cache：NuttX 侧 cacheable，root 侧 UIO mmap 非 cache，
 *     发布后必须 up_clean_dcache，否则 root 永远看不到更新。
 *
 * 共享布局（guest 虚拟 = phys 0x1faf1000，与 root 侧 xlat_collect 一致）
 *   0x00 u32 magic        0x584C4154 ('XLAT')
 *   0x04 u32 state        1=running 2=done 0=stop-requested(by root)
 *   0x08 u32 seq          每周期 +1（root 以此判断新样本）
 *   0x0C s32 jitter_ns    本次抖动 = t_wake − t_deadline
 *   0x10 s64 t_wake_ns    唤醒时刻（NuttX CLOCK_MONOTONIC）
 *   0x18 s64 t_deadline_ns 标称截止时刻
 *   0x20 s64 t_publish_ns 发布完成时刻（clean 之后，跨域延迟的起点）
 *   0x28 u32 count        已完成周期数
 *   0x2C s32 min_jitter
 *   0x30 s32 max_jitter
 *
 * 用法: xlat [-i <interval_us>] [-n <loops>] [-p <prio>]
 *   默认 interval=1000us(1kHz), loops=0(直到 root 置 state=0), prio=SCHED_FIFO max
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <sched.h>

#define XLAT_SHM_BASE   0x1faf1000u
#define XLAT_MAGIC      0x584C4154u   /* 'XLAT' */
#define XLAT_LINE       64u           /* cache line 粒度 */
#define NS_PER_SEC      1000000000LL
#define NS_PER_US       1000LL

#define OFF_MAGIC       0x00
#define OFF_STATE       0x04
#define OFF_SEQ         0x08
#define OFF_JITTER      0x0C
#define OFF_T_WAKE      0x10
#define OFF_T_DEADLINE  0x18
#define OFF_T_PUBLISH   0x20
#define OFF_COUNT       0x28
#define OFF_MIN         0x2C
#define OFF_MAX         0x30

extern void up_invalidate_dcache(uintptr_t start, uintptr_t end);
extern void up_clean_dcache(uintptr_t start, uintptr_t end);

static inline int64_t ts_to_ns(const struct timespec *ts)
{
  return (int64_t)ts->tv_sec * NS_PER_SEC + (int64_t)ts->tv_nsec;
}

int main(int argc, FAR char *argv[])
{
  long interval_us = 1000;
  long long loops = 0;          /* 0 = 无限，直到 root 置 state=0 */
  int prio = 0;
  int i;
  long long n;
  uintptr_t base = (uintptr_t)XLAT_SHM_BASE;
  volatile uint8_t *p = (volatile uint8_t *)XLAT_SHM_BASE;
  struct sched_param sp;
  struct timespec next;
  struct timespec now;
  struct timespec pub;
  int64_t t_deadline_ns;
  int64_t t_wake_ns;
  int64_t t_publish_ns;
  int32_t jitter;
  int32_t jmin = 0x7fffffff;
  int32_t jmax = -0x7fffffff;
  uint32_t seq = 0;

  for (i = 1; i < argc; i++)
    {
      if (strcmp(argv[i], "-i") == 0 && i + 1 < argc)
        {
          interval_us = atol(argv[++i]);
        }
      else if (strcmp(argv[i], "-n") == 0 && i + 1 < argc)
        {
          loops = atoll(argv[++i]);
        }
      else if (strcmp(argv[i], "-p") == 0 && i + 1 < argc)
        {
          prio = atoi(argv[++i]);
        }
      else
        {
          return 1;   /* 不打印：用法错误直接返回 */
        }
    }

  if (interval_us <= 0)
    {
      interval_us = 1000;
    }

  if (prio <= 0)
    {
      prio = sched_get_priority_max(SCHED_FIFO);
      if (prio < 1)
        {
          prio = 1;
        }
    }

  sp.sched_priority = prio;
  if (sched_setscheduler(0, SCHED_FIFO, &sp) < 0)
    {
      return 2;
    }

  /* 复位共享区（先 invalidate 再写，避免脏读） */

  up_invalidate_dcache(base, base + XLAT_LINE);
  *(volatile uint32_t *)(p + OFF_SEQ) = 0;
  *(volatile int32_t  *)(p + OFF_JITTER) = 0;
  *(volatile int64_t  *)(p + OFF_T_WAKE) = 0;
  *(volatile int64_t  *)(p + OFF_T_DEADLINE) = 0;
  *(volatile int64_t  *)(p + OFF_T_PUBLISH) = 0;
  *(volatile uint32_t *)(p + OFF_COUNT) = 0;
  *(volatile int32_t  *)(p + OFF_MIN) = 0;
  *(volatile int32_t  *)(p + OFF_MAX) = 0;
  *(volatile uint32_t *)(p + OFF_STATE) = 1;      /* running */
  *(volatile uint32_t *)(p + OFF_MAGIC) = XLAT_MAGIC;
  up_clean_dcache(base, base + XLAT_LINE);

  clock_gettime(CLOCK_MONOTONIC, &next);

  for (n = 0; loops == 0 || n < loops; n++)
    {
      /* 绝对时刻推进（不累积漂移） */

      next.tv_nsec += interval_us * NS_PER_US;
      while (next.tv_nsec >= NS_PER_SEC)
        {
          next.tv_nsec -= NS_PER_SEC;
          next.tv_sec++;
        }

      clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
      clock_gettime(CLOCK_MONOTONIC, &now);

      t_deadline_ns = ts_to_ns(&next);
      t_wake_ns = ts_to_ns(&now);
      jitter = (int32_t)(t_wake_ns - t_deadline_ns);   /* 带符号 */

      if (jitter < jmin)
        {
          jmin = jitter;
        }
      if (jitter > jmax)
        {
          jmax = jitter;
        }

      seq++;

      /* 发布顺序很重要：**先取 t_publish、写全部数据、clean，最后才写 seq**。
       * 若先写 seq 后写 t_publish，root 见 seq 变化时 t_publish 仍属上一周期，
       * 算出的延迟会整整差一个周期（实测 span 恰为 1000µs）。
       * 现在 t_publish = 数据就绪前的时刻，seq 写入 = 发布动作，
       * root 的 t_root − t_publish 即「生成 → Linux 可见」的真实延迟。 */

      clock_gettime(CLOCK_MONOTONIC, &pub);
      t_publish_ns = ts_to_ns(&pub);

      *(volatile int32_t *)(p + OFF_JITTER) = jitter;
      *(volatile int64_t *)(p + OFF_T_WAKE) = t_wake_ns;
      *(volatile int64_t *)(p + OFF_T_DEADLINE) = t_deadline_ns;
      *(volatile int64_t *)(p + OFF_T_PUBLISH) = t_publish_ns;
      *(volatile uint32_t *)(p + OFF_COUNT) = (uint32_t)(n + 1);
      *(volatile int32_t *)(p + OFF_MIN) = jmin;
      *(volatile int32_t *)(p + OFF_MAX) = jmax;
      up_clean_dcache(base, base + XLAT_LINE);

      *(volatile uint32_t *)(p + OFF_SEQ) = seq;
      up_clean_dcache(base, base + XLAT_LINE);

      /* 每 1024 周期检查一次 root 的停止请求（invalidate 后重读） */

      if ((seq & 0x3ffu) == 0)
        {
          uint32_t st;

          up_invalidate_dcache(base, base + XLAT_LINE);
          st = *(volatile uint32_t *)(p + OFF_STATE);
          if (st == 0)
            {
              break;
            }
        }
    }

  up_invalidate_dcache(base, base + XLAT_LINE);
  *(volatile uint32_t *)(p + OFF_STATE) = 2;      /* done */
  *(volatile uint32_t *)(p + OFF_COUNT) = (uint32_t)(n);
  *(volatile int32_t *)(p + OFF_MIN) = jmin;
  *(volatile int32_t *)(p + OFF_MAX) = jmax;
  up_clean_dcache(base, base + XLAT_LINE);

  return 0;
}
