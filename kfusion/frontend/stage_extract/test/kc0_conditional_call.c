/* 反例：入口条件调用。K-C0 不推断分支，必须拒绝。 */
#include <stdint.h>

int32_t g_x;

void stage_a(void)
{
	g_x = 1;
}

void stage_b(void)
{
	g_x = 2;
}

void loop_main(void)
{
	if (g_x)
		stage_a();
	stage_b();
}
