#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UKU 混合部署代码生成：用户态驱动 + ioctl 内核计算模块。

不是 IVSHMEM / NuttX。同一 Linux：用户态采样 → copy_from_user → 内核阶段 →
copy_to_user → 用户态收回。gold 模式全在用户态跑，digest 必须与 mixed 一致。
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys

COMPAT = """#ifndef _ECOS_COMPAT_STDINT_H
#define _ECOS_COMPAT_STDINT_H
#include <linux/types.h>
typedef __s8 int8_t; typedef __u8 uint8_t;
typedef __s16 int16_t; typedef __u16 uint16_t;
typedef __s32 int32_t; typedef __u32 uint32_t;
typedef __s64 int64_t; typedef __u64 uint64_t;
#endif
"""

HDR = """/* kfusion_uku.h — 用户态/内核共用 ioctl 邮箱。勿手改。 */
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
"""


def loadj(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def dump(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def region(name):
    if not name:
        return None
    s = name[1:] if str(name).startswith("@") else str(name)
    if s.startswith("g_") or s[0:1].isalpha():
        return s
    return None


def io_vars(plan, teir, calls=None):
    """只接受 UKUInterfaceCheck 通过的接口；失败则抛错，不得截断。"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ecos"))
    import uku_interface as uku
    chk = uku.check(plan, teir, calls=calls)
    if not chk["ok"]:
        msgs = "; ".join("%s: %s" % (e.get("code"), e.get("msg"))
                         for e in chk.get("errors") or [])
        raise SystemExit("UKU 接口门禁失败: %s" % msgs)
    iface = chk["interface"]
    return iface["kernel_stages"], iface["copy_in"], iface["copy_out"], iface


def kernel_calls(kmem, calls):
    out = []
    for m in kmem:
        out.append(calls.get(m) or ("%s()" % m))
    return out


def emit_mod(kmem, reads, writes, calls, stage_src_name, session_vars=None):
    # PR-10A：会话状态读回口。digest 明确不含 session（gold 侧没有内核那份），
    # 所以「状态是否真的在内核里延续」只能靠这个专用 ioctl 验证。
    _sess = [v for v in (session_vars or []) if v]
    if _sess:
        state_ioctl = (
            "\tif (cmd == KFUSION_UKU_STATE_IOC) {\n"
            "\t\tstruct kfusion_uku_state st;\n"
            "\t\tif (!uku_busy)\n"
            "\t\t\treturn -EBUSY;\n"
            "\t\tst.magic = KFUSION_UKU_MAGIC;\n"
            "\t\tst.value = %s;\n"
            "\t\tif (copy_to_user((void __user *)arg, &st, sizeof(st)))\n"
            "\t\t\treturn -EFAULT;\n"
            "\t\treturn 0;\n"
            "\t}\n" % _sess[0])
    else:
        state_ioctl = ""
    aset = []
    for i, v in enumerate(reads):
        aset.append("\t%s = msg.in[%d];" % (v, i))
    oset = []
    for i, v in enumerate(writes):
        oset.append("\tmsg.out[%d] = %s;" % (i, v))
    run = "\n".join("\t%s;" % c for c in kernel_calls(kmem, calls))
    ext = ["extern void loop_reset(void);"]
    seen = []
    for v in list(reads) + list(writes) + list(session_vars or []):
        if v and v not in seen:
            seen.append(v)
            ext.append("extern int32_t %s;" % v)
    for m in kmem:
        fn = (calls.get(m) or ("%s()" % m)).split("(")[0].strip()
        ext.append("extern void %s(void);" % fn)
    return """/* kfusion_uku_mod.c — ioctl 触发的内核计算。无 hrtimer。
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

%(externs)s

static DEFINE_MUTEX(uku_mu);
static bool uku_busy;
static u32 uku_expect;

static long uku_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	struct kfusion_uku_msg msg;
	s64 t0, t1;
	long rc = 0;

%(state_ioctl)s	if (cmd != KFUSION_UKU_IOC)
		return -ENOTTY;
	if (copy_from_user(&msg, (void __user *)arg, sizeof(msg)))
		return -EFAULT;
	mutex_lock(&uku_mu);
	if (!uku_busy) {
		rc = -ENODEV;
		goto out;
	}
	if (msg.magic != KFUSION_UKU_MAGIC ||
	    msg.n_in != %(nin)d || msg.n_out != %(nout)d) {
		rc = -EINVAL;
		goto out;
	}
	if (msg.seq != uku_expect) {
		rc = -EILSEQ;
		goto out;
	}
	t0 = ktime_get_ns();
%(aset)s
%(run)s
%(oset)s
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
""" % {
        "nin": len(reads), "nout": len(writes),
        "aset": "\n".join(aset), "oset": "\n".join(oset),
        "run": run,
        "externs": "\n".join(ext),
        "state_ioctl": state_ioctl,
    }


def emit_driver(plan, kmem, reads, writes, calls, stage_src, probe, period_us,
                cycles, digest_vars):
    groups = plan.get("groups") or []
    sess = set()
    meta = plan.get("meta") or {}
    if (meta.get("uku_session") or {}).get("region"):
        sess.add(meta["uku_session"]["region"])
    digest_vars = [v for v in (digest_vars or []) if v not in sess]
    # PR-10A：会话状态名（读回口用）。digest 故意不含它，所以单独打一行。
    sess_name = sorted(sess)[0] if sess else None
    lines = []
    A = lines.append
    A("/* UKU 混合驱动：gold=全用户态；mixed=中间组走 /dev/kfusion_uku */")
    A("#define _GNU_SOURCE")
    A("#include <errno.h>")
    A("#include <fcntl.h>")
    A("#include <stdint.h>")
    A("#include <stdio.h>")
    A("#include <stdlib.h>")
    A("#include <string.h>")
    A("#include <time.h>")
    A("#include <unistd.h>")
    A("#include <sys/ioctl.h>")
    A('#include "%s"' % probe)
    A('#include "kfusion_uku.h"')
    A('#include "%s"' % stage_src)
    A("")
    A("#define PERIOD_NS %dLL" % int(period_us * 1000))
    A("#define DEADLINE_NS PERIOD_NS")
    A("static uint32_t g_digest;")
    A("#define DIGEST_MIX(v) do { uint32_t _x=(uint32_t)(v); "
      "g_digest^=_x; g_digest*=16777619u; } while (0)")
    A("")
    A("static long long now_ns(void)")
    A("{")
    A("	struct timespec ts;")
    A("	clock_gettime(CLOCK_MONOTONIC, &ts);")
    A("	return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;")
    A("}")
    A("")
    A("/* PR-10E：本线程真正在 CPU 上的时间（不含被抢占/等 CPU）。")
    A(" * 与 now_ns 的差 = 这一段里「不在 CPU 上」的时间。")
    A(" * 不需要 ftrace：p1 启动分区只有 32MB，装不下第二个内核（见 PR10E_B 文档）。 */")
    A("static long long cpu_ns(void)")
    A("{")
    A("	struct timespec ts;")
    A("	clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);")
    A("	return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;")
    A("}")
    A("static void bump_ns(struct timespec *t, long long ns)")
    A("{")
    A("	t->tv_nsec += ns;")
    A("	while (t->tv_nsec >= 1000000000L) { t->tv_sec++; t->tv_nsec -= 1000000000L; }")
    A("}")
    A("")
    A("static int uku_fd = -1;")
    A("static int sess_val = 0, sess_ok = 0;")
    A("/* PR-10E：csv 模式逐拍输出原始样本（k,start_lat_ns,exec_ns,resp_ns），")
    A(" * 供统计基线用。默认关闭，避免污染既有聚合日志格式。 */")
    A("static int g_csv = 0;")
    A("static int do_kernel_group(uint32_t seq, long long *h_ns)")
    A("{")
    A("	struct kfusion_uku_msg msg;")
    A("	memset(&msg, 0, sizeof(msg));")
    A("	msg.magic = KFUSION_UKU_MAGIC;")
    A("	msg.seq = seq;")
    A("	msg.n_in = %d;" % len(reads))
    A("	msg.n_out = %d;" % len(writes))
    for i, v in enumerate(reads):
        A("	msg.in[%d] = %s;" % (i, v))
    A("	if (ioctl(uku_fd, KFUSION_UKU_IOC, &msg) != 0)")
    A("		return errno ? -errno : -1;")
    A("	if (msg.seq != seq) {")
    A("		errno = EILSEQ;")
    A("		return -EILSEQ;")
    A("	}")
    for i, v in enumerate(writes):
        A("	%s = msg.out[%d];" % (v, i))
    A("	if (h_ns)")
    A("		*h_ns = msg.k_end_ns - msg.k_start_ns;")
    A("	return 0;")
    A("}")
    A("")
    order = []
    for g in groups:
        order.extend(g.get("members") or [])
    nst = len(order) or 1
    A("static int run_mode(const char *mode, long cycles)")
    A("{")
    A("	int mixed = (strcmp(mode, \"mixed\") == 0);")
    A("	long k, miss = 0, bad_seq = 0, bad_ioctl = 0;")
    A("	struct timespec next;")
    A("	struct plc_probe_series g_start, g_work, g_resp, g_hand, g_rt;")
    A("	struct plc_probe_series g_cpu, g_preempt;")
    A("	struct plc_probe_series g_exec_stage[%d];" % nst)
    A("	loop_reset();")
    A("	g_digest = 2166136261u;")
    A("	if (mixed) {")
    A("		uku_fd = open(\"/dev/kfusion_uku\", O_RDWR);")
    A("		if (uku_fd < 0) { perror(\"/dev/kfusion_uku\"); return 2; }")
    A("	}")
    A("	plc_probe_reset(&g_start, \"start_lat\");")
    A("	plc_probe_reset(&g_work, \"exec\");")
    A("	plc_probe_reset(&g_resp, \"response\");")
    A("	plc_probe_reset(&g_cpu, \"cpu_in_work\");")
    A("	plc_probe_reset(&g_preempt, \"preempt_in_work\");")
    A("	plc_probe_reset(&g_hand, \"kernel_compute\");")
    A("	plc_probe_reset(&g_rt, \"ioctl_roundtrip\");")
    for i, name in enumerate(order):
        A("	plc_probe_reset(&g_exec_stage[%d], \"exec_stage_%s\");" % (i, name))
    A("	if (clock_gettime(CLOCK_MONOTONIC, &next) != 0) return 1;")
    A("	for (k = 0; k < cycles; k++) {")
    A("		long long r, s, f, hns = 0, _a, c0, c1;")
    A("		int rc;")
    A("		bump_ns(&next, PERIOD_NS);")
    A("		r = (long long)next.tv_sec * 1000000000LL + next.tv_nsec;")
    A("		if (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) != 0)")
    A("			return 1;")
    A("		c0 = cpu_ns();")
    A("		s = now_ns();")
    A("		plc_probe_add(&g_start, s - r);")
    si = 0
    for g in groups:
        dom = g.get("domain")
        members = g.get("members") or []
        if dom == "linux_kernel":
            A("		if (mixed) {")
            A("			long long t0 = now_ns();")
            A("			rc = do_kernel_group(g_seq, &hns);")
            A("			plc_probe_add(&g_rt, now_ns() - t0);")
            A("			if (rc != 0) {")
            A("				fprintf(stderr,")
            A('				        "# uku_abort: cycle=%ld seq=%u rc=%d errno=%d\\n",')
            A("				        k, g_seq, rc, errno);")
            A("				if (rc == -EILSEQ) bad_seq++;")
            A("				else bad_ioctl++;")
            A("				goto uku_fail;")
            A("			}")
            A("			plc_probe_add(&g_hand, hns);")
            A("		} else {")
            for m in members:
                expr = calls.get(m) or ("%s()" % m)
                A("			_a = now_ns();")
                A("			%s;" % expr)
                A("			plc_probe_add(&g_exec_stage[%d], now_ns() - _a);" % si)
                si += 1
            A("		}")
        else:
            for m in members:
                expr = calls.get(m) or ("%s()" % m)
                A("		_a = now_ns();")
                A("		%s;" % expr)
                A("		plc_probe_add(&g_exec_stage[%d], now_ns() - _a);" % si)
                si += 1
    A("		f = now_ns();")
    A("		plc_probe_add(&g_work, f - s);")
    A("		plc_probe_add(&g_resp, f - r);")
    A("		c1 = cpu_ns();")
    A("		plc_probe_add(&g_cpu, c1 - c0);")
    A("		plc_probe_add(&g_preempt, (f - s) - (c1 - c0));")
    A("		if (g_csv)")
    A("			printf(\"%ld,%lld,%lld,%lld,%lld,%lld\\n\", k, s - r, f - s, f - r, c1 - c0, (f - s) - (c1 - c0));")
    for v in digest_vars:
        A("		DIGEST_MIX(%s);" % v)
    A("		if (f - r > DEADLINE_NS) miss++;")
    A("	}")
    if sess_name:
        A("	if (mixed) {")
        A("		struct kfusion_uku_state st;")
        A("		memset(&st, 0, sizeof(st));")
        A("		if (uku_fd >= 0 &&")
        A("		    ioctl(uku_fd, KFUSION_UKU_STATE_IOC, &st) == 0 &&")
        A("		    st.magic == KFUSION_UKU_MAGIC) {")
        A("			sess_val = st.value;")
        A("			sess_ok = 1;")
        A("		}")
        A("	} else {")
        A("		sess_val = %s;" % sess_name)
        A("		sess_ok = 1;")
        A("	}")
    A("	if (uku_fd >= 0) { close(uku_fd); uku_fd = -1; }")
    A("	printf(\"\\n# mode: %s  cycles=%ld\\n\", mode, cycles);")
    A("	plc_probe_head(PERIOD_NS, DEADLINE_NS, (unsigned long long)cycles);")
    A("	plc_probe_line(&g_start);")
    A("	plc_probe_line(&g_work);")
    A("	plc_probe_line(&g_resp);")
    A("	plc_probe_line(&g_cpu);")
    A("	plc_probe_line(&g_preempt);")
    A("	if (mixed) {")
    A("		plc_probe_line(&g_hand);")
    A("		plc_probe_line(&g_rt);")
    A("	}")
    for i, _name in enumerate(order):
        A("	plc_probe_line(&g_exec_stage[%d]);" % i)
    A("	printf(\"# deadline_miss: %ld/%ld\\n\", miss, cycles);")
    A("	printf(\"# uku_seq_mismatch: %ld/%ld\\n\", bad_seq, cycles);")
    A("	printf(\"# uku_ioctl_error: %ld/%ld\\n\", bad_ioctl, cycles);")
    A("	printf(\"# digest: %08x\\n\", g_digest);")
    A("	printf(\"# last_seq: %u g_in=%d g_out=%d\\n\", g_seq, g_in, g_out);")
    if sess_name:
        A('	printf("# session: %s=%%d readback=%%d\\n", sess_val, sess_ok);'
          % sess_name)
    A("	return 0;")
    A("uku_fail:")
    A("	if (uku_fd >= 0) { close(uku_fd); uku_fd = -1; }")
    A("	printf(\"# uku_fail_closed: cycle=%ld seq_err=%ld ioctl_err=%ld\\n\",")
    A("	       k, bad_seq, bad_ioctl);")
    A("	return 3;")
    A("}")
    A("")
    A("int main(int argc, char **argv)")
    A("{")
    A("	const char *mode = (argc > 1) ? argv[1] : \"gold\";")
    A("	long cycles = (argc > 2) ? strtol(argv[2], NULL, 10) : %d;" % cycles)
    A("	if (strcmp(mode, \"gold\") && strcmp(mode, \"mixed\")) {")
    A("		fprintf(stderr, \"usage: %s gold|mixed [cycles] [csv]\\n\", argv[0]);")
    A("		return 2;")
    A("	}")
    A("	if (argc > 3 && !strcmp(argv[3], \"csv\"))")
    A("		g_csv = 1;")
    A("	return run_mode(mode, cycles);")
    A("}")
    return "\n".join(lines) + "\n"


def emit_kbuild(stage_src_name):
    return """obj-m := kfusion_uku.o
kfusion_uku-y := kfusion_uku_mod.o stage_impl.o
ccflags-y := -I$(src) -I$(src)/compat -include $(src)/compat/stdint.h
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--teir", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--probe-header", default="plc_probe__统一测量探针.h")
    ap.add_argument("--period-us", type=float, default=1000)
    ap.add_argument("--cycles", type=int, default=200)
    ap.add_argument("--call", action="append", default=[])
    ap.add_argument("--digest-var", action="append", default=[])
    a = ap.parse_args()
    plan = loadj(a.plan)
    teir = loadj(a.teir)
    adapter = (plan.get("meta") or {}).get("xdom_adapter")
    if adapter != "uku_ioctl":
        print("plan.meta.xdom_adapter=%s，不是 uku_ioctl" % adapter, file=sys.stderr)
        return 2
    calls = {}
    for spec in a.call:
        k, v = spec.split("=", 1)
        calls[k.strip()] = v.strip()
    try:
        kmem, reads, writes, iface = io_vars(plan, teir, calls=calls)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 3
    out = a.out_dir
    os.makedirs(out, exist_ok=True)
    dump(os.path.join(out, "kfusion_uku.h"), HDR)
    dump(os.path.join(out, "compat/stdint.h"), COMPAT)
    dump(os.path.join(out, "kfusion_uku_mod.c"),
         emit_mod(kmem, reads, writes, calls, a.source,
                  session_vars=(iface or {}).get("session_vars")))
    src_abs = os.path.abspath(a.source)
    # stage_impl.c is a copy/include of user source
    with io.open(src_abs, encoding="utf-8") as f:
        body = f.read().replace("#include <stdint.h>",
                                "/* stdint via compat -include */")
        dump(os.path.join(out, "stage_impl.c"), body)
    dump(os.path.join(out, "Kbuild"), emit_kbuild(a.source))
    dump(os.path.join(out, "Makefile"),
         "KDIR ?= /root/linux-5.15.86\n"
         "all:\n\t$(MAKE) -C $(KDIR) M=$(PWD) modules\n"
         "clean:\n\t$(MAKE) -C $(KDIR) M=$(PWD) clean\n")
    sess = set((iface or {}).get("session_vars") or [])
    digest = [v for v in (a.digest_var or ["g_out", "g_seq"]) if v not in sess]
    dump(os.path.join(out, "driver.c"), emit_driver(
        plan, kmem, reads, writes, calls, a.source, a.probe_header,
        a.period_us, a.cycles, digest))
    dump(os.path.join(out, "interface.json"),
         json.dumps(iface, indent=2, ensure_ascii=False) + "\n")
    print("UKU 写出 %s  in=%s out=%s stages=%s" % (out, reads, writes, kmem))
    return 0


if __name__ == "__main__":
    sys.exit(main())
