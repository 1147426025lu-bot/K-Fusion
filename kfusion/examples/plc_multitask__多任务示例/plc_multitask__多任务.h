/*
 * plc_multitask__多任务.h — STRICT LET 多 job 演示（PLCFusion）
 */
#ifndef PLC_MULTITASK_H
#define PLC_MULTITASK_H

#include <pthread.h>
#include "plc_fixed__定点Q.h"
#include "plc_let__LET.h"

#define MT_MAX_PRIO     8
#define MT_TASK_COUNT   4
#define MT_LET_JOB_COUNT 6
#define MT_LOOP_MS      1
#define MT_MS_TO_NS(ms) ((uint64_t)(ms) * 1000000ULL)

#ifndef MT_RUN_LOOPS
#define MT_RUN_LOOPS    500
#endif

/* 全局状态（manifest 导出） */
extern volatile int shutdown;
extern unsigned long g_mt_tick;
extern unsigned long g_mt_task_runs;
extern unsigned long g_alarm_edges;
extern int g_alarm_active;
extern plc_fix32_t g_fused_temp;
extern plc_fix32_t g_pid_output;

extern pthread_mutex_t g_state_lock;
extern struct plc_let_runtime g_mt_let;

void mt_state_init(void);
int  mt_let_setup(void);
int  mt_let_run(void);

void task_sensor_fusion(void);
void task_pid_control(void);
void task_alarm_log(void);
void task_stats_heap(void);
void task_watchdog_poll(void);

void mt_supervisor_job(void *ctx);

#endif /* PLC_MULTITASK_H */
