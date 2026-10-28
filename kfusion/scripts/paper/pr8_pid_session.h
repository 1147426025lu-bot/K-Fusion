/* PR-8：会话内 PID 积分协议（显式状态，不是 UKU v1 静默迁移）。
 * 内核在一次 open 会话中持有 integ；close 丢弃。只接受 int32_t 输入。
 */
#ifndef KFUSION_UKU_PID_H
#define KFUSION_UKU_PID_H
#ifdef __KERNEL__
#include <linux/ioctl.h>
#include <linux/types.h>
#else
#include <stdint.h>
#include <sys/ioctl.h>
#endif

#define KFUSION_UKU_PID_MAGIC 0x554B5532u /* UKU2 */
#define KFUSION_UKU_PID_INTEG_MAX 10000
#define KFUSION_UKU_PID_INTEG_MIN -10000

struct kfusion_uku_pid_msg {
	uint32_t magic;
	uint32_t seq;
	int32_t g_in;
	int32_t g_out;
};

#define KFUSION_UKU_PID_IOC _IOWR('K', 2, struct kfusion_uku_pid_msg)

static inline void kfusion_pid_step(int32_t *integ, int32_t gin, int32_t *gout)
{
	int32_t err = 0 - gin;
	int32_t i = *integ + err;

	if (i > KFUSION_UKU_PID_INTEG_MAX)
		i = KFUSION_UKU_PID_INTEG_MAX;
	else if (i < KFUSION_UKU_PID_INTEG_MIN)
		i = KFUSION_UKU_PID_INTEG_MIN;
	*integ = i;
	*gout = (err << 1) + (i >> 3);
}

static inline int32_t kfusion_pid_sample(uint32_t seq)
{
	return (int32_t)((seq * 13u) & 0x7ffu) - 256;
}

#endif
