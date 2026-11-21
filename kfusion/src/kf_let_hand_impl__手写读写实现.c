// SPDX-License-Identifier: GPL-2.0
/* kf_let_hand_impl__手写读写实现.c —— C 组（手写）的 LET 读写实现
 *
 * 用途：C/D 单变量对照。两者共用同一份宿主（事件表、hrtimer 回调、计时点、
 * 环形缓冲、就绪门禁），**只有本文件被替换**：
 *   D 组 = let_impl_gen.c（由契约的 publish / snap_vars 生成）
 *   C 组 = 本文件（人写）
 *
 * 约束（改这里必须同步）：
 *   · 语义必须与 kfusion/contracts/examples/let_pc_shared.time.json 一致——
 *     commit@400 发布 g_cmd←g_cmd_pending 并把 g_cmd_ver +1；
 *     fetch@500 把 g_cmd/g_cmd_ver 快照到 g_cmd_snap/g_cmd_snap_ver。
 *   · 门禁口径不许在这里改：kf_let_gate_take() / kf_let_gate_fail() 由宿主提供，
 *     skip / stale / consumed 的计数也在宿主里。
 *   · 本文件只覆盖「一拍一次 commit + 一次 fetch」的示例形状；多事件形状由生成版负责。
 *
 * 与生成版的写法差异（这才是被比较的东西）：
 *   · 生成版逐条展开 WRITE_ONCE(dst, READ_ONCE(src))，每条各自取一次快照；
 *   · 手写版先取到局部变量、算出新版本号，再一次性写入两个共享量。
 * 语义等价，指令序列不同，正好用来量「生成代码是否保持了手写实现的语义与开销」。
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/compiler.h>

#include "kf_let_host.h"

/* 共享量与阶段实现同源（stage_impl*.c/.S）；这里只声明，不定义。
 * 类型必须与宿主 ecos_fused_linux.c 里的 extern 声明完全一致（全是 int32_t），
 * 否则 C/D 的 diff 里会出现"无意义的类型差"，给单变量对照添噪声。
 */
extern int32_t g_cmd;
extern int32_t g_cmd_ver;
extern int32_t g_cmd_pending;
extern int32_t g_cmd_snap;
extern int32_t g_cmd_snap_ver;

int let_run(u64 cycle_id, u32 id, u32 op, u32 *ver_out)
{
	s64 age_ns = 0;
	int st;

	(void)id;

	switch (op) {
	case KF_LET_OP_WRITE:
		/* 逻辑写入：必须拿到本拍生产者（g0）的结果，否则按 skip 收尾 */
		st = kf_let_gate_take(cycle_id, KF_PROD_GID, &age_ns);
		if (st != KF_STAGE_OK) {
			if (ver_out)
				*ver_out = READ_ONCE(g_cmd_ver);
			return kf_let_gate_fail(st, KF_LET_POLICY_SKIP);
		}
		{
			int32_t payload = READ_ONCE(g_cmd_pending);
			uint32_t next_ver = READ_ONCE(g_cmd_ver) + 1u;

			WRITE_ONCE(g_cmd, payload);
			WRITE_ONCE(g_cmd_ver, next_ver);
			if (ver_out)
				*ver_out = next_ver;
		}
		return KF_LET_ST_OK;

	case KF_LET_OP_READ:
		/* 逻辑读取：值与其版本必须来自同一时刻，先读值再读版本会跨拍 */
		{
			int32_t snap = READ_ONCE(g_cmd);
			uint32_t snap_ver = READ_ONCE(g_cmd_ver);

			WRITE_ONCE(g_cmd_snap, snap);
			WRITE_ONCE(g_cmd_snap_ver, snap_ver);
			if (ver_out)
				*ver_out = snap_ver;
		}
		return KF_LET_ST_OK;

	default:
		if (ver_out)
			*ver_out = 0;
		return KF_LET_ST_ERR;
	}
}
