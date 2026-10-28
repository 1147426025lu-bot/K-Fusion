#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr10c_boundary__边界拆解.py — PR-10C 第一步：同步边界到底吃掉多少。

背景（PR-10B v2 的结论）：部分内核化的中位时延稳定高 4–5 µs，尾部更差。
但那 4–5 µs 里有多少是**边界**（ioctl 陷入/锁/唤醒/copy），多少是**计算**？
不拆开就只能归因猜测。

做法（两个独立来源，互相校验）：

  A. 空 ioctl 地板价：生成一个**不做任何计算**的内核模块 + 同构消息的微基准，
     测一次完整往返（copy_in + 时间戳 + copy_out）。这就是边界的下界，
     再减去 0 计算，所以它本身就是「边界成本」。
  B. 从已有 mixed 运行日志里取两个量：
       stat_ioctl_roundtrip_us（用户态到用户态的往返）
       stat_kernel_compute_us （内核侧 k_start→k_end）
     两者相减 = 往返里扣掉计算后的部分（陷入 + 锁 + 唤醒 + copy）。

若 A 与 B 的边界项量级一致，说明 4–5 µs 主要不是计算，而是协议本身的代价。

诚实边界：
  · 这是**同一台机器**的实测，不是 WCET；空模块的地板价也不等于理论上界。
  · 微基准不带阶段函数，被测代码比真实部署少，但**同步路径完全相同**——
    这正是要隔离的变量。
  · 不做「内核自主周期执行」对照组（PR-10C 第二步），所以不能宣称
    「内核执行本身」有多快。

用法：
  python3 kfusion/scripts/pr10c_boundary__边界拆解.py --cycles 5000
"""
from __future__ import print_function

import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)
OUT_ROOT = os.path.join(KF, "docs", "pr10c_boundary")
CODEGEN = os.path.join(KF, "scripts", "mixed_uku_codegen__混合交接.py")
PROBE = os.path.join(KF, "include", "plc_probe__统一测量探针.h")
KDIR = os.environ.get("KDIR", "/root/linux-5.15.86")

MOD_C = r"""/* kfusion_noop_mod.c — PR-10C：只走同步路径，不做任何计算。
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
"""

BENCH_C = r"""/* bench_noop.c — 与真实驱动同构：每拍一次 ioctl，但不跑任何阶段。
 * 打印与 plc_probe 一致的 stat 行，便于与 mixed 日志并列比较。 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sched.h>
#include "kfusion_uku.h"
#include "%(probe)s"

static int fd = -1;
static struct plc_probe_series g_rt, g_resp;

static void bump_ns(struct timespec *t, long long ns)
{
	t->tv_nsec += (long)ns;
	while (t->tv_nsec >= 1000000000L) {
		t->tv_nsec -= 1000000000L;
		t->tv_sec += 1;
	}
}

static long long now_ns(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (long long)t.tv_sec * 1000000000LL + t.tv_nsec;
}

int main(int argc, char **argv)
{
	long cycles = (argc > 1) ? atol(argv[1]) : 5000;
	long k, bad = 0, miss = 0;
	struct timespec next, res;
	struct kfusion_uku_msg msg;
	const long long period = %(period_ns)dLL, deadline = %(deadline_ns)dLL;

	fd = open("/dev/kfusion_noop", O_RDWR);
	if (fd < 0) { perror("/dev/kfusion_noop"); return 2; }
	plc_probe_reset(&g_rt, "ioctl_roundtrip");
	plc_probe_reset(&g_resp, "response");
	clock_gettime(CLOCK_MONOTONIC, &next);
	for (k = 0; k < cycles; k++) {
		long long r, s, f;
		memset(&msg, 0, sizeof(msg));
		msg.magic = KFUSION_UKU_MAGIC;
		msg.seq = (uint32_t)(k + 1);
		msg.n_in = 1; msg.n_out = 1;
		bump_ns(&next, period);
		if (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) != 0)
			break;
		r = (long long)next.tv_sec * 1000000000LL + next.tv_nsec;
		s = now_ns();
		if (ioctl(fd, KFUSION_UKU_IOC, &msg) != 0) { bad++; continue; }
		f = now_ns();
		plc_probe_add(&g_rt, f - s);
		plc_probe_add(&g_resp, f - r);
		if (f - r > deadline) miss++;
	}
	(void)res;
	close(fd);
	printf("\n# mode: noop  cycles=%%ld\n", cycles);
	plc_probe_head(period, deadline, (unsigned long long)cycles);
	plc_probe_line(&g_rt);
	plc_probe_line(&g_resp);
	printf("# ioctl_error: %%ld/%%ld\n", bad, cycles);
	printf("# deadline_miss: %%ld/%%ld\n", miss, cycles);
	return 0;
}
"""


def run(cmd, cwd=None, env=None):
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=env)
    out = p.communicate()[0]
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return p.returncode, out


def load_hdr():
    spec = importlib.util.spec_from_file_location("uku_cg", CODEGEN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.HDR


def build(out, cycles):
    os.makedirs(os.path.join(out, "compat"), exist_ok=True)
    with io.open(os.path.join(out, "kfusion_uku.h"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(load_hdr())
    with io.open(os.path.join(out, "kfusion_noop_mod.c"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(MOD_C)
    with io.open(os.path.join(out, "Kbuild"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("obj-m := kfusion_noop.o\n"
                "kfusion_noop-y := kfusion_noop_mod.o\n"
                "ccflags-y := -I$(src) -I$(src)/compat "
                "-include $(src)/compat/stdint.h\n")
    with io.open(os.path.join(out, "compat", "stdint.h"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("#pragma once\n#include <linux/types.h>\n"
                "typedef __s32 int32_t;\ntypedef __u32 uint32_t;\n"
                "typedef __s64 int64_t;\n")
    with io.open(os.path.join(out, "Makefile"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("KDIR ?= %s\nall:\n\t$(MAKE) -C $(KDIR) M=$(PWD) modules\n"
                "clean:\n\t$(MAKE) -C $(KDIR) M=$(PWD) clean\n" % KDIR)

    rc, log = run(["make", "-C", out], env=dict(os.environ))
    if rc != 0:
        return {"status": "kmod_build_failed", "log_tail": log.splitlines()[-8:]}

    with io.open(os.path.join(out, "bench_noop.c"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write(BENCH_C % {"probe": PROBE, "period_ns": 1000000,
                           "deadline_ns": 1000000})
    rc, log = run(["gcc", "-O2", "-std=gnu11", "-Wall", "-I", KF + "/include",
                   "-I", out, os.path.join(out, "bench_noop.c"),
                   "-o", os.path.join(out, "bench_noop"), "-lrt"])
    if rc != 0:
        return {"status": "bench_build_failed", "log_tail": log.splitlines()[-8:]}
    return {"status": "ok", "bench": os.path.join(out, "bench_noop"),
            "ko": os.path.join(out, "kfusion_noop.ko")}


def stats(text):
    d = {}
    for m in re.finditer(r"# stat_([A-Za-z0-9_]+)_us: .*?p50=(\S+) .*?p99=(\S+) "
                         r".*?max=(\S+)", text):
        d[m.group(1)] = {"p50": float(m.group(2)), "p99": float(m.group(3)),
                         "max": float(m.group(4))}
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, nargs="+", default=[5000])
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", default=OUT_ROOT)
    a = ap.parse_args()

    out = os.path.join(a.out, "noop")
    os.makedirs(out, exist_ok=True)
    built = build(out, a.cycles[0])
    if built["status"] != "ok":
        print("%s\n%s" % (built["status"], "\n".join(built.get("log_tail") or [])),
              file=sys.stderr)
        return 2

    rc, ins = run(["insmod", built["ko"]])
    if rc != 0:
        print("insmod 失败：%s" % ins.strip()[:300], file=sys.stderr)
        return 3
    runs = []
    try:
        for cyc in a.cycles:
            for i in range(a.repeat):
                rc, txt = run([built["bench"], str(cyc)])
                with io.open(os.path.join(out, "r%d_%d.log" % (cyc, i)), "w",
                             encoding="utf-8") as f:
                    f.write(txt)
                s = stats(txt)
                runs.append({"cycles": cyc, "repeat": i, "rc": rc,
                             "roundtrip": s.get("ioctl_roundtrip"),
                             "response": s.get("response")})
    finally:
        run(["rmmod", "kfusion_noop"])

    def med(k, field):
        vals = [(r[k] or {}).get(field) for r in runs if r.get(k)]
        vals = [v for v in vals if v is not None]
        return statistics.median(vals) if vals else None

    floor_rt = med("roundtrip", "p50")
    doc = {"kind": "pr10c_boundary", "cycles": a.cycles, "repeat": a.repeat,
           "noop_ioctl": {
               "roundtrip_p50_med": floor_rt,
               "roundtrip_p99_med": med("roundtrip", "p99"),
               "roundtrip_max_med": med("roundtrip", "max"),
               "response_p50_med": med("response", "p50"),
               "runs": runs},
           "note": ("空模块：只做 copy_in/copy_out + 时间戳 + 互斥，不含任何阶段计算。"
                    "这就是同步边界的地板价。")}

    # 与已有 mixed 日志对照：往返 − 内核计算 = 边界项
    v2 = os.path.join(KF, "docs", "pr10b_matrix", "matrix_v2.json")
    if os.path.isfile(v2):
        d = json.load(io.open(v2, encoding="utf-8"))
        cmp_rows = []
        for c in d.get("cells") or []:
            if c.get("scenario") != "partial_kernel" or c.get("status") != "ok":
                continue
            for cond in c.get("conditions") or []:
                rt = (cond.get("ioctl") or {}).get("p50_med")
                if rt is None:
                    continue
                cmp_rows.append({"period_us": c["period_us"],
                                 "load": cond["load"], "mixed_roundtrip_p50": rt})
        if cmp_rows and floor_rt is not None:
            for r in cmp_rows:
                r["noop_floor_p50"] = floor_rt
                r["delta_compute_p50"] = round(r["mixed_roundtrip_p50"] - floor_rt, 3)
            doc["mixed_vs_noop"] = cmp_rows

    with io.open(os.path.join(a.out, "boundary.json"), "w",
                 encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("\n空 ioctl 地板价（p50 中位）: %.3f µs" % (floor_rt or -1), file=sys.stderr)
    for r in doc.get("mixed_vs_noop") or []:
        print("  period=%d load=%d  mixed 往返 %.3f / 空模块 %.3f → 差 %.3f µs"
              % (r["period_us"], r["load"], r["mixed_roundtrip_p50"],
                 r["noop_floor_p50"], r["delta_compute_p50"]), file=sys.stderr)
    print("结果：%s" % os.path.join(a.out, "boundary.json"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
