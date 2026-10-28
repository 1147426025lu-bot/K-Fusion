/* 反例：入口函数指针调用。必须拒绝。 */
#include <stdint.h>

int32_t g_x;

void stage_work(void)
{
	g_x = 1;
}

void loop_main(void)
{
	void (*fp)(void) = stage_work;

	fp();
}
