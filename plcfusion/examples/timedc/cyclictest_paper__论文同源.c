/*
 * Timed C workload aligned with paper cyclictest protocol.
 *
 * Upstream reference (same repo / parameters as PLCFusion manifest):
 *   test/rt-tests/src/cyclictest/cyclictest.c
 *   userspace/fused flags: -p 99 -i 1000 -m -q -h 100000
 *
 * Export: TIMEDC_JITTER_BIN → jitter.bin v1 (same layout as fused ring export)
 *         for plot_frequency_polygon__抖动绘图.py dual PNG output.
 */
#include <stdio.h>
#include <signal.h>
#include <time.h>
#include <stdint.h>
#include <cilktc.h>

#define INTERVAL_US 1000L
#define INTERVAL_NS (INTERVAL_US * 1000L)
#define MAX_DECIM_SAMPLES 72000U
#define FUSED_RING_MAGIC 0x504C434A
#define JITTER_HDR_V1 40
#define DEFAULT_DECIM_STRIDE 50U
/* Shell copies this file to TIMEDC_JITTER_BIN after run (CIL cannot use getenv). */
#define TIMEDC_JITTER_EXPORT "/tmp/timedc_export.jitter.bin"

static volatile sig_atomic_t stop;
static long min_lat;
static long max_lat;
static long long sum_lat;
static unsigned long cycles;
static long long decim_buf[MAX_DECIM_SAMPLES];
static unsigned long decim_count;
static unsigned long decim_stride;

static long timespec_diff_ns(const struct timespec *a, const struct timespec *b)
{
    return (long)(a->tv_sec - b->tv_sec) * 1000000000L
         + (long)(a->tv_nsec - b->tv_nsec);
}

static void write_jitter_bin(const char *path)
{
    FILE *fp;
    uint32_t sample_count;
    uint32_t reserved;
    uint32_t magic;
    uint32_t version;
    uint64_t cycles_u64;
    int64_t min_ns;
    int64_t max_ns;
    unsigned long i;

    if (path == NULL || path[0] == '\0' || decim_count == 0) {
        return;
    }
    fp = fopen(path, "wb");
    if (fp == NULL) {
        return;
    }
    sample_count = (uint32_t)decim_count;
    reserved = 0U;
    magic = (uint32_t)FUSED_RING_MAGIC;
    version = 1U;
    cycles_u64 = (uint64_t)cycles;
    min_ns = (int64_t)min_lat;
    max_ns = (int64_t)max_lat;
    fwrite(&magic, 4, 1, fp);
    fwrite(&version, 4, 1, fp);
    fwrite(&cycles_u64, 8, 1, fp);
    fwrite(&min_ns, 8, 1, fp);
    fwrite(&max_ns, 8, 1, fp);
    fwrite(&sample_count, 4, 1, fp);
    fwrite(&reserved, 4, 1, fp);
    for (i = 0; i < decim_count; i++) {
        int64_t v = (int64_t)decim_buf[i];
        fwrite(&v, 8, 1, fp);
    }
    fclose(fp);
    printf("TimedCBinSummary: path=%s cycles=%lu decim=%u abs_max_ns=%ld\n",
           path, cycles, sample_count,
           (long)(max_lat > 0 ? max_lat : -min_lat));
    fflush(stdout);
}

static void print_summary(void)
{
    long avg = 0;
    long abs_max_ns;

    if (cycles > 0) {
        avg = (long)(sum_lat / (long long)cycles);
    }
    abs_max_ns = max_lat > 0 ? max_lat : -min_lat;

    printf("T: 0 (timedc) P:99 I:%ld C:%lu Min:%7ld Act:%7ld Avg:%7ld Max:%7ld\n",
           INTERVAL_US, cycles, min_lat / 1000, max_lat / 1000, avg / 1000, max_lat / 1000);
    printf("TimedCSummary: abs_max_ns=%ld min_ns=%ld max_ns=%ld cycles=%lu decim=%lu source=rt-tests/cyclictest.c\n",
           abs_max_ns, min_lat, max_lat, cycles, decim_count);

    write_jitter_bin(TIMEDC_JITTER_EXPORT);
    fflush(stdout);
}

static void on_signal(int sig)
{
    (void)sig;
    stop = 1;
}

int main(void)
{
    struct timespec t0, t1;
    long lat;

    min_lat = 0;
    max_lat = 0;
    sum_lat = 0;
    cycles = 0;
    decim_count = 0;
    decim_stride = DEFAULT_DECIM_STRIDE;
    stop = 0;

    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);

    spolicy(FIFO_RM);
    printf("# Timed C cyclictest-paper: sdelay 1ms, ref rt-tests cyclictest.c (-p99 -i1000)\n");
    printf("# decim_stride=%lu export=%s\n", decim_stride, TIMEDC_JITTER_EXPORT);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    while (!stop) {
        sdelay(1, ms);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        lat = timespec_diff_ns(&t1, &t0) - INTERVAL_NS;
        if (cycles == 0 || lat < min_lat) {
            min_lat = lat;
        }
        if (lat > max_lat) {
            max_lat = lat;
        }
        sum_lat += lat;
        if ((cycles % decim_stride) == 0U && decim_count < MAX_DECIM_SAMPLES) {
            decim_buf[decim_count] = (long long)lat;
            decim_count++;
        }
        cycles++;
        t0.tv_sec = t1.tv_sec;
        t0.tv_nsec = t1.tv_nsec;
    }

    print_summary();
    return 0;
}
