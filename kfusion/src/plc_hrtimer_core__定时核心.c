/*
 * plc_hrtimer_core.c — hrtimer 核心：分块 sleep、时间读取、EWMA 补偿
 */
#include <linux/delay.h>
#include <linux/sched.h>
#include <linux/sched/signal.h>

#include "plc_hrtimer_core__定时核心.h"

extern int shutdown;

static plc_hr_stop_fn plc_hr_stop_hook;

static bool plc_hr_default_stop(void)
{
	return READ_ONCE(shutdown) != 0;
}

static bool plc_hr_should_stop(void)
{
	if (plc_hr_stop_hook && plc_hr_stop_hook())
		return true;
	return plc_hr_default_stop();
}

void plc_hr_set_stop_hook(plc_hr_stop_fn fn)
{
	plc_hr_stop_hook = fn;
}

void plc_hr_pin_cpu(int cpu)
{
	if (cpu >= 0 && cpu < nr_cpu_ids && cpu_online(cpu))
		set_cpus_allowed_ptr(current, cpumask_of(cpu));
}

int plc_hr_ktime_get_ts(int clk_id, struct plc_timespec *ts)
{
	u64 ns = ktime_get_ns();

	(void)clk_id;
	if (!ts)
		return -EINVAL;
	ts->tv_sec = (long)div_u64(ns, NSEC_PER_SEC);
	ts->tv_nsec = (long)(ns - (u64)ts->tv_sec * NSEC_PER_SEC);
	return 0;
}

int plc_hr_timespec_sleep(const struct plc_timespec *req,
			    struct plc_timespec *rem)
{
	u64 ns, slept;

	(void)rem;
	if (!req)
		return 0;

	ns = (u64)req->tv_sec * NSEC_PER_SEC + (u64)req->tv_nsec;
	if (!ns)
		return 0;

	for (slept = 0; slept < ns;) {
		u64 step = ns - slept;
		ktime_t expire;

		if (plc_hr_should_stop() || fatal_signal_pending(current))
			return -EINTR;

		if (step > PLC_HR_SLEEP_CHUNK_NS)
			step = PLC_HR_SLEEP_CHUNK_NS;

		if (step < 50 * NSEC_PER_USEC) {
			u32 us = div_u64(step + NSEC_PER_USEC - 1, NSEC_PER_USEC);

			usleep_range(us, us + 1);
		} else {
			expire = ktime_add_ns(ktime_get(), step);
			set_current_state(TASK_INTERRUPTIBLE);
			schedule_hrtimeout(&expire, HRTIMER_MODE_ABS);
			__set_current_state(TASK_RUNNING);
		}
		slept += step;
		cond_resched();
	}
	return fatal_signal_pending(current) ? -EINTR : 0;
}

void plc_hr_comp_reset(struct plc_hr_comp_state *st)
{
	if (!st)
		return;
	st->ewma_ns = 0;
	st->comp_ns = 0;
	st->warmup = 0;
}

void plc_hr_comp_update(struct plc_hr_comp_state *st,
			const struct plc_hr_comp_cfg *cfg, s64 jitter_ns)
{
	s64 aj = jitter_ns < 0 ? -jitter_ns : jitter_ns;
	s32 ignore = cfg && cfg->ignore_above_ns > 0 ? cfg->ignore_above_ns
						     : PLC_HR_DEFAULT_IGNORE_NS;
	s64 max_comp = cfg && cfg->max_comp_ns > 0 ? cfg->max_comp_ns
						   : PLC_HR_DEFAULT_MAX_COMP_NS;
	bool enabled = !cfg || cfg->enabled;

	if (!st)
		return;
	if (aj > ignore)
		return;

	st->ewma_ns = ((PLC_HR_EWMA_ALPHA - 1) * st->ewma_ns + jitter_ns) >>
		      PLC_HR_EWMA_SHIFT;
	if (!enabled) {
		st->comp_ns = 0;
		return;
	}

	st->comp_ns = st->ewma_ns;
	if (st->comp_ns > max_comp)
		st->comp_ns = max_comp;
	else if (st->comp_ns < -max_comp)
		st->comp_ns = -max_comp;
}

ktime_t plc_hr_abs_next(ktime_t next_nominal, ktime_t interval,
			struct plc_hr_comp_state *st,
			const struct plc_hr_comp_cfg *cfg, u64 comp_warmup)
{
	ktime_t next_abs = next_nominal;
	ktime_t now = ktime_get();
	bool comp_on = !cfg || cfg->enabled;

	if (st && comp_on && comp_warmup > PLC_HR_COMP_WARMUP)
		next_abs = ktime_sub_ns(next_abs, st->comp_ns);
	if (ktime_before(next_abs, now))
		next_abs = ktime_add_ns(now, interval);
	return next_abs;
}
