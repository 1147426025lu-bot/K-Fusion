/* 反例：周期体里 malloc —— Gate 1 必须 fail-closed，不得生成 .ko */
#include <stdint.h>
#include <stdlib.h>

int32_t g_x;

void stage_work(void)
{
	int32_t *p = (int32_t *)malloc(sizeof(int32_t));
	if (p) {
		g_x = *p;
		free(p);
	}
}

void loop_main(void)
{
	stage_work();
}
