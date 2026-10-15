/****************************************************************************
 * apps/benchmarks/rtlat/rtlat_main.c
 *
 * 实时调度抖动测量 (inmate / cRTOS 域内)
 *
 * 在 NuttX(Jailhouse 非根 cell) 内测高优先级周期任务的唤醒抖动，
 * 逻辑等价 rt-tests/cyclictest 的核心：高优先级线程 + 周期
 * clock_nanosleep(TIMER_ABSTIME) 唤醒，用 clock_gettime 量实际唤醒时刻
 * 相对期望时刻的偏差。
 *
 * 用法: rtlat [-i <interval_us>] [-l <loops>] [-p <prio>]
 *   默认 interval=1000us, loops=60000, prio=10
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <sched.h>
#include <unistd.h>

#define NS_PER_SEC 1000000000L
#define NS_PER_US  1000L
#define HIST_SIZE  200000   /* 直方图: 0..199999 us, 1us 一桶 */

int main(int argc, FAR char *argv[])
{
  int interval_us = 1000;
  long loops = 60000;
  int prio = 10;
  int i;

  for (i = 1; i < argc; i++)
    {
      if (strcmp(argv[i], "-i") == 0 && i + 1 < argc)
        {
          interval_us = atoi(argv[++i]);
        }
      else if (strcmp(argv[i], "-l") == 0 && i + 1 < argc)
        {
          loops = atol(argv[++i]);
        }
      else if (strcmp(argv[i], "-p") == 0 && i + 1 < argc)
        {
          prio = atoi(argv[++i]);
        }
      else
        {
          printf("usage: rtlat [-i <us>] [-l <loops>] [-p <prio>]\n");
          return 1;
        }
    }

  struct sched_param sp;
  sp.sched_priority = prio;
  if (sched_setscheduler(0, SCHED_FIFO, &sp) < 0)
    {
      printf("rtlat: sched_setscheduler(SCHED_FIFO, prio=%d) failed\n", prio);
    }

  unsigned long *hist = (unsigned long *)calloc(HIST_SIZE, sizeof(unsigned long));
  if (hist == NULL)
    {
      printf("rtlat: calloc failed\n");
      return 1;
    }

  struct timespec next;
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &next);

  long minlat = 0x7fffffff;
  long maxlat = 0;
  long long sum = 0;
  long over200 = 0;

  printf("rtlat START interval=%dus loops=%ld prio=%d\n",
         interval_us, loops, prio);

  for (i = 0; i < loops; i++)
    {
      next.tv_nsec += (long)interval_us * NS_PER_US;
      while (next.tv_nsec >= NS_PER_SEC)
        {
          next.tv_nsec -= NS_PER_SEC;
          next.tv_sec++;
        }

      clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
      clock_gettime(CLOCK_MONOTONIC, &now);

      long lat = (long)(now.tv_sec - next.tv_sec) * NS_PER_SEC
               + (long)(now.tv_nsec - next.tv_nsec);
      if (lat < 0)
        {
          lat = 0;
        }

      long us = lat / NS_PER_US;
      if (us < HIST_SIZE)
        {
          hist[us]++;
        }

      if (lat < minlat)
        {
          minlat = lat;
        }

      if (lat > maxlat)
        {
          maxlat = lat;
        }

      if (us > 200)
        {
          over200++;
        }

      sum += lat;
    }

  printf("rtlat RESULT loops=%ld min=%ldus avg=%lldus max=%ldus over200us=%ld\n",
         loops, minlat / NS_PER_US, sum / loops / NS_PER_US,
         maxlat / NS_PER_US, over200);

  printf("rtlat HISTOGRAM us:count (nonzero)\n");
  for (i = 0; i < HIST_SIZE; i++)
    {
      if (hist[i] != 0)
        {
          printf("%d:%lu ", i, hist[i]);
        }
    }

  printf("\nrtlat DONE\n");
  free(hist);
  return 0;
}
