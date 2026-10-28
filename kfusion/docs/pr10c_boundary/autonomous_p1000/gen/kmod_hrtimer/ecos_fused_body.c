/* ecos_fused_body.c —— 由 ECoS 融合方案生成，两个后端共用；不要手改。
 *
 * 组内顺序 = 方案顺序 = DAG 拓扑序（已由 PlanVerifier 校验）。
 * 每个 ecos_body_gN() 对应方案里的一次释放：释放 N 次/周期，而不是阶段数次。
 */
#include "ecos_fused_body.h"

/* 阶段执行计时钩子（弱默认空实现）。
 * Linux 宿主提供真实 ktime_get() 计时覆盖此弱符号；NuttX / LET 后端不提供
 * 强定义，自动用空实现。用弱符号而非宏，保证三个后端共用同一份周期体
 * （逐字节相同）——计时开销只在 Linux 侧产生。 */
extern void ecos_stage_enter(unsigned int idx);
extern void ecos_stage_exit(unsigned int idx);
__attribute__((weak)) void ecos_stage_enter(unsigned int idx) { (void)idx; }
__attribute__((weak)) void ecos_stage_exit(unsigned int idx) { (void)idx; }

void stage_sample(void);
void stage_pid(void);
void stage_sink(void);

/* 组 g0：释放相位 0 µs，执行 ≈0.00 µs（3 阶段） */
void ecos_body_g0(void)
{
	ecos_stage_enter(0);
	stage_sample();	/* stage_sample，相位 0 µs */
	ecos_stage_exit(0);
	ecos_stage_enter(1);
	stage_pid();	/* stage_pid，相位 200 µs */
	ecos_stage_exit(1);
	ecos_stage_enter(2);
	stage_sink();	/* stage_sink，相位 400 µs */
	ecos_stage_exit(2);
}
