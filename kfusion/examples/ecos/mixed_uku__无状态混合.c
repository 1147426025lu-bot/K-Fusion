/* mixed_uku__无状态混合.c — PR-5B 最小混合部署负载
 *
 * 用户态采样 → 内核纯计算（无持久状态）→ 用户态收回。
 * sample/sink 钉在用户态；add/shift 可进内核。
 * 正确性交接：同一 seq 下 gold（全用户态）与 mixed（ioctl）digest 必须一致。
 */
#include <stdint.h>

/* kfusion:task name=mixed_uku entry=loop_main period_us=1000 deadline_us=1000 step_us=200
 * sample@0 add@200 shift@400 sink@600 */

int32_t g_in;
int32_t g_mid;
int32_t g_out;
uint32_t g_seq;

/* kfusion:pin linux_userspace */
void stage_sample(void)
{
	g_seq += 1u;
	g_in = (int32_t)(g_seq * 17u + 3u);
}

void stage_add(void)
{
	g_mid = g_in + 1;
}

void stage_shift(void)
{
	g_out = g_mid << 1;
}

/* kfusion:pin linux_userspace */
void stage_sink(void)
{
	/* 观察点：digest 读 g_out / g_seq。无 I/O。 */
}

void loop_reset(void)
{
	g_in = 0;
	g_mid = 0;
	g_out = 0;
	g_seq = 0;
}

void loop_main(void)
{
	stage_sample();
	stage_add();
	stage_shift();
	stage_sink();
}
