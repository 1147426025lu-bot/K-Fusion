/*
 * plc_let__LET.h — 项目级 Logical Execution Time (LET) 运行时
 *
 * STRICT 模式：单线程按逻辑 release 时刻调度；单任务 = 1 个 job，多任务 = N 个 job。
 * 依赖 POSIX clock_gettime / clock_nanosleep（相对 delta，flags=0）；fused 下由 Pass 映射到 plc_*。
 */
#ifndef PLC_LET_H
#define PLC_LET_H

#include <stdint.h>
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PLC_LET_MAX_JOBS 16
#define PLC_LET_MODE_STRICT        1
#define PLC_LET_SKIP_MISSED        2

struct plc_let_job {
	const char *name;
	void (*fn)(void *ctx);
	void *ctx;
	uint64_t period_ns;
	uint64_t phase_ns;
	uint64_t let_ns;
	unsigned prio;
	unsigned flags;
};

struct plc_let_stats {
	uint64_t releases;
	uint64_t let_overruns;
	uint64_t deadline_misses;
	uint64_t skipped;
	int64_t  release_jitter_max_ns;
	uint64_t exec_max_ns;
};

struct plc_let_job_state {
	const struct plc_let_job *desc;
	int64_t next_release_ns;
	struct plc_let_stats stats;
};

struct plc_let_runtime {
	const struct plc_let_job *jobs;
	unsigned job_count;
	unsigned mode;
	int stopped;
	struct plc_let_job_state state[PLC_LET_MAX_JOBS];
};

int  plc_let_init(struct plc_let_runtime *rt,
		  const struct plc_let_job *jobs, unsigned job_count,
		  unsigned mode);
int  plc_let_run(struct plc_let_runtime *rt);
int  plc_let_tick(struct plc_let_runtime *rt);
void plc_let_stop(struct plc_let_runtime *rt);

void plc_let_get_stats(const struct plc_let_runtime *rt, unsigned job_id,
		       struct plc_let_stats *out);
void plc_let_aggregate_stats(const struct plc_let_runtime *rt,
			     struct plc_let_stats *out);
int  plc_let_find_job(const struct plc_let_runtime *rt, const char *name);

void plc_let_print_summary(FILE *out, const struct plc_let_runtime *rt,
			   const char *baseline, int exit_code);

#ifdef __cplusplus
}
#endif

#endif /* PLC_LET_H */
