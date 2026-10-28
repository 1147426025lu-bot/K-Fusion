/* 反例：入口循环调用阶段。必须拒绝。 */
#include <stdint.h>

int32_t g_x;

void stage_work(void)
{
	g_x = g_x + 1;
}

void loop_main(void)
{
	int i;

	for (i = 0; i < 2; i++)
		stage_work();
}
