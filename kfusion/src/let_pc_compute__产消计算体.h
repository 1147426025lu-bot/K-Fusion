/*
 * let_pc_compute__产消计算体.h — 四组共用的唯一任务计算定义
 *
 * 规范 = 本文件。kfusion/examples/ecos/let_pc_shared__LET产消共享.c 也包含它，
 * 因此 D 组（Pass 生成）与 A/C 组跑的是同一份算术，不存在第二份副本可以漂移。
 *
 *   阶段          相位      做什么
 *   ------------  --------  --------------------------------------------------
 *   kf_pc_prod    0us       推进 g_seed / g_state，算出 g_cmd_pending（不碰 g_cmd）
 *   kf_pc_commit  400us     逻辑写入：g_cmd = g_cmd_pending，g_cmd_ver++
 *   kf_pc_fetch   500us     逻辑读取：快照到 g_cmd_snap / g_cmd_snap_ver
 *   kf_pc_act     700us     消费者计算：只许读快照，产出 g_out
 *
 * 被三方包含，保证四组跑的是同一份算术、同一套缓冲区协议、同一组变量宽度：
 *   · kf_let_userspace__用户态LET读写.c   A 组（计算与读写桩都在用户态线程）
 *   · kf_hand_let__手写硬中断读写.c        C 组（计算在 worker，读写桩在手写硬中断）
 *   · let_pc_shared__LET产消共享.c         D 组（Pass 生成，读写桩钉扎到 hrtimer）
 *
 * 本文件只允许出现：算术、对共享变量的读改。禁止系统调用、锁、内存分配、
 * 日志、时间读取——一旦引入，四组就不再是同一份计算。
 *
 * 为什么是"语句宏 + 内联包装"两层：
 *   · KF_PC_*_STMT() 是裸语句，供 let_pc_shared.c 的 stage_* 直接展开。
 *     D 组的 Pass 必须在预处理后的 AST 上看到平铺算术，若 stage_prod 里只剩
 *     一个 static inline 调用，融合/inline 的判定对象就变了。
 *   · kf_pc_*() 是给 A/C 用的函数外壳，避免宏在循环里反复展开。
 *
 * 访问原语按各自内存模型取（内核 READ_ONCE/WRITE_ONCE，用户态 __atomic_relaxed）。
 * 表达式本身逐字相同，差别只在原语——这是语言层必需，不是被测变量。
 *
 * 变量声明由包含者提供，类型必须逐字节一致（包含者负责，见文件末尾的宽度守卫）：
 *   int32_t  g_state, g_cmd_pending, g_cmd, g_cmd_snap, g_out;
 *   uint32_t g_cmd_ver, g_cmd_snap_ver;
 *   uint32_t g_seed;            // 初值 1u
 *   int32_t  g_ready;           // 生产者就绪（由读改它的宿主维护）
 */
#ifndef KF_LET_PC_COMPUTE_H
#define KF_LET_PC_COMPUTE_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#endif

/* 三种访问原语，按宿主内存模型选：
 *   KF_PC_PLAIN  规范源 let_pc_shared.c 用——必须保持"裸赋值"，否则 D 组
 *                Pass 看到的 AST 会从普通 load/store 变成 atomic builtin，
 *                融合判定与生成代码都会跟着变，那不是本轮要动的变量。
 *   __KERNEL__   内核模块用 READ_ONCE/WRITE_ONCE。
 *   其他         用户态用 __atomic relaxed。 */
#if defined(KF_PC_PLAIN)
#define KF_PC_R(v)     (v)
#define KF_PC_W(v, x)  ((v) = (x))
#elif defined(__KERNEL__)
#define KF_PC_R(v)     READ_ONCE(v)
#define KF_PC_W(v, x)  WRITE_ONCE((v), (x))
#else
#define KF_PC_R(v)     __atomic_load_n(&(v), __ATOMIC_RELAXED)
#define KF_PC_W(v, x)  __atomic_store_n(&(v), (x), __ATOMIC_RELAXED)
#endif

/* ---- 生产者计算：推进跨周期状态，算出待发布命令 ----
 * 顺序敏感：先更新 g_seed 再用它的第 16..18 位。反过来（先用后乘）会得到
 * 完全不同的序列，而序列不同就意味着四组跑的不是同一个任务。 */
#define KF_PC_PROD_STMT()                                                  \
	do {                                                               \
		int32_t kf_s_;                                             \
		KF_PC_W(g_seed, KF_PC_R(g_seed) * 1103515245u + 12345u);   \
		kf_s_ = KF_PC_R(g_state)                                   \
		      + (int32_t)((KF_PC_R(g_seed) >> 16) & 0x7u) - 3;     \
		if (kf_s_ > 1000)                                          \
			kf_s_ = 1000;                                      \
		else if (kf_s_ < -1000)                                    \
			kf_s_ = -1000;                                     \
		KF_PC_W(g_state, kf_s_);                                   \
		KF_PC_W(g_cmd_pending, kf_s_ * 2);                         \
	} while (0)

/* ---- 逻辑写入的本体 ----
 * 不含"是否允许提交"的门禁。门禁口径本身就是 D 组要修的对象，不能被藏进
 * 共用计算里，否则四个宿主会共享同一个错误。 */
#define KF_PC_COMMIT_STMT()                                        \
	do {                                                       \
		KF_PC_W(g_cmd, KF_PC_R(g_cmd_pending));            \
		KF_PC_W(g_cmd_ver, KF_PC_R(g_cmd_ver) + 1u);       \
	} while (0)

/* ---- 逻辑读取的本体 ---- */
#define KF_PC_FETCH_STMT()                                         \
	do {                                                       \
		KF_PC_W(g_cmd_snap, KF_PC_R(g_cmd));               \
		KF_PC_W(g_cmd_snap_ver, KF_PC_R(g_cmd_ver));       \
	} while (0)

/* ---- 消费者计算：只依赖快照，禁止直接读 g_cmd ---- */
#define KF_PC_ACT_STMT()                                           \
	do {                                                       \
		int32_t kf_v_ = KF_PC_R(g_cmd_snap) + 1;           \
		if (kf_v_ > 2000)                                  \
			kf_v_ = 2000;                              \
		else if (kf_v_ < -2000)                            \
			kf_v_ = -2000;                             \
		KF_PC_W(g_out, kf_v_);                             \
	} while (0)

#define KF_PC_RESET_STMT()                                         \
	do {                                                       \
		KF_PC_W(g_state, 0);                               \
		KF_PC_W(g_cmd_pending, 0);                         \
		KF_PC_W(g_cmd, 0);                                 \
		KF_PC_W(g_cmd_ver, 0u);                            \
		KF_PC_W(g_cmd_snap, 0);                            \
		KF_PC_W(g_cmd_snap_ver, 0u);                       \
		KF_PC_W(g_out, 0);                                 \
		KF_PC_W(g_seed, 1u);                               \
	} while (0)

static inline void kf_pc_prod(void)   { KF_PC_PROD_STMT(); }
static inline void kf_pc_commit(void) { KF_PC_COMMIT_STMT(); }
static inline void kf_pc_fetch(void)  { KF_PC_FETCH_STMT(); }
static inline void kf_pc_act(void)    { KF_PC_ACT_STMT(); }
static inline void kf_pc_reset(void)  { KF_PC_RESET_STMT(); }

/* 编译期宽度守卫：A 组曾用 volatile long（ARM64 上 8 字节），与 D 组的
 * int32_t（4 字节）访问宽度不同，会悄悄改变 store 粒度。这里把它钉死。 */
#ifdef __KERNEL__
typedef char kf_pc_size_guard_t[(sizeof(int32_t) == 4) ? 1 : -1];
#elif defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(int32_t) == 4, "int32_t 必须是 4 字节");
#endif

#endif /* KF_LET_PC_COMPUTE_H */
