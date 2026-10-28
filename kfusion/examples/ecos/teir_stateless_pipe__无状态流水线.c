/* teir_stateless_pipe — Gate 2 对照：无跨拍状态的算术流水线
 *
 * 三阶段只靠本拍数据边相连，没有积分、没有 z⁻¹。
 * TEIR 应给出 same-job 边、零 persistent_state、零 previous-job。
 */
#include <stdint.h>

/* kfusion:task name=stateless_pipe entry=loop_main period_us=1000 deadline_us=1000 step_us=200 */

int32_t g_in;
int32_t g_mid;
int32_t g_out;

void stage_add(void)
{
	g_mid = g_in + 1;
}

void stage_shift(void)
{
	g_out = g_mid << 1;
}

void loop_main(void)
{
	stage_add();
	stage_shift();
}
