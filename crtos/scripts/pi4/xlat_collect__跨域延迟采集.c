/*
 * xlat_collect.c — root cell 侧跨域延迟采集器
 *
 * 与 inmate 侧 apps/examples/xlat 配对：
 *   inmate 每周期发布 {seq, jitter_ns, t_wake, t_deadline, t_publish}
 *   本程序 busy-poll IVSHMEM rw_section（/dev/uio0 map2），
 *   一看到 seq 变化就立刻取 CLOCK_MONOTONIC_RAW，算出
 *       lat_raw = t_root_raw_ns − t_publish_ns
 *   lat_raw 含两域 epoch 差（NuttX 启动晚于 Linux，是一个常数），
 *   后处理时用 min(lat_raw) 作零点校准（one-way delay 标准做法）。
 *
 * 输出二进制（每条 16 字节，小端）：
 *   struct rec { int64 lat_raw; int32 jitter_ns; uint32 seq; }
 * 文件头（32 字节）：magic "XLATREC1", u32 version=1, u32 rec_size=16,
 *                    u64 n_records, u64 interval_us
 *
 * 用法: xlat_collect -d <seconds> -o <out.bin> [-c <cpu>] [-t <interval_us>]
 *   -c  绑核（Jailhouse 启用后 root 可用 0,2,3；默认 2）
 *   -t  inmate 的标称周期（仅用于估算容量，默认 1000us）
 */

#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <errno.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/stat.h>

#define XLAT_MAGIC      0x584C4154u
#define UIO_MAP_INDEX   2
#define UIO_PAGE        4096u
#define MAP_LEN         4096u

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

struct rec
{
  int64_t  lat_raw;
  int32_t  jitter_ns;
  uint32_t seq;
};

struct file_hdr
{
  char     magic[8];
  uint32_t version;
  uint32_t rec_size;
  uint64_t n_records;
  uint64_t interval_us;
};

static inline int64_t ts_ns(const struct timespec *ts)
{
  return (int64_t)ts->tv_sec * 1000000000LL + (int64_t)ts->tv_nsec;
}

int main(int argc, char **argv)
{
  long duration = 900;
  const char *out_path = "xlat.bin";
  int cpu = 2;
  long interval_us = 1000;
  int i;
  int fd;
  void *map;
  volatile uint8_t *p;
  struct rec *buf;
  size_t cap;
  size_t n = 0;
  uint32_t last_seq;
  uint32_t state = 0;
  struct timespec t0;
  struct timespec now;
  struct timespec t_root;
  int64_t t_root_ns;
  int64_t t_pub_ns;
  int32_t jitter;
  int64_t lat_raw;
  FILE *fp;

  for (i = 1; i < argc; i++)
    {
      if (strcmp(argv[i], "-d") == 0 && i + 1 < argc)
        {
          duration = atol(argv[++i]);
        }
      else if (strcmp(argv[i], "-o") == 0 && i + 1 < argc)
        {
          out_path = argv[++i];
        }
      else if (strcmp(argv[i], "-c") == 0 && i + 1 < argc)
        {
          cpu = atoi(argv[++i]);
        }
      else if (strcmp(argv[i], "-t") == 0 && i + 1 < argc)
        {
          interval_us = atol(argv[++i]);
        }
      else
        {
          fprintf(stderr,
                  "usage: xlat_collect -d <sec> -o <out.bin> [-c cpu] [-t interval_us]\n");
          return 1;
        }
    }

  if (interval_us <= 0)
    {
      interval_us = 1000;
    }

  /* 容量：按标称频率 + 20% 余量预分配，不够再倍增 */

  cap = (size_t)((double)duration * (1000000.0 / (double)interval_us) * 1.2) + 1024;
  buf = (struct rec *)malloc(cap * sizeof(struct rec));
  if (buf == NULL)
    {
      fprintf(stderr, "malloc(%zu recs) failed\n", cap);
      return 2;
    }

  /* 绑核 + 实时优先级（busy-poll 必须抢占普通任务，否则延迟测量被调度影响） */

  {
    cpu_set_t set;

    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0)
      {
        fprintf(stderr, "sched_setaffinity(cpu%d) failed: %s\n", cpu, strerror(errno));
      }
  }

  {
    struct sched_param sp;

    sp.sched_priority = 50;   /* 不与内核关键线程争 99 */
    if (sched_setscheduler(0, SCHED_FIFO, &sp) != 0)
      {
        fprintf(stderr, "sched_setscheduler(FIFO,50) failed: %s\n", strerror(errno));
      }
  }

  fd = open("/dev/uio0", O_RDWR | O_SYNC);
  if (fd < 0)
    {
      fprintf(stderr, "open /dev/uio0 failed: %s\n", strerror(errno));
      return 3;
    }

  map = mmap(NULL, MAP_LEN, PROT_READ | PROT_WRITE, MAP_SHARED, fd,
             (off_t)UIO_MAP_INDEX * UIO_PAGE);
  if (map == MAP_FAILED)
    {
      fprintf(stderr, "mmap uio map%d failed: %s\n", UIO_MAP_INDEX, strerror(errno));
      close(fd);
      return 4;
    }

  p = (volatile uint8_t *)map;

  if (*(volatile uint32_t *)(p + OFF_MAGIC) != XLAT_MAGIC)
    {
      fprintf(stderr,
              "warning: magic=0x%08x != XLAT(0x%08x); inmate 尚未启动 xlat?\n",
              *(volatile uint32_t *)(p + OFF_MAGIC), XLAT_MAGIC);
    }

  last_seq = *(volatile uint32_t *)(p + OFF_SEQ);
  clock_gettime(CLOCK_MONOTONIC_RAW, &t0);

  fprintf(stderr,
          "xlat_collect START cpu=%d duration=%lds interval=%ldus out=%s "
          "seq0=%u magic=0x%08x\n",
          cpu, duration, interval_us, out_path, last_seq,
          *(volatile uint32_t *)(p + OFF_MAGIC));

  /* busy-poll：这是本采集器的核心——不能用 sleep，否则测到的是轮询周期 */

  {
    size_t last_check = 0;
    uint32_t idle = 0;

    for (;;)
      {
        uint32_t seq = *(volatile uint32_t *)(p + OFF_SEQ);
        int do_check = 0;

        if (seq != last_seq)
          {
            clock_gettime(CLOCK_MONOTONIC_RAW, &t_root);
            t_root_ns = ts_ns(&t_root);

            jitter = *(volatile int32_t *)(p + OFF_JITTER);
            t_pub_ns = *(volatile int64_t *)(p + OFF_T_PUBLISH);
            lat_raw = t_root_ns - t_pub_ns;

            if (n == cap)
              {
                size_t ncap = cap * 2;
                struct rec *nb = (struct rec *)realloc(buf, ncap * sizeof(struct rec));

                if (nb == NULL)
                  {
                    fprintf(stderr, "realloc failed at n=%zu\n", n);
                    break;
                  }
                buf = nb;
                cap = ncap;
              }

            buf[n].lat_raw = lat_raw;
            buf[n].jitter_ns = jitter;
            buf[n].seq = seq;
            n++;

            last_seq = seq;
            idle = 0;

            if (n - last_check >= 2048)
              {
                last_check = n;
                do_check = 1;
              }
          }
        else if (++idle > 20000000u)
          {
            /* 空转超时：inmate 可能已停（state=2）或本就未起，兜底检查 */
            idle = 0;
            do_check = 1;
          }

        /* 时长/结束状态检查按样本计数（每 2048 条 ≈ 2s @1kHz）触发，
         * 不能每秒调 clock_gettime —— 那会在 busy-poll 里插入空隙，
         * 实测漏掉 4.7% 的样本。 */

        if (do_check)
          {
            state = *(volatile uint32_t *)(p + OFF_STATE);
            if (state == 2)
              {
                break;
              }
            clock_gettime(CLOCK_MONOTONIC, &now);
            if ((ts_ns(&now) - ts_ns(&t0)) >= (int64_t)duration * 1000000000LL)
              {
                break;
              }
          }
      }
  }

  /* 落盘 */

  fp = fopen(out_path, "wb");
  if (fp == NULL)
    {
      fprintf(stderr, "fopen %s failed: %s\n", out_path, strerror(errno));
      close(fd);
      return 5;
    }

  {
    struct file_hdr h;

    memset(&h, 0, sizeof(h));
    memcpy(h.magic, "XLATREC1", 8);
    h.version = 1;
    h.rec_size = sizeof(struct rec);
    h.n_records = (uint64_t)n;
    h.interval_us = (uint64_t)interval_us;
    fwrite(&h, sizeof(h), 1, fp);
  }

  fwrite(buf, sizeof(struct rec), n, fp);
  fclose(fp);

  {
    int64_t mn = INT64_MAX;
    int64_t mx = INT64_MIN;
    int32_t jmn = 0x7fffffff;
    int32_t jmx = -0x7fffffff;
    size_t k;

    for (k = 0; k < n; k++)
      {
        if (buf[k].lat_raw < mn) mn = buf[k].lat_raw;
        if (buf[k].lat_raw > mx) mx = buf[k].lat_raw;
        if (buf[k].jitter_ns < jmn) jmn = buf[k].jitter_ns;
        if (buf[k].jitter_ns > jmx) jmx = buf[k].jitter_ns;
      }

    fprintf(stderr,
            "xlat_collect DONE n=%zu lat_raw_min=%lld lat_raw_max=%lld "
            "span_us=%.3f jitter_min=%d jitter_max=%d state=%u\n",
            n, (long long)mn, (long long)mx,
            (double)(mx - mn) / 1000.0, jmn, jmx, state);
  }

  munmap(map, MAP_LEN);
  close(fd);
  free(buf);
  return 0;
}
