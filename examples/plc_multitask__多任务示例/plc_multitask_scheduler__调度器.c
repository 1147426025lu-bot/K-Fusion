/*
 * plc_multitask_scheduler__调度器.c — 定长查表 + 优先级就绪位图
 */
#include "plc_multitask__多任务.h"

static struct mt_task mt_table[MT_TASK_COUNT];
static unsigned mt_task_count;
static unsigned mt_ready_bitmap;

static void mt_task_register(const char *name, mt_task_fn fn, unsigned prio,
			     unsigned period_ms, unsigned phase_ms)
{
	struct mt_task *t;

	if (mt_task_count >= MT_TASK_COUNT)
		return;
	t = &mt_table[mt_task_count++];
	t->name = name;
	t->fn = fn;
	t->prio = prio;
	t->period_ms = period_ms;
	t->phase_ms = phase_ms;
	t->ready = 0;
	t->last_tick = phase_ms;
	t->run_count = 0;
}

void mt_scheduler_init(void)
{
	unsigned i;

	mt_task_count = 0;
	mt_ready_bitmap = 0;
	for (i = 0; i < MT_TASK_COUNT; i++)
		mt_table[i].fn = NULL;

	mt_task_register("sensor_fusion", task_sensor_fusion, 6, 10, 0);
	mt_task_register("pid_control", task_pid_control, 5, 20, 5);
	mt_task_register("alarm_log", task_alarm_log, 3, 100, 0);
	mt_task_register("stats_heap", task_stats_heap, 1, 500, 0);
}

void mt_scheduler_tick(unsigned tick_ms)
{
	int i, best = -1, best_prio = -1;

	for (i = 0; i < MT_TASK_COUNT; i++) {
		struct mt_task *t = &mt_table[i];

		if (!t->fn)
			continue;
		if (tick_ms >= t->last_tick + t->period_ms) {
			t->ready = 1;
			if (t->prio < MT_MAX_PRIO)
				mt_ready_bitmap |= (1u << t->prio);
		}
	}

	for (i = 0; i < MT_TASK_COUNT; i++) {
		if (!mt_table[i].fn || !mt_table[i].ready)
			continue;
		if ((int)mt_table[i].prio > best_prio) {
			best_prio = (int)mt_table[i].prio;
			best = i;
		}
	}

	if (best < 0)
		return;

	mt_table[best].fn();
	mt_table[best].ready = 0;
	mt_table[best].last_tick = tick_ms;
	mt_table[best].run_count++;
	g_mt_task_runs++;
	mt_ready_bitmap &= ~(1u << mt_table[best].prio);
}
