/*
 * 1 ms periodic loop for PLCFusion vs Timed C comparison on Pi.
 * Matches cyclictest -i 1000 intent (1 kHz soft periodic via sdelay).
 */
#include <stdio.h>
#include <cilktc.h>

static volatile unsigned long iter;

static void tick(void)
{
    iter++;
}

int main(void)
{
    int ov;

    spolicy(FIFO_RM);
    printf("timedc periodic_1ms: 1 ms sdelay loop (use taskset + chrt -f 99 externally)\n");
    SOFT_PERIODIC_LOOP(1, ms, ov, tick);
    return 0;
}
