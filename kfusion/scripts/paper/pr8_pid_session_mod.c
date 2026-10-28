/* 会话 PID：open 清零 integ；ioctl 按序号更新；失败不改状态；close 丢弃。 */
#define pr_fmt(fmt) "kfusion_uku_pid: " fmt
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>
#include <linux/errno.h>
#include "pr8_pid_session.h"

static DEFINE_MUTEX(pid_mu);
static bool pid_busy;
static u32 pid_expect;
static s32 pid_integ;

static long pid_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	struct kfusion_uku_pid_msg msg;
	s32 next_integ, gout;
	long rc = 0;

	if (cmd != KFUSION_UKU_PID_IOC)
		return -ENOTTY;
	if (copy_from_user(&msg, (void __user *)arg, sizeof(msg)))
		return -EFAULT;
	mutex_lock(&pid_mu);
	if (!pid_busy) {
		rc = -ENODEV;
		goto out;
	}
	if (msg.magic != KFUSION_UKU_PID_MAGIC) {
		rc = -EINVAL;
		goto out;
	}
	if (msg.seq != pid_expect) {
		rc = -EILSEQ;
		goto out;
	}
	next_integ = pid_integ;
	kfusion_pid_step(&next_integ, msg.g_in, &gout);
	msg.g_out = gout;
	if (copy_to_user((void __user *)arg, &msg, sizeof(msg))) {
		rc = -EFAULT;
		goto out;
	}
	pid_integ = next_integ;
	pid_expect++;
out:
	mutex_unlock(&pid_mu);
	return rc;
}

static int pid_open(struct inode *i, struct file *f)
{
	mutex_lock(&pid_mu);
	if (pid_busy) {
		mutex_unlock(&pid_mu);
		return -EBUSY;
	}
	pid_busy = true;
	pid_expect = 1;
	pid_integ = 0;
	mutex_unlock(&pid_mu);
	return 0;
}

static int pid_release(struct inode *i, struct file *f)
{
	mutex_lock(&pid_mu);
	pid_busy = false;
	pid_expect = 0;
	pid_integ = 0;
	mutex_unlock(&pid_mu);
	return 0;
}

static const struct file_operations pid_fops = {
	.owner = THIS_MODULE,
	.open = pid_open,
	.release = pid_release,
	.unlocked_ioctl = pid_ioctl,
	.compat_ioctl = pid_ioctl,
};

static struct miscdevice pid_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "kfusion_uku_pid",
	.fops = &pid_fops,
	.mode = 0600,
};

static int __init pid_init(void)
{
	return misc_register(&pid_dev);
}

static void __exit pid_exit(void)
{
	misc_deregister(&pid_dev);
}

module_init(pid_init);
module_exit(pid_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("K-Fusion UKU PID session integrator");
