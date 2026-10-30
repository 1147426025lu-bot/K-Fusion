/* mixed_pid_stateful__积分混合.c — PR-6 W2：有跨拍积分的 UKU 形态
 *
 * 形状是 U|K|U，但 pid 读写 g_integ（self_carry）。UKU v1 没有状态版本，
 * 不得把积分器静默放进 ioctl 邮箱当无状态计算。
 */
#include <stdint.h>

/* kfusion:task name=mixed_pid entry=loop_main period_us=1000 deadline_us=1000 step_us=200
 * sample@0 pid@200 sink@400 */

int32_t g_in;
int32_t g_integ;
int32_t g_out;
uint32_t g_seq;

/* kfusion:pin linux_userspace */
void stage_sample(void)
{
	g_seq += 1u;
	g_in = (int32_t)((g_seq * 13u) & 0x7ffu) - 256;
}

/* 算术右移 3 位的可移植写法。
 * 原代码直接写 `g_integ >> 3`：对负数是**实现定义**行为（GCC 上恰好是算术移位），
 * 换个编译器或换优化档就可能与参考模型不一致，而 digest 相同并不能证明语义相同。
 * 这里显式实现 floor(v/8)，与 GCC 的算术移位取值完全一致，因此采样序列不变。 */
static int32_t asr3(int32_t v)
{
	return (v >= 0) ? (v >> 3) : ~((~v) >> 3);
}

void stage_pid(void)
{
	int32_t err = 0 - g_in;
	int64_t acc;

	g_integ += err;
	if (g_integ > 10000)
		g_integ = 10000;
	else if (g_integ < -10000)
		g_integ = -10000;

	/* 原写法 `(err << 1)` 在 err < 0 时是有符号左移——C 标准未定义。
	 * 不能简单换成 `err * 2`：那只是把 UB 换成"可能溢出"。这里在 64 位里算，
	 * 并显式钳位，使"不溢出"由代码保证而不是靠注释里的推理：
	 *   g_in ∈ [-256, 1791] ⇒ err ∈ [-1791, 256] ⇒ 2·err ∈ [-3582, 512]
	 *   asr3(g_integ) ∈ [-1250, 1250]（g_integ 已钳在 ±10000）
	 *   故 |acc| < 5000，int32 装得下；钳位是给将来改输入范围留的保险。 */
	acc = (int64_t)err * 2 + (int64_t)asr3(g_integ);
	if (acc > 2147483647LL)
		acc = 2147483647LL;
	else if (acc < -2147483648LL)
		acc = -2147483648LL;
	g_out = (int32_t)acc;
}

/* kfusion:pin linux_userspace */
void stage_sink(void)
{
}

void loop_reset(void)
{
	g_in = 0;
	g_integ = 0;
	g_out = 0;
	g_seq = 0;
}

void loop_main(void)
{
	stage_sample();
	stage_pid();
	stage_sink();
}
