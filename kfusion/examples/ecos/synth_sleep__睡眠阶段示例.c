/* synth_sleep__睡眠阶段示例.c —— synth_sleep.dag.json 的阶段实现（PR9-C 负向夹具用）。
 *
 * io_read 会在周期体里睡眠：这在 RTOS/用户态可以是合法设计，
 * 但在 ring-0 周期体里会直接错过相位，所以后端必须拒绝它进内核。
 */
#include <stdint.h>
#include <unistd.h>

int32_t ss_io;
int32_t ss_out;

void io_read(void)
{
	usleep(50);       /* 阻塞式读取：不能进 ring-0 周期体 */
	ss_io = 42;
}

void compute(void)
{
	ss_out = ss_io * 2;
}
