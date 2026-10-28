/* 反例：入口直接写全局。状态更新必须在阶段函数内。 */
#include <stdint.h>

int32_t g_x;

void stage_work(void)
{
	g_x = g_x + 1;
}

void loop_main(void)
{
	g_x = 0;
	stage_work();
}
