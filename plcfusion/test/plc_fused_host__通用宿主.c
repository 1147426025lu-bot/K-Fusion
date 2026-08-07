/*
 * plc_fused_host.c — 通用 fused 模块宿主（kthread 入口 / main 路径）
 *
 * 功能: 按 manifest 启动 FUSE_KTHREAD_ENTRY 或 FUSE_RUN_MAIN=1 时跑 main()
 * 构建: ignite_fused__通用ko构建.sh -DFUSED_ENTRY_SYMBOL=... -DFUSED_RUN_MAIN=...
 * 卸载: echo 1 > /sys/module/<name>_mod/parameters/shutdown_request && rmmod
 */
#include <linux/module.h>
#include <linux/kthread.h>
#include <linux/completion.h>
#include <linux/sched.h>
#include <linux/sched/rt.h>
#include <linux/cpumask.h>
#include <linux/moduleparam.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>
#include <linux/atomic.h>
#include <linux/string.h>

#ifndef FUSED_ENTRY_SYMBOL
#define FUSED_ENTRY_SYMBOL timerthread
#endif

#ifndef FUSED_RUN_MAIN
#define FUSED_RUN_MAIN 0
#endif

#ifndef FUSED_MAIN_ARGS_DEFAULT
#define FUSED_MAIN_ARGS_DEFAULT ""
#endif

#define FUSED_MAX_ARGS 16
#define FUSED_ARG_BUF 256

#define STR(x) #x

#define FUSED_ENTRY_CONCAT2(a, b) a##b
#define FUSED_ENTRY_CONCAT(a, b) FUSED_ENTRY_CONCAT2(a, b)
#define fused_entry_fn FUSED_ENTRY_CONCAT(, FUSED_ENTRY_SYMBOL)

#if !FUSED_RUN_MAIN
extern void *fused_entry_fn(void *arg);
#else
extern int main(int argc, char **argv);
#endif

/* FUSE_GLOBALIZE_SYMBOLS=shutdown 时由融合 .o 导出；否则 weak 默认 0 */
__attribute__((weak)) int shutdown;

__attribute__((weak)) void plc_pthread_wake_all(void) { }

static struct task_struct *fused_task;
static struct completion fused_done;
static struct dentry *fused_stats_dentry;
static int fused_cpu = -1;
static bool shutdown_request;
static atomic64_t fused_loop_count = ATOMIC64_INIT(0);

#if FUSED_RUN_MAIN
static char fused_main_args[FUSED_ARG_BUF] = FUSED_MAIN_ARGS_DEFAULT;
module_param_string(main_args, fused_main_args, FUSED_ARG_BUF, 0644);
MODULE_PARM_DESC(main_args, "Arguments for fused main() (space-separated)");

static char fused_prog_name[32] = "plc_fused";
static char fused_token_storage[FUSED_MAX_ARGS][32];
static char *fused_argv_ptrs[FUSED_MAX_ARGS];
static int fused_argc;

static void fused_prepare_main_argv(void)
{
	int i = 0;
	char *p = fused_main_args;
	char *tok;

	fused_argc = 0;
	fused_argv_ptrs[fused_argc++] = fused_prog_name;

	while (fused_argc < FUSED_MAX_ARGS && p && *p) {
		while (*p == ' ' || *p == '\t')
			p++;
		if (!*p)
			break;
		tok = p;
		while (*p && *p != ' ' && *p != '\t')
			p++;
		if (*p)
			*p++ = '\0';
		strscpy(fused_token_storage[i], tok, sizeof(fused_token_storage[i]));
		fused_argv_ptrs[fused_argc++] = fused_token_storage[i];
		i++;
	}
}
#endif

void plc_fused_stats_tick(void)
{
	atomic64_inc(&fused_loop_count);
}
EXPORT_SYMBOL_GPL(plc_fused_stats_tick);
module_param(fused_cpu, int, 0644);
MODULE_PARM_DESC(fused_cpu, "CPU to pin fused worker (-1=any)");

static void fused_request_stop(void)
{
	WRITE_ONCE(shutdown, 1);
	plc_pthread_wake_all();
	if (fused_task)
		wake_up_process(fused_task);
}

static int shutdown_request_set(const char *val, const struct kernel_param *kp)
{
	bool req = false;

	(void)kp;
	if (kstrtobool(val, &req))
		return -EINVAL;
	if (req)
		fused_request_stop();
	return 0;
}

static const struct kernel_param_ops shutdown_request_ops = {
	.set = shutdown_request_set,
	.get = param_get_bool,
};

module_param_cb(shutdown_request, &shutdown_request_ops, &shutdown_request, 0644);
MODULE_PARM_DESC(shutdown_request, "Write 1 to stop fused worker and allow rmmod");

static int fused_stats_show(struct seq_file *m, void *v)
{
	(void)v;
	seq_printf(m, "shutdown=%d\n", READ_ONCE(shutdown));
	seq_printf(m, "loops=%lld\n", (long long)atomic64_read(&fused_loop_count));
	seq_printf(m, "run_main=%d\n", FUSED_RUN_MAIN);
	seq_printf(m, "entry=%s\n", FUSED_RUN_MAIN ? "main" : STR(FUSED_ENTRY_SYMBOL));
	return 0;
}

static int fused_stats_open(struct inode *inode, struct file *file)
{
	return single_open(file, fused_stats_show, NULL);
}

static const struct file_operations fused_stats_fops = {
	.owner = THIS_MODULE,
	.open = fused_stats_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int fused_worker(void *arg)
{
	(void)arg;

	if (fused_cpu >= 0 && fused_cpu < nr_cpu_ids && cpu_online(fused_cpu))
		set_cpus_allowed_ptr(current, cpumask_of(fused_cpu));

#if FUSED_RUN_MAIN
	fused_prepare_main_argv();
	printk("PLCFusion: running fused main() argc=%d\n", fused_argc);
	main(fused_argc, fused_argv_ptrs);
#else
	printk("PLCFusion: running fused entry %s()\n", STR(FUSED_ENTRY_SYMBOL));
	fused_entry_fn(NULL);
#endif

	printk("PLCFusion: fused worker done (shutdown=%d)\n", READ_ONCE(shutdown));
	kthread_complete_and_exit(&fused_done, 0);
}

static int __init fused_host_init(void)
{
	init_completion(&fused_done);
	fused_stats_dentry = debugfs_create_file("fused_stats", 0444, NULL, NULL,
						 &fused_stats_fops);
	fused_task = kthread_create(fused_worker, NULL, "plc_fused_worker");
	if (IS_ERR(fused_task))
		return PTR_ERR(fused_task);
	wake_up_process(fused_task);
#if FUSED_RUN_MAIN
	printk("PLCFusion: loaded generic host (run_main=1, entry=main)\n");
#else
	printk("PLCFusion: loaded generic host (run_main=0, entry=%s)\n",
	       STR(FUSED_ENTRY_SYMBOL));
#endif
	return 0;
}

static void __exit fused_host_exit(void)
{
	fused_request_stop();
	debugfs_remove(fused_stats_dentry);
	fused_stats_dentry = NULL;

	if (fused_task) {
		wait_for_completion_timeout(&fused_done, msecs_to_jiffies(15000));
		fused_task = NULL;
	}
	printk("PLCFusion: unloaded generic host\n");
}

module_init(fused_host_init);
module_exit(fused_host_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("PLCFusion generic fused host");
