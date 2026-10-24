/* chain_window__四段窗口示例.c —— chain_window.dag.json 的阶段实现（P0-2 反例用）。
 *
 * 说明：
 *  - 模块级状态一律**外部链接**（不要 static）：融合实验里若输出没人读，
 *    -O1/-O2 会合法地把整段删掉，性能实验就变成测空函数。
 *  - s3 代表执行器输出：契约要求它在周期内 [380, 520] µs 生效。
 */
#include <stdint.h>

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
