/*
 * plc_multitask_tasks__任务集.c — Q 定点传感融合、PID、日志、堆分配统计
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "plc_multitask__多任务.h"
#include "plc_fixed__定点Q.h"

unsigned long g_mt_task_runs;
unsigned long g_alarm_edges;
int g_alarm_active;
plc_fix32_t g_fused_temp;
plc_fix32_t g_pid_output;

void mt_state_init(void)
{
	g_fused_temp = PLC_FIX32(48.0);
}

static plc_fix32_t g_sensors[4] = {
	PLC_FIX32(48.5), PLC_FIX32(49.2), PLC_FIX32(50.1), PLC_FIX32(49.8),
};
static plc_fix32_t g_weights[4] = {
	PLC_FIX32(0.25), PLC_FIX32(0.25), PLC_FIX32(0.30), PLC_FIX32(0.20),
};
static plc_fix32_t g_pid_integral;
static plc_fix32_t g_pid_prev_err;
static const plc_fix32_t Kp = PLC_FIX32(1.20);
static const plc_fix32_t Ki = PLC_FIX32(0.05);
static const plc_fix32_t Kd = PLC_FIX32(0.01);
static const plc_fix32_t g_setpoint = PLC_FIX32(50.0);
static const plc_fix32_t g_alarm_hi = PLC_FIX32(55.0);
static const plc_fix32_t g_alarm_lo = PLC_FIX32(42.0);

static void fix32_print_parts(plc_fix32_t v, int *whole, int *frac2)
{
	int64_t frac;

	if (whole)
		*whole = plc_fix32_to_int(v);
	frac = (int64_t)(v & 0xFFFFFFFF) * 100;
	if (frac2)
		*frac2 = (int)(frac >> 32);
}

static plc_fix32_t fix32_weighted_sum(void)
{
	plc_fix32_t fused = 0;
	int i;

	for (i = 0; i < 4; i++) {
		plc_fix32_t term =
			(plc_fix32_t)plc_fix_mul_i64(g_sensors[i], g_weights[i], 32);

		fused = fused + term;
	}
	return fused;
}

void task_sensor_fusion(void)
{
	plc_fix32_t fused, drift, scaled;
	int idx;

	fused = fix32_weighted_sum();
	idx = (int)(g_mt_tick % 4);
	drift = (plc_fix32_t)plc_fix_mul_i64(PLC_FIX32(0.02),
					     PLC_FIX32((double)((int)(g_mt_tick % 5) - 2)),
					     32);
	g_sensors[idx] = g_sensors[idx] + drift;
	scaled = (plc_fix32_t)plc_fix_mul_i64(drift, PLC_FIX32(0.1), 32);

	pthread_mutex_lock(&g_state_lock);
	g_fused_temp = fused + scaled;
	pthread_mutex_unlock(&g_state_lock);
}

void task_pid_control(void)
{
	plc_fix32_t measured, err, deriv, p_term, i_term, d_term, out;

	pthread_mutex_lock(&g_state_lock);
	measured = g_fused_temp;
	pthread_mutex_unlock(&g_state_lock);

	err = g_setpoint - measured;
	g_pid_integral = g_pid_integral +
		(plc_fix32_t)plc_fix_mul_i64(err, Ki, 32);
	deriv = err - g_pid_prev_err;
	g_pid_prev_err = err;

	p_term = (plc_fix32_t)plc_fix_mul_i64(err, Kp, 32);
	i_term = g_pid_integral;
	d_term = (plc_fix32_t)plc_fix_mul_i64(deriv, Kd, 32);
	out = p_term + i_term + d_term;

	if (plc_fix32_cmp(out, PLC_FIX32(100.0)) > 0)
		out = PLC_FIX32(100.0);
	else if (plc_fix32_cmp(out, PLC_FIX32(0.0)) < 0)
		out = PLC_FIX32(0.0);

	pthread_mutex_lock(&g_state_lock);
	g_pid_output = out;
	g_alarm_active =
		(plc_fix32_cmp(measured, g_alarm_hi) > 0 ||
		 plc_fix32_cmp(measured, g_alarm_lo) < 0) ? 1 : 0;
	pthread_mutex_unlock(&g_state_lock);
}

void task_alarm_log(void)
{
	int whole = 0, frac2 = 0;
	plc_fix32_t temp, out;

	pthread_mutex_lock(&g_state_lock);
	temp = g_fused_temp;
	out = g_pid_output;
	pthread_mutex_unlock(&g_state_lock);

	fix32_print_parts(temp, &whole, &frac2);
	printf("mt: tick=%lu temp=%d.%02d pid=%d alarm=%d\n",
	       g_mt_tick, whole, frac2, plc_fix32_to_int(out), g_alarm_active);
}

void task_stats_heap(void)
{
	char *buf;
	unsigned long n;

	buf = (char *)malloc(128);
	if (!buf)
		return;
	n = g_mt_task_runs;
	snprintf(buf, 128, "heap_stats runs=%lu tick=%lu", n, g_mt_tick);
	if ((n % 7) == 0)
		printf("mt: %s\n", buf);
	free(buf);
}

void task_watchdog_poll(void)
{
	static int last_alarm;
	int alarm_now;

	pthread_mutex_lock(&g_state_lock);
	alarm_now = g_alarm_active;
	pthread_mutex_unlock(&g_state_lock);

	if (alarm_now && !last_alarm) {
		g_alarm_edges++;
		printf("mt: watchdog ALARM edge at tick=%lu\n", g_mt_tick);
	}
	last_alarm = alarm_now;
}
