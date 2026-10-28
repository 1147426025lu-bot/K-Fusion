/* ecos_fused_body.h —— 由 ECoS 融合方案生成，两个后端共用；不要手改。
 *
 * 方案：1 组 / 3 阶段，周期 1000 µs
 * 生成依据：plan.verification.ok=true，执行时间证据级别 empirical
 *
 * 这里只有"一拍要做的计算"，没有调度原语。
 */
#ifndef ECOS_FUSED_BODY_H
#define ECOS_FUSED_BODY_H

/* 阶段原型里会出现 int32_t 这类定宽类型。
 * NuttX/用户态用标准 <stdint.h>；ring-0 侧由生成的 compat/stdint.h 提供
 * （只 typedef，不含 libc），Kbuild 里以 -I$(src)/compat 引入。 */
#include <stdint.h>

void ecos_body_g0(void);
#endif /* ECOS_FUSED_BODY_H */
