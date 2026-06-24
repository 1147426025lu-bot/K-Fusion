/*
 * plc_pthread_host.c — pthread → kthread 映射宿主
 *
 * 功能: 实现 plc_pthread_create/join/kill、plc_sigwait；供 FUSE_HOST=pthread
 * 链接: ignite_fused__通用ko构建.sh 在 FUSE_LINK_PTHREAD_HOST=1 时编入 .ko
 */
#include <linux/module.h>
#include <linux/kthread.h>
#include <linux/completion.h>
#include <linux/wait.h>
#include <linux/spinlock.h>
#include <linux/slab.h>
#include <linux/sched.h>
#include <linux/sched/signal.h>
#include <linux/cpumask.h>

#include "../include/plc_abi__运行时ABI.h"

#define PLC_PTHREAD_MAX 32

struct plc_pthread_slot {
	struct task_struct *task;
	void *(*start_fn)(void *);
	void *arg;
	struct completion done;
	wait_queue_head_t sig_wait;
	atomic_t sig_pending;
	int last_sig;
	unsigned long handle;
	bool used;
};

static struct plc_pthread_slot plc_slots[PLC_PTHREAD_MAX];
static DEFINE_SPINLOCK(plc_slots_lock);
static atomic64_t plc_handle_seq = ATOMIC64_INIT(1);

static struct plc_pthread_slot *plc_slot_by_handle(unsigned long handle)
{
	int i;

	for (i = 0; i < PLC_PTHREAD_MAX; i++) {
		if (plc_slots[i].used && plc_slots[i].handle == handle)
			return &plc_slots[i];
	}
	return NULL;
}

static struct plc_pthread_slot *plc_slot_by_task(struct task_struct *t)
{
	int i;

	for (i = 0; i < PLC_PTHREAD_MAX; i++) {
		if (plc_slots[i].used && plc_slots[i].task == t)
			return &plc_slots[i];
	}
	return NULL;
}

static struct plc_pthread_slot *plc_slot_alloc(void)
{
	int i;

	for (i = 0; i < PLC_PTHREAD_MAX; i++) {
		if (!plc_slots[i].used) {
			memset(&plc_slots[i], 0, sizeof(plc_slots[i]));
			init_completion(&plc_slots[i].done);
			init_waitqueue_head(&plc_slots[i].sig_wait);
			atomic_set(&plc_slots[i].sig_pending, 0);
			plc_slots[i].last_sig = 0;
			plc_slots[i].handle = (unsigned long)atomic64_inc_return(&plc_handle_seq);
			plc_slots[i].used = true;
			return &plc_slots[i];
		}
	}
	return NULL;
}

static int plc_pthread_worker(void *data)
{
	struct plc_pthread_slot *slot = data;

	if (slot->start_fn)
		slot->start_fn(slot->arg);
	complete(&slot->done);
	/* 正常 return 即可；join 侧勿再 kthread_stop */
	return 0;
}

int plc_pthread_create(unsigned long *thread, void *attr,
		       void *(*start_routine)(void *), void *arg)
{
	struct plc_pthread_slot *slot;
	struct task_struct *task;

	(void)attr;
	if (!thread || !start_routine)
		return -EINVAL;

	spin_lock(&plc_slots_lock);
	slot = plc_slot_alloc();
	spin_unlock(&plc_slots_lock);
	if (!slot)
		return -EAGAIN;

	slot->start_fn = start_routine;
	slot->arg = arg;

	task = kthread_create(plc_pthread_worker, slot, "plc_pthread");
	if (IS_ERR(task)) {
		slot->used = false;
		return PTR_ERR(task);
	}
	slot->task = task;
	*thread = slot->handle;
	wake_up_process(task);
	return 0;
}

int plc_pthread_join(unsigned long thread, void **retval)
{
	struct plc_pthread_slot *slot;
	long ret;

	(void)retval;
	slot = plc_slot_by_handle(thread);
	if (!slot)
		return -ESRCH;

	ret = wait_for_completion_interruptible(&slot->done);
	slot->task = NULL;
	slot->used = false;
	return ret;
}

int plc_pthread_kill(unsigned long thread, int sig)
{
	struct plc_pthread_slot *slot;

	slot = plc_slot_by_handle(thread);
	if (!slot)
		return -ESRCH;

	slot->last_sig = sig;
	atomic_inc(&slot->sig_pending);
	plc_signal_deliver(sig);
	wake_up_interruptible(&slot->sig_wait);
	if (slot->task)
		wake_up_process(slot->task);
	return 0;
}

void plc_pthread_wake_all(void)
{
	int i;

	spin_lock(&plc_slots_lock);
	for (i = 0; i < PLC_PTHREAD_MAX; i++) {
		if (!plc_slots[i].used)
			continue;
		atomic_inc(&plc_slots[i].sig_pending);
		wake_up_interruptible(&plc_slots[i].sig_wait);
		if (plc_slots[i].task)
			wake_up_process(plc_slots[i].task);
	}
	spin_unlock(&plc_slots_lock);
}

int plc_sigwait(const unsigned long *set, int *sig)
{
	struct plc_pthread_slot *slot;

	(void)set;
	slot = plc_slot_by_task(current);
	if (!slot) {
		schedule();
		if (sig)
			*sig = 0;
		return 0;
	}

	for (;;) {
		if (atomic_read(&slot->sig_pending) > 0) {
			atomic_dec(&slot->sig_pending);
			if (sig)
				*sig = slot->last_sig ? slot->last_sig : 1;
			return 0;
		}
		if (wait_event_interruptible(slot->sig_wait,
					     atomic_read(&slot->sig_pending) > 0))
			return -ERESTARTSYS;
	}
}

int plc_pthread_setaffinity_np(unsigned long thread, size_t cpusetsize,
			       const unsigned long *cpuset)
{
	struct plc_pthread_slot *slot;
	struct cpumask cpumask;
	unsigned long i;

	(void)cpusetsize;
	if (!cpuset)
		return -EINVAL;
	cpumask_clear(&cpumask);
	for (i = 0; i < 8 * sizeof(unsigned long) && i < nr_cpu_ids; i++) {
		if (cpuset[0] & (1UL << i))
			cpumask_set_cpu(i, &cpumask);
	}
	if (cpumask_empty(&cpumask))
		return -EINVAL;

	slot = plc_slot_by_handle(thread);
	if (!slot || !slot->task)
		return set_cpus_allowed_ptr(current, &cpumask);
	return set_cpus_allowed_ptr(slot->task, &cpumask);
}

