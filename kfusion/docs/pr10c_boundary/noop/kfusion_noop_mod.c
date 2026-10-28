/* kfusion_noop_mod.c — PR-10C：只走同步路径，不做任何计算。
 * 目的不是功能，而是把「ioctl 陷入 + 互斥 + copy + 时间戳」单独量出来。
 * 消息结构与真实 UKU 模块完全一致（同一个 kfusion_uku.h）。 */
#define pr_fmt(fmt) "kfusion_noop: " fmt
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/ktime.h>
#include <linux/mutex.h>
#include <linux/errno.h>
#include "kfusion_uku.h"

static DEFINE_MUTEX(noop_mu);
static bool noop_busy;
static u32 noop_expect;

static long noop_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	struct kfusion_uku_msg msg;
	s64 t0, t1;
	long rc = 0;

	if (cmd != KFUSION_UKU_IOC)
		return -ENOTTY;
	if (copy_from_user(&msg, (void __user *)arg, sizeof(msg)))
		return -EFAULT;
	if (msg.magic != KFUSION_UKU_MAGIC)
		return -EINVAL;
	if (mutex_lock_interruptible(&noop_mu))
		return -ERESTARTSYS;
	if (msg.seq != noop_expect) {
		rc = -EILSEQ;
		goto out;
	}
	t0 = ktime_get_ns();
	t1 = ktime_get_ns();
	msg.k_start_ns = (int64_t)t0;
	msg.k_end_ns = (int64_t)t1;
	if (copy_to_user((void __user *)arg, &msg, sizeof(msg))) {
		rc = -EFAULT;
		goto out;
	}
	noop_expect++;
out:
	mutex_unlock(&noop_mu);
	return rc;
}

static int noop_open(struct inode *i, struct file *f)
{
	mutex_lock(&noop_mu);
	if (noop_busy) {
		mutex_unlock(&noop_mu);
		return -EBUSY;
	}
	noop_busy = true;
	noop_expect = 1;
	mutex_unlock(&noop_mu);
	return 0;
}

static int noop_release(struct inode *i, struct file *f)
{
	mutex_lock(&noop_mu);
	noop_busy = false;
	noop_expect = 0;
	mutex_unlock(&noop_mu);
	return 0;
}

static const struct file_operations noop_fops = {
	.owner = THIS_MODULE,
	.open = noop_open,
	.release = noop_release,
	.unlocked_ioctl = noop_ioctl,
	.compat_ioctl = noop_ioctl,
};

static struct miscdevice noop_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "kfusion_noop",
	.fops = &noop_fops,
	.mode = 0600,
};

static int __init noop_init(void) { return misc_register(&noop_dev); }
static void __exit noop_exit(void) { misc_deregister(&noop_dev); }
module_init(noop_init);
module_exit(noop_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("K-Fusion PR-10C sync-boundary noop module");
