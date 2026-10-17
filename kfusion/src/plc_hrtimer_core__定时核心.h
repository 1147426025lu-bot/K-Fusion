/*
 * plc_hrtimer_core.h — 共享 hrtimer 睡眠 / 时间 / EWMA 补偿
 *
 * 供 plc_fused_timer_host 与 plc_runner_official 共用；单 .ko 只链其一 + core。
 */
#ifndef PLC_HRTIMER_CORE_H
#define PLC_HRTIMER_CORE_H

#include <linux/ktime.h>
#include <linux/types.h>

#include "../include/plc_abi__运行时ABI.h"

#define PLC_HR_EWMA_ALPHA 16
#define PLC_HR_EWMA_SHIFT 4
#define PLC_HR_DEFAULT_IGNORE_NS 2000
#define PLC_HR_DEFAULT_MAX_COMP_NS 500LL
#define PLC_HR_COMP_WARMUP 64
#define PLC_HR_SLEEP_CHUNK_NS (100ULL * NSEC_PER_USEC)

struct plc_hr_comp_state {
	s64 ewma_ns;
	s64 comp_ns;
	u64 warmup;
};

struct plc_hr_comp_cfg {
	bool enabled;
	s32 ignore_above_ns;
	s64 max_comp_ns;
	/*
	 * 唤醒目标偏置（ns）：在"补偿居中"之上再整体平移唤醒目标点。
	 * 正值 = 唤醒目标再提前 bias_ns（jitter 整体 -bias），负值 = 推后。
	 * 用途：抖动分布通常右偏（晚唤醒尾长、早唤醒尾短），单纯 EWMA 居中
	 * 只保证均值≈0，abs_max 仍被长右尾主导。取 bias≈(max-|min|)/2 可最小化
	 * abs_max（minimax 校准）。注意：此项只平移分布，**不改变峰峰值**。
	 * 默认 0 = 旧行为。
	 */
	s64 bias_ns;
};

typedef bool (*plc_hr_stop_fn)(void);

void plc_hr_set_stop_hook(plc_hr_stop_fn fn);

void plc_hr_pin_cpu(int cpu);

int plc_hr_ktime_get_ts(int clk_id, struct plc_timespec *ts);

int plc_hr_timespec_sleep(const struct plc_timespec *req,
			  struct plc_timespec *rem);

void plc_hr_comp_reset(struct plc_hr_comp_state *st);

void plc_hr_comp_update(struct plc_hr_comp_state *st,
			const struct plc_hr_comp_cfg *cfg, s64 jitter_ns);

ktime_t plc_hr_abs_next(ktime_t next_nominal, ktime_t interval,
			struct plc_hr_comp_state *st,
			const struct plc_hr_comp_cfg *cfg, u64 comp_warmup);

#endif /* PLC_HRTIMER_CORE_H */
