/* 反例：入口重复调用同一阶段。按函数名去重会丢掉第二次，必须拒绝。 */
#include <stdint.h>

int32_t g_x;

void stage_work(void)
{
	g_x = g_x + 1;
}

void loop_main(void)
{
	stage_work();
	stage_work();
}
