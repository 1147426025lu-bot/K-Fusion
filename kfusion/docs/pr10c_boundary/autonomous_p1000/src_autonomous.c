/* mixed_pid_stateful__积分混合.c — PR-6 W2：有跨拍积分的 UKU 形态
 *
 * 形状是 U|K|U，但 pid 读写 g_integ（self_carry）。UKU v1 没有状态版本，
 * 不得把积分器静默放进 ioctl 邮箱当无状态计算。
 */
#include <stdint.h>

/* kfusion:task name=mixed_pid entry=loop_main period_us=1000 deadline_us=1000 step_us=200
 * sample@0 pid@200 sink@400 */

int32_t g_in;
int32_t g_integ;
int32_t g_out;
uint32_t g_seq;
void stage_sample(void)
{
	g_seq += 1u;
	g_in = (int32_t)((g_seq * 13u) & 0x7ffu) - 256;
}

void stage_pid(void)
{
	int32_t err = 0 - g_in;

	g_integ += err;
	if (g_integ > 10000)
		g_integ = 10000;
	else if (g_integ < -10000)
		g_integ = -10000;
	g_out = (err << 1) + (g_integ >> 3);
}
void stage_sink(void)
{
}

void loop_reset(void)
{
	g_in = 0;
	g_integ = 0;
	g_out = 0;
	g_seq = 0;
}

void loop_main(void)
{
	stage_sample();
	stage_pid();
	stage_sink();
}
