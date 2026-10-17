/****************************************************************************
 * apps/benchmarks/rtlat/rtlat_main.c
 *
 * 实时调度抖动测量 (inmate / cRTOS 域内)
 *
 * 在 NuttX(Jailhouse 非根 cell) 内测高优先级周期任务的唤醒抖动。
 * 逻辑与 rt-tests/cyclictest 对齐：高优先级 SCHED_FIFO 线程 +
 * 周期 clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME) 唤醒，用
 * clock_gettime 量「实际唤醒时刻 − 理想截止时刻」的带符号偏差。
 *
 * 与 cyclictest 的一致性要点：
 *   - 偏差**带符号**（负 = 提前唤醒），不再截断为 0；
 *   - 直方图由 **全部样本** 累加（不抽样），桶宽可配（默认 100ns）；
 *   - 同时输出 min/avg/max 与 p50/p99/p99.9（由全样本直方图求得）；
 *   - 附带实测时钟粒度（clock_gettime 最小非零增量），说明测量下限。
 *
 * 用法: rtlat [-i <interval_us>] [-l <loops>] [-p <prio>] [-b <bin_ns>] [-d <stride>]
 *   -i 唤醒周期(us)，默认 1000
 *   -l 循环次数，默认 60000
 *   -p 优先级；不给则取 sched_get_priority_max(SCHED_FIFO)
 *   -b 直方图桶宽(ns)，默认 100（0.1us）
 *   -d 逐样本导出步长；0=不导出（默认），1=每个样本，
 *      n>1 每 n 个样本导出 1 个（与仓库 DECIM_STRIDE 同义，仅供时间线绘图）
 *
 * 输出行：
 *   rtlat START ...
 *   rtlat CLKRES granularity_ns=<实测>
 *   rtlat STATS loops=<n> min_ns=.. avg_ns=.. max_ns=.. p50_ns=.. p99_ns=.. p999_ns=.. over200us=..
 *   rtlat RESULT loops=<n> min=<us> avg=<us> max=<us> over200us=<n>      (兼容旧解析器)
 *   rtlat HIST bin_ns=<b> lo_ns=<l> nbins=<N>
 *   rtlat HISTDATA <bin_index>:<count> ...                               (仅非零桶)
 *   rtlat SAMPLE <idx> <ns>                                              (仅 -d 指定时)
 *   rtlat DONE
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

/* 直方图：覆盖 [lo_ns, lo_ns + nbins*bin) ；默认 100ns 桶、40000 桶 = 4ms 跨度
 * 用堆分配而不是 static 数组：static 会进 .bss，被 objcopy -O binary 展平成
 * 320KB 的零填充，使 nuttx.bin 从 ~360KB 涨到 ~680KB。 */
#define DEFAULT_BIN_NS   100L
#define DEFAULT_NBINS    40000
#define HIST_LO_NS       (-1000000L)   /* 覆盖 -1ms .. +3ms */

int main(int argc, FAR char *argv[])
{
  int interval_us = 1000;
  long loops = 60000;
  int prio = -1;
  long bin_ns = DEFAULT_BIN_NS;
  long stride = 0;
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
      else if (strcmp(argv[i], "-b") == 0 && i + 1 < argc)
        {
          bin_ns = atol(argv[++i]);
          if (bin_ns <= 0) bin_ns = DEFAULT_BIN_NS;
        }
      else if (strcmp(argv[i], "-d") == 0 && i + 1 < argc)
        {
          stride = atol(argv[++i]);
        }
      else
        {
          printf("usage: rtlat [-i <us>] [-l <loops>] [-p <prio>] "
                 "[-b <bin_ns>] [-d <stride>]\n");
          return 1;
        }
    }

  /* 优先级：不给就用该策略的最高值（NuttX 与 Linux 的数字语义不同，
   * 用 API 取而不是硬编码，避免踩坑） */
  if (prio < 0)
    {
      prio = sched_get_priority_max(SCHED_FIFO);
      if (prio < 1) prio = 1;
    }

  struct sched_param sp;
  sp.sched_priority = prio;
  if (sched_setscheduler(0, SCHED_FIFO, &sp) < 0)
    {
      printf("rtlat: sched_setscheduler(SCHED_FIFO, prio=%d) failed\n", prio);
    }

  /* 实测时钟粒度：连续读 clock_gettime，取最小非零增量 */
  long clkres_ns = 0;
  {
    struct timespec a, b;
    long mind = 0;
    clock_gettime(CLOCK_MONOTONIC, &a);
    for (i = 0; i < 2000; i++)
      {
        long d;
        clock_gettime(CLOCK_MONOTONIC, &b);
        d = (long)(b.tv_sec - a.tv_sec) * NS_PER_SEC
          + (long)(b.tv_nsec - a.tv_nsec);
        if (d > 0 && (mind == 0 || d < mind))
          {
            mind = d;
          }
        a = b;
      }
    clkres_ns = mind;
  }

  long long *hist = (long long *)calloc(DEFAULT_NBINS, sizeof(long long));
  if (hist == NULL)
    {
      printf("rtlat: calloc(%d) failed\n", DEFAULT_NBINS);
      return 1;
    }

  /* 逐样本缓冲：**先存内存、循环结束后再打印**。
   * 绝不能在实时循环里 printf —— 往串口写会阻塞并干扰被测对象
   * （仓库的做法同理：ringbuf 先采集，跑完再导出）。 */
  int32_t *samples = NULL;
  long ncap = 0;
  long nsamp = 0;
  if (stride > 0)
    {
      ncap = (loops + stride - 1) / stride + 1;
      samples = (int32_t *)malloc((size_t)ncap * sizeof(int32_t));
      if (samples == NULL)
        {
          printf("rtlat: malloc(%ld samples) failed; -d disabled\n", ncap);
          stride = 0;
          ncap = 0;
        }
    }

  struct timespec next;
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &next);

  long minlat = 0x7fffffff;
  long maxlat = -0x7fffffff;
  long long sum = 0;
  long over200 = 0;
  long n = 0;

  printf("rtlat START interval=%dus loops=%ld prio=%d bin_ns=%ld stride=%ld\n",
         interval_us, loops, prio, bin_ns, stride);
  printf("rtlat CLKRES granularity_ns=%ld\n", clkres_ns);

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

      /* 带符号偏差：负 = 提前唤醒（不截断，与 cyclictest 一致） */
      long lat = (long)(now.tv_sec - next.tv_sec) * NS_PER_SEC
               + (long)(now.tv_nsec - next.tv_nsec);

      long bin = (lat - HIST_LO_NS) / bin_ns;
      if (bin >= 0 && bin < DEFAULT_NBINS)
        {
          hist[bin]++;
        }

      if (lat < minlat) minlat = lat;
      if (lat > maxlat) maxlat = lat;
      if (lat > 200000L) over200++;

      sum += lat;
      n++;

      if (stride > 0 && (i % stride) == 0 && nsamp < ncap)
        {
          samples[nsamp++] = (int32_t)lat;   /* 只存内存，不打印 */
        }
    }

  /* 由全样本直方图求分位数 */
  long p50 = 0, p99 = 0, p999 = 0;
  {
    long long acc = 0;
    long long t50 = (long long)(n * 0.50);
    long long t99 = (long long)(n * 0.99);
    long long t999 = (long long)(n * 0.999);
    int got50 = 0, got99 = 0, got999 = 0;
    int b;
    for (b = 0; b < DEFAULT_NBINS; b++)
      {
        if (hist[b] == 0) continue;
        acc += hist[b];
        if (!got50 && acc >= t50)  { p50 = HIST_LO_NS + (long)b * bin_ns;  got50 = 1; }
        if (!got99 && acc >= t99)  { p99 = HIST_LO_NS + (long)b * bin_ns;  got99 = 1; }
        if (!got999 && acc >= t999){ p999 = HIST_LO_NS + (long)b * bin_ns; got999 = 1; }
      }
    if (!got50)  p50 = minlat;
    if (!got99)  p99 = maxlat;
    if (!got999) p999 = maxlat;
  }

  long long avg = (n > 0) ? (sum / n) : 0;

  printf("rtlat STATS loops=%ld n=%ld min_ns=%ld avg_ns=%ld max_ns=%ld "
         "p50_ns=%ld p99_ns=%ld p999_ns=%ld over200us=%ld clkres_ns=%ld\n",
         loops, n, minlat, (long)avg, maxlat, p50, p99, p999, over200, clkres_ns);

  /* 兼容旧解析器（截断为 us） */
  printf("rtlat RESULT loops=%ld min=%ldus avg=%ldus max=%ldus over200us=%ld\n",
         loops, minlat / NS_PER_US, (long)avg / NS_PER_US,
         maxlat / NS_PER_US, over200);

  printf("rtlat HIST bin_ns=%ld lo_ns=%ld nbins=%d\n",
         bin_ns, HIST_LO_NS, DEFAULT_NBINS);
  printf("rtlat HISTDATA");
  {
    int b;
    for (b = 0; b < DEFAULT_NBINS; b++)
      {
        if (hist[b] != 0)
          {
            printf(" %d:%lld", b, hist[b]);
          }
      }
  }
  /* 循环结束后再导出逐样本（每 stride 个取 1 个），不影响被测区间 */
  printf("\n");                     /* 必须先换行：HISTDATA 行不换行，否则与首行 SAMPLE 粘连 */
  if (stride > 0 && samples != NULL)
    {
      long k;
      for (k = 0; k < nsamp; k++)
        {
          printf("rtlat SAMPLE %ld %d\n", k * stride, samples[k]);
        }
    }

  printf("\nrtlat DONE\n");
  free(samples);
  free(hist);
  return 0;
}
