/* plc_chain__三阶段示例.c — ECoS §10 最小闭环：Sensor → FIR → PID
 *
 * 这份示例**不是**完整 PLC，只保留「效果判定」关心的形态：
 *   sensor_prepare(): 读写 MMIO 寄存器        → 期望 mmio
 *   fir_filter():     读写全局状态            → 期望 state_read + state_write
 *   pid_step():       读写全局状态 + 定长循环 → 期望 state_read + state_write
 *
 * 用途：让 EffectSummaryAnalysis 从 IR 推出上述效果，再让 contract_check
 * 回答"这三个阶段能不能放进 Linux 内核域"。因为内核域 allow_mmio=false，
 * sensor_prepare 必须留在 NuttX 侧 —— 这就是 ECoS 想要的自动分域信号。
 */
#include <stdint.h>

/* 假想的传感器寄存器（BCM2711 外设窗口内的一段地址） */
#define SENSOR_BASE 0x7e200000u

static volatile uint32_t *const sensor_reg = (volatile uint32_t *)SENSOR_BASE;

/* 同上：必须外部链接，否则"没人读的输出"会被整段删掉
 * （实测 g_actuator 曾被优化掉，pid_step 的 writes 里只剩 g_pid_integ）。 */
int32_t g_sensor_raw;         /* 最近一次采样 */
int32_t g_fir_hist[8];        /* FIR 历史窗口 */
int32_t g_fir_out;            /* FIR 输出 */
int32_t g_pid_integ;          /* PID 积分项 */
int32_t g_actuator;           /* 执行器输出 */

/* --- 阶段 A：读设备（MMIO）--- */
void sensor_prepare(void)
{
	*sensor_reg = 0x1u;               /* MMIO 写：触发一次采样 */
	g_sensor_raw = (int32_t)(*sensor_reg);   /* MMIO 读 */
}

/* --- 阶段 B：纯计算 + 状态读写 --- */
void fir_filter(void)
{
	int32_t acc = 0;
	int i;

	for (i = 0; i < 8; i++)
		acc += g_fir_hist[i];
	for (i = 7; i > 0; i--)
		g_fir_hist[i] = g_fir_hist[i - 1];
	g_fir_hist[0] = g_sensor_raw;
	g_fir_out = acc >> 3;
}

/* --- 阶段 C：纯计算 + 状态读写 --- */
void pid_step(int32_t setpoint)
{
	int32_t err = setpoint - g_fir_out;

	g_pid_integ += err;
	if (g_pid_integ > 10000)
		g_pid_integ = 10000;
	else if (g_pid_integ < -10000)
		g_pid_integ = -10000;

	/* Kp=2, Ki=1/8, Kd=0 的整数近似 */
	g_actuator = (err << 1) + (g_pid_integ >> 3);
}

/* 一个周期的完整链路 */
void chain_main(int32_t setpoint)
{
	sensor_prepare();
	fir_filter();
	pid_step(setpoint);
}
