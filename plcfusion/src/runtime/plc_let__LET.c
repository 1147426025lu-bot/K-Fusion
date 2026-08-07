/*
 * plc_let__LET.c — STRICT LET 调度器（单线程，逻辑 release + 相对 delta sleep）
 */
#include "plc_let__LET.h"

#include <errno.h>
#include <string.h>
#include <time.h>

extern volatile int shutdown;

#ifndef PLC_LET_MAX_JOBS
#define PLC_LET_MAX_JOBS 16
#endif

#define PLC_LET_NS_PER_SEC 1000000000LL

static int64_t plc_let_now_ns(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
		return 0;
	return (int64_t)ts.tv_sec * PLC_LET_NS_PER_SEC + ts.tv_nsec;
}

static int plc_let_sleep_until_ns(int64_t target_ns)
{
	struct timespec ts;
	int64_t now = plc_let_now_ns();
	int64_t delta = target_ns - now;

	if (delta <= 0)
		return 0;

	ts.tv_sec = (time_t)(delta / PLC_LET_NS_PER_SEC);
	ts.tv_nsec = (long)(delta % PLC_LET_NS_PER_SEC);
	for (;;) {
		if (clock_nanosleep(CLOCK_MONOTONIC, 0, &ts, NULL) == 0)
			return 0;
		if (errno != EINTR)
			return -1;
		if (shutdown)
			return -1;
		now = plc_let_now_ns();
		delta = target_ns - now;
		if (delta <= 0)
			return 0;
		ts.tv_sec = (time_t)(delta / PLC_LET_NS_PER_SEC);
		ts.tv_nsec = (long)(delta % PLC_LET_NS_PER_SEC);
	}
}

static void plc_let_stat_release_jitter(struct plc_let_stats *st, int64_t jitter_ns)
{
	int64_t abs_j = jitter_ns < 0 ? -jitter_ns : jitter_ns;

	if (abs_j > st->release_jitter_max_ns)
		st->release_jitter_max_ns = abs_j;
}

static void plc_let_stat_exec(struct plc_let_stats *st, uint64_t exec_ns,
			      uint64_t let_ns, int64_t release_ns,
			      int64_t finish_ns, uint64_t period_ns)
{
	st->releases++;
	if (exec_ns > st->exec_max_ns)
		st->exec_max_ns = exec_ns;
	if (let_ns && exec_ns > let_ns)
		st->let_overruns++;
	if (period_ns && finish_ns > release_ns + (int64_t)period_ns)
		st->deadline_misses++;
}

static void plc_let_catch_up(struct plc_let_job_state *js, int64_t now)
{
	const struct plc_let_job *job = js->desc;
	int64_t period = (int64_t)job->period_ns;
	int64_t late, missed;

	if (period <= 0 || now < js->next_release_ns + period)
		return;

	late = now - js->next_release_ns;
	missed = late / period;
	if (missed <= 0)
		return;

	if (job->flags & PLC_LET_SKIP_MISSED) {
		js->stats.skipped += (uint64_t)missed;
		js->next_release_ns += missed * period;
	} else {
		js->stats.skipped++;
		js->next_release_ns += period;
	}
}

static int plc_let_pick_ready(struct plc_let_runtime *rt, int64_t now)
{
	unsigned i;
	int best = -1;
	unsigned best_prio = 0;

	for (i = 0; i < rt->job_count; i++) {
		struct plc_let_job_state *js = &rt->state[i];

		plc_let_catch_up(js, now);
		if (now >= js->next_release_ns) {
			if (best < 0 || js->desc->prio > best_prio) {
				best = (int)i;
				best_prio = js->desc->prio;
			}
		}
	}
	return best;
}

static int64_t plc_let_next_release(struct plc_let_runtime *rt, int64_t now)
{
	unsigned i;
	int64_t next = 0;
	int have = 0;

	for (i = 0; i < rt->job_count; i++) {
		struct plc_let_job_state *js = &rt->state[i];
		int64_t candidate;

		plc_let_catch_up(js, now);
		candidate = js->next_release_ns;
		if (!have || candidate < next) {
			next = candidate;
			have = 1;
		}
	}
	return have ? next : now;
}

static void plc_let_dispatch(struct plc_let_runtime *rt, int job_id, int64_t now)
{
	struct plc_let_job_state *js = &rt->state[job_id];
	const struct plc_let_job *job = js->desc;
	int64_t release_ns = js->next_release_ns;
	int64_t t0, t1;
	uint64_t exec_ns;

	plc_let_stat_release_jitter(&js->stats, now - release_ns);

	if (job->fn)
		job->fn(job->ctx);

	t1 = plc_let_now_ns();
	t0 = now;
	exec_ns = (uint64_t)(t1 - t0);
	plc_let_stat_exec(&js->stats, exec_ns, job->let_ns, release_ns, t1,
			  job->period_ns);

	if (job->period_ns)
		js->next_release_ns = release_ns + (int64_t)job->period_ns;
}

int plc_let_init(struct plc_let_runtime *rt,
		 const struct plc_let_job *jobs, unsigned job_count,
		 unsigned mode)
{
	unsigned i;
	int64_t now;

	if (!rt || !jobs || !job_count || job_count > PLC_LET_MAX_JOBS)
		return -1;

	memset(rt, 0, sizeof(*rt));
	rt->jobs = jobs;
	rt->job_count = job_count;
	rt->mode = mode ? mode : PLC_LET_MODE_STRICT;

	now = plc_let_now_ns();
	for (i = 0; i < job_count; i++) {
		int64_t period = (int64_t)jobs[i].period_ns;
		int64_t phase = (int64_t)jobs[i].phase_ns;
		int64_t rel = phase;

		rt->state[i].desc = &jobs[i];
		if (period > 0 && now > rel) {
			int64_t elapsed = now - phase;

			rel = phase + (elapsed / period + 1) * period;
		}
		rt->state[i].next_release_ns = rel;
	}

	return 0;
}

int plc_let_tick(struct plc_let_runtime *rt)
{
	int64_t now;
	int job_id;

	if (!rt || rt->stopped)
		return -1;

	now = plc_let_now_ns();
	job_id = plc_let_pick_ready(rt, now);
	if (job_id < 0) {
		int64_t next = plc_let_next_release(rt, now);

		if (next > now && !shutdown) {
			if (plc_let_sleep_until_ns(next) != 0)
				return shutdown ? 0 : -1;
		}
		return 0;
	}

	plc_let_dispatch(rt, job_id, now);
	return 1;
}

int plc_let_run(struct plc_let_runtime *rt)
{
	if (!rt)
		return -1;

	while (!shutdown && !rt->stopped) {
		if (plc_let_tick(rt) < 0 && !shutdown)
			return -1;
	}
	return 0;
}

void plc_let_stop(struct plc_let_runtime *rt)
{
	if (rt)
		rt->stopped = 1;
}

void plc_let_get_stats(const struct plc_let_runtime *rt, unsigned job_id,
		       struct plc_let_stats *out)
{
	if (!rt || !out || job_id >= rt->job_count)
		return;
	*out = rt->state[job_id].stats;
}

void plc_let_aggregate_stats(const struct plc_let_runtime *rt,
			     struct plc_let_stats *out)
{
	unsigned i;
	struct plc_let_stats sum;

	if (!rt || !out)
		return;

	memset(&sum, 0, sizeof(sum));
	for (i = 0; i < rt->job_count; i++) {
		const struct plc_let_stats *st = &rt->state[i].stats;

		sum.releases += st->releases;
		sum.let_overruns += st->let_overruns;
		sum.deadline_misses += st->deadline_misses;
		sum.skipped += st->skipped;
		sum.exec_max_ns += st->exec_max_ns;
		if (st->release_jitter_max_ns > sum.release_jitter_max_ns)
			sum.release_jitter_max_ns = st->release_jitter_max_ns;
	}
	*out = sum;
}

int plc_let_find_job(const struct plc_let_runtime *rt, const char *name)
{
	unsigned i;

	if (!rt || !name)
		return -1;
	for (i = 0; i < rt->job_count; i++) {
		if (rt->state[i].desc->name &&
		    strcmp(rt->state[i].desc->name, name) == 0)
			return (int)i;
	}
	return -1;
}

void plc_let_print_summary(FILE *out, const struct plc_let_runtime *rt,
			   const char *baseline, int exit_code)
{
	struct plc_let_stats agg;

	if (!out || !rt)
		return;

	plc_let_aggregate_stats(rt, &agg);
	fprintf(out,
		"LetSummary: baseline=%s jobs=%u releases=%llu let_overrun=%llu "
		"deadline_miss=%llu skipped=%llu release_jitter_max_ns=%lld exit=%d\n",
		baseline ? baseline : "app",
		rt->job_count,
		(unsigned long long)agg.releases,
		(unsigned long long)agg.let_overruns,
		(unsigned long long)agg.deadline_misses,
		(unsigned long long)agg.skipped,
		(long long)agg.release_jitter_max_ns,
		exit_code);
	fflush(out);
}
