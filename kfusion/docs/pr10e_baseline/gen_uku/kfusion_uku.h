/* kfusion_uku.h — 用户态/内核共用 ioctl 邮箱。勿手改。 */
#ifndef KFUSION_UKU_H
#define KFUSION_UKU_H
#ifdef __KERNEL__
#include <linux/ioctl.h>
#include <linux/types.h>
#else
#include <stdint.h>
#include <sys/ioctl.h>
#endif
#define KFUSION_UKU_MAGIC 0x554B5531u
#define KFUSION_UKU_MAX 8
struct kfusion_uku_msg {
	uint32_t magic;
	uint32_t seq;
	int32_t n_in;
	int32_t n_out;
	int32_t in[KFUSION_UKU_MAX];
	int32_t out[KFUSION_UKU_MAX];
	int64_t k_start_ns;
	int64_t k_end_ns;
};
#define KFUSION_UKU_IOC _IOWR('K', 1, struct kfusion_uku_msg)
/* PR-10A：读回内核独占的会话状态，用于验证「状态真的在内核里延续」，
 * 而不是只看用户态 digest（digest 不含 session，看不到内核那份）。 */
struct kfusion_uku_state {
	uint32_t magic;
	int32_t value;
};
#define KFUSION_UKU_STATE_IOC _IOWR('K', 2, struct kfusion_uku_state)
#endif
