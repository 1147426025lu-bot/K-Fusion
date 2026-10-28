/* chain_window__四段窗口示例.c —— chain_window.dag.json 的阶段实现（P0-2 反例用）。
 *
 * 说明：
 *  - 模块级状态一律**外部链接**（不要 static）：融合实验里若输出没人读，
 *    -O1/-O2 会合法地把整段删掉，性能实验就变成测空函数。
 *  - s3 代表执行器输出：契约要求它在周期内 [380, 520] µs 生效。
 */
#include <stdint.h>

/* kfusion:task name=chain_window entry=loop_main period_us=1000 deadline_us=1000 step_us=200
 * 相位：s1@0 s2@200 s3@400 s4@600。s3 执行器窗口 [380,520] µs，全融合会前拉。 */

int32_t cw_in = 7;   /* 外部输入（传感器读数） */
int32_t cw_a;
int32_t cw_b;
int32_t cw_c;
int32_t cw_out;      /* 执行器输出 */

void s1(void)
{
	cw_a = cw_in + 1;
}

void s2(void)
{
	cw_b = cw_a * 2;
}

void s3(void)
{
	/* 执行器：契约窗口 [380,520] µs，提前/推后都算破坏物理对齐 */
	cw_c = cw_b - 3;
}

void s4(void)
{
	cw_out = cw_c + cw_a;
}

void loop_reset(void)
{
	cw_in = 7;
	cw_a = 0;
	cw_b = 0;
	cw_c = 0;
	cw_out = 0;
}

void loop_main(void)
{
	s1();
	s2();
	s3();
	s4();
}
