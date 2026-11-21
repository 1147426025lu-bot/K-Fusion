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

/* 任务计算只有一份定义：kfusion/src/let_pc_compute__产消计算体.h。
 * 本文件（D 组输入）与 A/C 组共用它，禁止在此另写一份算术。
 * 必须放在变量声明之后：头文件里的内联包装要引用这些变量。
 * KF_PC_PLAIN：保持裸赋值，使 Pass 看到的 AST 与之前一致。 */
#define KF_PC_PLAIN 1
#include "../../src/let_pc_compute__产消计算体.h"

/* 生产者：推进状态，算出待发布命令（不直接写 g_cmd） */
void stage_prod(void)
{
	KF_PC_PROD_STMT();
}

/* 逻辑写入：到点才发布共享命令与版本 */
void stage_commit(void)
{
	KF_PC_COMMIT_STMT();
}

/* 逻辑读取：到点才快照共享命令（计算侧只许用快照） */
void stage_fetch(void)
{
	KF_PC_FETCH_STMT();
}

/* 消费者动作：仅依赖快照 */
void stage_act(void)
{
	KF_PC_ACT_STMT();
}

void pc_reset(void)
{
	KF_PC_RESET_STMT();
}

void pc_main(void)
{
	stage_prod();
	stage_commit();
	stage_fetch();
	stage_act();
}
