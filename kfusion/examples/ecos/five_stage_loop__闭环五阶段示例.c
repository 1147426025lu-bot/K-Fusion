/* five_stage_loop__闭环五阶段示例.c — 用于「跨周期依赖」实验的闭环周期任务
 *
 * 与 four_stage_chain 的唯一区别：**把环闭上**。
 * 四阶段链的输出 `g_out` 无人读，图是一条 DAG，规划器永远见不到环；
 * 而真实控制回路一定是环——执行器写出去，下一拍传感器才读回来。
 * 这一份就是那个最小闭环：
 *
 *   sample(0µs) → fir(200) → pid(400) → clamp(600) → plant(800) ─┐
 *      ↑                                                          │
 *      └────────────────── 下一拍（z⁻¹，跨周期）──────────────────┘
 *
 * 为什么这个环**合法**：plant 在 800 µs 写 `g_plant`，sample 在下一拍 0 µs 读它。
 * 生产者相位（800）晚于消费者相位（0），这条边在一拍之内不可能满足，
 * 只能跨拍——延迟由调度强制，不是代码里写的状态变量。
 * 对照：如果两者顺序反过来（sample 800、plant 0），环就要求在**同一拍内**首尾相接，
 * 那是代数环，没有合法执行顺序，必须拒绝（见 fusion_plan 的 algebraic_loops）。
 *
 * 关于融合的影响：把五个阶段并成一个周期体后，plant 从 800 µs 被前拉到 ~0 µs，
 * 环闭合边的间隔从 200 µs 变成约一整拍。**采样序列逐位不变**（读到的还是
 * 上一拍的同一个值），但回路的死区时间变了——这是融合对反馈环唯一的实质影响。
 */
#include <stdint.h>

/* 外部链接：static 会让整条链在 -O2 下被当死代码删掉（see four_stage_chain） */
int32_t g_plant;              /* 被控量（plant 的输出） */
int32_t g_dist;               /* 扰动 */
int32_t g_raw;                /* 采样值 = 被控量 + 扰动 */
int32_t g_hist[8];            /* FIR 窗口 */
int32_t g_fir_out;            /* FIR 输出 */
int32_t g_integ;              /* PID 积分 */
int32_t g_act;                /* 控制量 */
int32_t g_out;                /* 限幅后的执行器输出 */
uint32_t g_seed = 12345u;

/* 阶段 1：采样。读上一拍的被控量（跨周期依赖就在这里） */
void stage_sample(void)
{
	g_seed = g_seed * 1103515245u + 12345u;
	g_dist = (int32_t)((g_seed >> 16) & 0x3ffu) - 512;
	g_raw = g_plant + g_dist;
}

/* 阶段 2：8 点滑动平均 + 窗口右移 */
void stage_fir(void)
{
	int32_t acc = 0;
	int i;

	for (i = 0; i < 8; i++)
		acc += g_hist[i];
	for (i = 7; i > 0; i--)
		g_hist[i] = g_hist[i - 1];
	g_hist[0] = g_raw;
	g_fir_out = acc >> 3;
}

/* 阶段 3：PI 控制（Kp=2, Ki=1/8），带积分限幅 */
void stage_pid(int32_t setpoint)
{
	int32_t err = setpoint - g_fir_out;

	g_integ += err;
	if (g_integ > 100000)
		g_integ = 100000;
	else if (g_integ < -100000)
		g_integ = -100000;

	g_act = (err << 1) + (g_integ >> 3);
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

/* 阶段 5：一阶对象（y ← (3y + u)/4）。环就闭在这里 */
void stage_plant(void)
{
	g_plant = (g_plant * 3 + g_out) >> 2;
}

/* 一拍的完整链路（供效果分析取调用关系用；融合实验跑的是五个阶段本体） */
void loop_main(int32_t setpoint)
{
	stage_sample();
	stage_fir();
	stage_pid(setpoint);
	stage_clamp();
	stage_plant();
}
