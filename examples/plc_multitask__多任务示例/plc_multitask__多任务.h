/*
 * plc_multitask__多任务.h — 多优先级协作调度 + pthread 监视器（PLCFusion 演示）
 *
 * 调度：定长任务表 + 优先级位图（非红黑树），主循环 1ms tick。
 * 浮点：任务内 double/float 由 FUSE_FIXED_POINT=1 Pass 转 Q 定点。
 */
#ifndef PLC_MULTITASK_H
#define PLC_MULTITASK_H

#include <pthread.h>
#include "plc_fixed__定点Q.h"

#define MT_MAX_PRIO     8
#define MT_TASK_COUNT   4
#define MT_LOOP_MS      1
#define MT_RUN_LOOPS    500

typedef void (*mt_task_fn)(void);

struct mt_task {
	const char *name;
	mt_task_fn fn;
	unsigned prio;
	unsigned period_ms;
	unsigned phase_ms;
	unsigned ready;
	unsigned last_tick;
	unsigned long run_count;
};

/* 全局状态（manifest FUSE_GLOBALIZE_SYMBOLS 导出，宿主可读） */
extern volatile int shutdown;
extern unsigned long g_mt_tick;
extern unsigned long g_mt_task_runs;
extern int g_alarm_active;
extern plc_fix32_t g_fused_temp;
extern plc_fix32_t g_pid_output;

extern pthread_mutex_t g_state_lock;

void mt_scheduler_init(void);
void mt_scheduler_tick(unsigned tick_ms);

void task_sensor_fusion(void);
void task_pid_control(void);
void task_alarm_log(void);
void task_stats_heap(void);

void *watchdog_thread(void *arg);

#endif /* PLC_MULTITASK_H */
