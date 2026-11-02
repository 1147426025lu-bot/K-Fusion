/* four_stage_chain__四阶段示例.c — 用于「融合边界」实验的四阶段周期任务
 *
 * 与 plc_chain 的区别：**没有 MMIO、没有 sleep、没有外部调用**。
 * 为什么需要这一份：plc_chain 的 sensor_prepare 碰 MMIO，在 linux_kernel 域
 * （allow_mmio=false）会被门禁正确拒绝，于是无法在 Linux 域做"同域融合"实验。
 * 融合实验要回答的是「一个周期体拆成几次释放，各付一次唤醒代价」，
 * 与是否碰 MMIO 无关，所以这里用一个纯计算链把变量控制住。
 *
 * 四个阶段的依赖是一条链（靠全局状态传递，与 plc_chain 同构）：
 *   sample（采样）→ fir（滤波）→ pid（控制）→ clamp（限幅输出）
 * 每阶段约 0.4–0.6 µs 的整数运算，量级与实测的 BM2 周期体相当。
 */
#include <stdint.h>

/* kfusion:task name=four_stage_chain entry=chain_main period_us=1000 deadline_us=1000 step_us=200
 * sample@0 fir@200 pid@400 clamp@600 */

/* 注意：这里**不能**用 static。
 * 若状态全为内部链接且最终输出无人读取，-O1/-O2 会把整条尾链当死代码删掉
 * （实测 stage_clamp 被优化成 `ret void`，@g_act / @g_out 直接消失），
 * 于是"性能实验"变成测空函数。改为外部链接后，存储被视为可观察，链会保留。 */
int32_t g_raw;                /* 最新采样 */
int32_t g_hist[8];            /* FIR 窗口 */
int32_t g_fir_out;            /* FIR 输出 */
int32_t g_integ;              /* PID 积分 */
int32_t g_act;                /* 控制量 */
int32_t g_out;                /* 限幅后的执行器输出 */
uint32_t g_seed = 12345u;

/* 阶段 1：采样（确定性伪随机，替代真实 ADC，避免引入 MMIO） */
void stage_sample(void)
{
	g_seed = g_seed * 1103515245u + 12345u;
	g_raw = (int32_t)((g_seed >> 16) & 0x7fffu);
}

/* 阶段 2：8 点滑动平均 + 窗口右移。
 * 手写展开，避免 Clang 在 -O2 插入 llvm.vector.reduce.add.*（会被
 * EffectSummary 标成 unknown external，契约默认拒内核/用户态）。 */
void stage_fir(void)
{
	int32_t acc = g_hist[0] + g_hist[1] + g_hist[2] + g_hist[3]
		+ g_hist[4] + g_hist[5] + g_hist[6] + g_hist[7];

	g_hist[7] = g_hist[6];
	g_hist[6] = g_hist[5];
	g_hist[5] = g_hist[4];
	g_hist[4] = g_hist[3];
	g_hist[3] = g_hist[2];
	g_hist[2] = g_hist[1];
	g_hist[1] = g_hist[0];
	g_hist[0] = g_raw;
	g_fir_out = acc >> 3;
}

/* 阶段 3：PI 控制（Kp=2, Ki=1/8），带积分限幅。
 * 有符号左移改为 64 位乘法，避免 C 标准对负值左移的 UB。 */
void stage_pid(int32_t setpoint)
{
	int32_t err = setpoint - g_fir_out;
	int64_t acc;

	g_integ += err;
	if (g_integ > 100000)
		g_integ = 100000;
	else if (g_integ < -100000)
		g_integ = -100000;

	acc = (int64_t)err * 2 + (int64_t)(g_integ / 8);
	if (acc > 2147483647LL)
		acc = 2147483647LL;
	else if (acc < -2147483648LL)
		acc = -2147483648LL;
	g_act = (int32_t)acc;
}

/* 阶段 4：输出限幅 */
void stage_clamp(void)
{
	int32_t v = g_act;

	if (v > 60000)
		v = 60000;
	else if (v < -60000)
		v = -60000;
	g_out = v;
}

/* 一拍的完整链路（供效果分析取调用关系用；融合实验跑的是四个阶段本体） */
void chain_main(int32_t setpoint)
{
	stage_sample();
	stage_fir();
	stage_pid(setpoint);
	stage_clamp();
}
