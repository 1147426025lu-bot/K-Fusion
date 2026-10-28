/* kfusion_uku_mod.c — ioctl 触发的内核计算。无 hrtimer。
 * 单客户端独占；序号必须连续；校验失败不跑阶段、不改状态。 */
#define pr_fmt(fmt) "kfusion_uku: " fmt
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/ktime.h>
#include <linux/mutex.h>
#include <linux/errno.h>
#include "kfusion_uku.h"

extern void loop_reset(void);
extern int32_t g_in;
extern int32_t g_out;
extern int32_t g_integ;
extern void stage_pid(void);

static DEFINE_MUTEX(uku_mu);
static bool uku_busy;
static u32 uku_expect;

static long uku_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	struct kfusion_uku_msg msg;
	s64 t0, t1;
	long rc = 0;

	if (cmd == KFUSION_UKU_STATE_IOC) {
		struct kfusion_uku_state st;
		if (!uku_busy)
			return -EBUSY;
		st.magic = KFUSION_UKU_MAGIC;
		st.value = g_integ;
		if (copy_to_user((void __user *)arg, &st, sizeof(st)))
			return -EFAULT;
		return 0;
	}
	if (cmd != KFUSION_UKU_IOC)
		return -ENOTTY;
	if (copy_from_user(&msg, (void __user *)arg, sizeof(msg)))
		return -EFAULT;
	mutex_lock(&uku_mu);
	if (!uku_busy) {
		rc = -ENODEV;
		goto out;
	}
	if (msg.magic != KFUSION_UKU_MAGIC ||
	    msg.n_in != 1 || msg.n_out != 1) {
		rc = -EINVAL;
		goto out;
	}
	if (msg.seq != uku_expect) {
		rc = -EILSEQ;
		goto out;
	}
	t0 = ktime_get_ns();
	g_in = msg.in[0];
	stage_pid();
	msg.out[0] = g_out;
	t1 = ktime_get_ns();
	msg.k_start_ns = (int64_t)t0;
	msg.k_end_ns = (int64_t)t1;
	if (copy_to_user((void __user *)arg, &msg, sizeof(msg))) {
		rc = -EFAULT;
		goto out;
	}
	uku_expect++;
out:
	mutex_unlock(&uku_mu);
	return rc;
}

static int uku_open(struct inode *i, struct file *f)
{
	mutex_lock(&uku_mu);
	if (uku_busy) {
		mutex_unlock(&uku_mu);
		return -EBUSY;
	}
	loop_reset();
	uku_busy = true;
	uku_expect = 1;
	mutex_unlock(&uku_mu);
	return 0;
}

static int uku_release(struct inode *i, struct file *f)
{
	mutex_lock(&uku_mu);
	loop_reset();
	uku_busy = false;
	uku_expect = 0;
	mutex_unlock(&uku_mu);
	return 0;
}

static const struct file_operations uku_fops = {
	.owner = THIS_MODULE,
	.open = uku_open,
	.release = uku_release,
	.unlocked_ioctl = uku_ioctl,
	.compat_ioctl = uku_ioctl,
};

static struct miscdevice uku_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "kfusion_uku",
	.fops = &uku_fops,
	.mode = 0600,
};

static int __init uku_init(void)
{
	return misc_register(&uku_dev);
}

static void __exit uku_exit(void)
{
	misc_deregister(&uku_dev);
}

module_init(uku_init);
module_exit(uku_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("K-Fusion UKU ioctl compute");
