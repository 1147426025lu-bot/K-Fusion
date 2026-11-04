/* let_pc_shared__LET产消共享.c — LET 任务间共享读写最小示例
 *
 * 研究口径（开题方案第 1 步）：
 *   · 生产者：stage_prod 计算待发布命令，stage_commit 在逻辑写入时刻发布；
 *   · 消费者：stage_fetch 在逻辑读取时刻快照共享命令，stage_act 使用快照；
 *   · 跨周期状态：g_state 仅由生产者持有并跨拍累加。
 *
 * LET 钉扎（见 contracts/examples/let_pc_shared.time.json）：
 *   stage_commit @400µs  output_commit  → 发布 g_cmd / g_cmd_ver
 *   stage_fetch  @500µs  input_snapshot → 快照到 g_cmd_snap / g_cmd_snap_ver
 *
 * 语义要求：消费者不得直接读 g_cmd 做计算；必须经过逻辑读取快照，
 * 以便对照「线程路径读写」与「中断路径读写」时，差异只落在读写生效位置。
 */
#include <stdint.h>

/* kfusion:task name=let_pc_shared entry=pc_main period_us=1000 deadline_us=1000 offset_us=0,400,500,700 */

int32_t g_state;                 /* 生产者跨周期状态 */
int32_t g_cmd_pending;           /* 待发布命令（未到逻辑写入时刻不可见） */
int32_t g_cmd;                   /* 共享命令（逻辑写入后才更新） */
uint32_t g_cmd_ver;              /* 共享命令版本 */
int32_t g_cmd_snap;              /* 消费者输入快照 */
uint32_t g_cmd_snap_ver;         /* 快照版本 */
int32_t g_out;                   /* 消费者输出（可观察） */
uint32_t g_seed = 1u;

/* 生产者：推进状态，算出待发布命令（不直接写 g_cmd） */
void stage_prod(void)
{
	g_seed = g_seed * 1103515245u + 12345u;
	g_state = g_state + (int32_t)((g_seed >> 16) & 0x7u) - 3;
	if (g_state > 1000)
		g_state = 1000;
	else if (g_state < -1000)
		g_state = -1000;
	g_cmd_pending = g_state * 2;
}

/* 逻辑写入：到点才发布共享命令与版本 */
void stage_commit(void)
{
	g_cmd = g_cmd_pending;
	g_cmd_ver = g_cmd_ver + 1u;
}

/* 逻辑读取：到点才快照共享命令（计算侧只许用快照） */
void stage_fetch(void)
{
	g_cmd_snap = g_cmd;
	g_cmd_snap_ver = g_cmd_ver;
}

/* 消费者动作：仅依赖快照 */
void stage_act(void)
{
	int32_t v = g_cmd_snap + 1;

	if (v > 2000)
		v = 2000;
	else if (v < -2000)
		v = -2000;
	g_out = v;
}

void pc_reset(void)
{
	g_state = 0;
	g_cmd_pending = 0;
	g_cmd = 0;
	g_cmd_ver = 0;
	g_cmd_snap = 0;
	g_cmd_snap_ver = 0;
	g_out = 0;
	g_seed = 1u;
}

void pc_main(void)
{
	stage_prod();
	stage_commit();
	stage_fetch();
	stage_act();
}
