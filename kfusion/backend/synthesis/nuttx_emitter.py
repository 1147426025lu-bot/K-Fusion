# -*- coding: utf-8 -*-
"""NuttX 后端生成器。

与 Linux 后端的差异（必须写清楚，否则"两个部署"没有可比性）：

| | Linux ring-0 | NuttX |
|---|---|---|
| 释放原语 | hrtimer + FIFO 内核线程 | `clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME)` |
| 睡眠 | 禁止（内核态） | 允许（任务态） |
| 入口 | `module_init` | `main()`（NuttX app 入口） |
| 周期体 | **同一份** `ecos_fused_body.c` | **同一份** `ecos_fused_body.c` |

周期体共用是关键：否则两个后端的"融合效果"对比会被代码差异污染。
NuttX 侧一个任务按组相位释放 G 次/周期（与 plan 模式同口径，
不是 flat 模式的 N 次），这样唤醒税才是可比的。

探针（`--probe`）
-----------------
量法与 Linux 后端**刻意保持一致**：dz = t(本拍首组开始) − t(上拍末组结束)，
也就是环闭合边的间隔。这样"融合把死区拉长约 800 µs"这条结论在两个宿主上
可以直接对读，而不是各算各的。周期性地打印一行：

    # dz: n=... min=.. p50=.. p99=.. max=.. avg=.. lag_avg=.. (us)

采样用 1 µs 桶的直方图，分位数在打印时现算。
入口名与 PROGNAME 的绑定（fail-closed）
--------------------------------------
NuttX 的 builtin 注册表由 `apps/Make.defs` 的 `REGISTER` 宏**按 app 的 PROGNAME
自动生成**，注册项引用的符号固定是 `<PROGNAME 去横线>_main`：

    { "fiveloop", SCHED_PRIORITY_DEFAULT, 8192, fiveloop_main },

同时 `Application.mk` 会给 MAINSRC 传 `-Dmain=$(PROGSYM)_main`，把源码里的
`main` 改名成注册表要的那个符号。于是有一条**隐式但硬**的契约：

    源码里的入口函数名  ==  PROGNAME 去横线 + "_main"

踩过的坑：跨域那版把 PROGNAME 改成 `fiveloop`（复用已打开的
CONFIG_BENCHMARK_FIVELOOP），入口函数却叫 `xdom_main` → 注册表引用
`fiveloop_main`、源码只给 `xdom_main` → 链接期 `undefined reference`。
第一次构建能过纯属侥幸：`libapps.a` 里还残留着上一轮的 `fiveloop_main.c.o`
顶着这个符号（NuttX 的 libapps.a 会保留历史成员，踩过）。

所以这里**不允许**两个名字各走各的：`main_name` 由 `prog_name` 派生，
调用方只给 PROGNAME。
"""

BODY_H = "ecos_fused_body.h"
BODY_C = "ecos_fused_body.c"
MAIN_C = "ecos_nuttx_main.c"
MAKEFILE = "Makefile"
KCONFIG = "Kconfig"
README = "README.build.txt"

DZ_BUCKETS = 4096

_MAIN_C = """/* %(name)s —— 由 ECoS 融合方案生成的 NuttX 应用；不要手改。
 *
 * 方案：%(ngroups)d 组 / %(nstages)d 阶段，周期 %(period)d µs，证据级别 %(evidence)s
 * 组落位：%(layout)s
 *
 * 单任务 + 绝对时刻睡眠：每周期按各组相位释放 %(ngroups)d 次（plan 口径）。
 * 周期体来自 ecos_fused_body.c（与 Linux 后端**同一份**），差异只在宿主。
 */
#include <nuttx/config.h>

#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <time.h>
#include <errno.h>

#include "ecos_fused_body.h"
%(xdom_include)s

#define ECOS_GROUPS %(ngroups)d
#define ECOS_XDOM_REPORT_EVERY 1000u	/* 跨域观测的串口报告间隔（拍） */
#define ECOS_PERIOD_US %(period)d
#define ECOS_FIRST_GROUP %(first_g)d
#define ECOS_LAST_GROUP %(last_g)d
%(probe_defs)s
static const uint64_t ecos_phase_us[ECOS_GROUPS] = {
%(phase_list)s
};

/* 睡到绝对时刻（µs）。EINTR 就重试——NuttX 上信号并不罕见。 */
static void ecos_sleep_until(uint64_t abs_us)
{
	struct timespec ts;

	ts.tv_sec = (time_t)(abs_us / 1000000u);
	ts.tv_nsec = (long)((abs_us %% 1000000u) * 1000u);

	while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR)
		;
}

static void ecos_run_body(int id)
{
	switch (id) {
%(body_cases)s
	}
}

int %(main_name)s(int argc, char *argv[])
{
	struct timespec now;
	uint64_t base;
	unsigned long periods = 0;
	int i;

%(xdom_init)s
	clock_gettime(CLOCK_MONOTONIC, &now);
	base = (uint64_t)now.tv_sec * 1000000u + (uint64_t)now.tv_nsec / 1000u;

	for (;;) {
%(xdom_anchor)s%(t_first_decl)s
		base += (uint64_t)ECOS_PERIOD_US;
		for (i = 0; i < ECOS_GROUPS; i++) {
			ecos_sleep_until(base + ecos_phase_us[i]);
%(probe_before)s
			ecos_run_body(i);
%(probe_after)s
		}
		periods++;
%(probe_report)s
%(xdom_report)s
	}

	return 0;
}
"""

_MAKEFILE = """# 由 ECoS 方案生成。放到 NuttX 的 apps/<类别>/%(mod)s/ 下使用。
include $(APPDIR)/Make.defs

# ⚠ PROGNAME 与源码里的入口名是**绑定的**：NuttX 的 builtin 注册表按
#   PROGNAME 生成注册项，引用符号是 <PROGNAME 去横线>_main；而
#   Application.mk 会给 MAINSRC 传 -Dmain=<PROGNAME 去横线>_main。
#   两者必须一致，否则链接期报 undefined reference。
#   本文件由生成器产出，PROGNAME=%(prog)s、入口=%(main_name)s 一一对应。
PROGNAME  = %(prog)s
PRIORITY  = SCHED_PRIORITY_DEFAULT
STACKSIZE = 8192
MODULE    = $(CONFIG_%(upper)s)

MAINSRC = ecos_nuttx_main.c
CSRCS   = ecos_fused_body.c stage_impl.c%(xdom_srcs)s

include $(APPDIR)/Application.mk
"""

_KCONFIG = """config %(upper)s
	tristate "ECoS fused control (%(mod)s)"
	default n
	---help---
		Generated from an ECoS fusion plan. Periodic body is shared with the
		Linux ring-0 backend; only the host differs.
"""

_README = """%(mod)s —— NuttX 构建说明（必须在 NuttX 树上构建）

1) 阶段实现编成一个对象（NuttX 侧不需要内核化 Pass，但需要 NuttX 的 libc）：

    交叉编译 stage_impl.c -> stage_impl.o （用 NuttX 的 toolchain 与 include）

2) 放进 apps/<类别>/%(mod)s/，打开 Kconfig 选项 %(upper)s，然后：

    ./tools/configure.sh -l <board>:<config>
    make -j

3) 板上执行：

    nsh> %(mod)s

注意：
  · 周期体 ecos_fused_body.c 与 Linux 后端逐字节相同（同一方案），
    所以两者差异只来自宿主（hrtimer vs 睡眠），不是代码差异。
  · 一个任务、按组相位释放 G 次/周期（plan 口径）；不是每阶段一次。
"""


def _probe_blocks(buckets):
    """探针：与 Linux 后端同一量法（dz = 本拍首组 − 上拍末组）。"""
    defs = """#define ECOS_DZ_BUCKETS %d
#define ECOS_REPORT_EVERY 2000

static uint64_t dz_last_us;
static bool dz_have;
static uint64_t dz_sum_us, dz_min_us, dz_max_us, dz_lag_sum_us;
static unsigned long dz_count;
static unsigned int dz_hist[ECOS_DZ_BUCKETS];

/* 打印一行 dz 统计（1 µs 桶，分位数现算） */
static void ecos_dz_report(void)
{
	static const unsigned int pct[3] = { 500, 990, 999 };
	unsigned long cum = 0;
	long found[3] = { -1, -1, -1 };
	int i, k;

	if (!dz_count)
		return;

	for (i = 0; i < ECOS_DZ_BUCKETS; i++) {
		cum += dz_hist[i];
		for (k = 0; k < 3; k++)
			if (found[k] < 0 &&
			    cum * 1000ULL >= (unsigned long long)dz_count * pct[k])
				found[k] = i;
	}

	printf("# dz: n=%%lu min=%%llu p50=%%ld p99=%%ld p99.9=%%ld max=%%llu "
	       "avg=%%llu lag_avg=%%llu (us)\\n",
	       dz_count, (unsigned long long)dz_min_us, found[0], found[1],
	       found[2], (unsigned long long)dz_max_us,
	       (unsigned long long)(dz_sum_us / dz_count),
	       (unsigned long long)(dz_lag_sum_us / dz_count));
	fflush(stdout);
}
""" % buckets

    before = """			if (i == ECOS_FIRST_GROUP) {
				clock_gettime(CLOCK_MONOTONIC, &now);
				t_first = (uint64_t)now.tv_sec * 1000000u +
					  (uint64_t)now.tv_nsec / 1000u;
				if (dz_have) {
					uint64_t d = t_first - dz_last_us;

					if (d > 0) {
						dz_count++;
						dz_sum_us += d;
						if (dz_count == 1 || d < dz_min_us)
							dz_min_us = d;
						if (d > dz_max_us)
							dz_max_us = d;
						if (d < ECOS_DZ_BUCKETS)
							dz_hist[d]++;
					}
				}
			}"""
    after = """			if (i == ECOS_LAST_GROUP) {
				clock_gettime(CLOCK_MONOTONIC, &now);
				dz_last_us = (uint64_t)now.tv_sec * 1000000u +
					     (uint64_t)now.tv_nsec / 1000u;
				dz_have = true;
				dz_lag_sum_us += dz_last_us - t_first;
			}"""
    report = """		if (periods % ECOS_REPORT_EVERY == 0)
			ecos_dz_report();"""
    return defs, before, after, report


def nuttx_symbol(prog_name):
    """NuttX builtin 注册表给 app 分配的入口符号名。

    与 apps/Make.defs 的 REGISTER 宏同一条规则：PROGNAME 去横线 + "_main"。
    源码里的入口名必须**正好**是它，否则链接期 undefined reference。
    """
    return prog_name.replace("-", "_") + "_main"


def emit(ir, module_name, main_name=None, probe=False, xdom=False,
         prog_name=None):
    """prog_name：NuttX app 的 PROGNAME（注册表按它生成入口符号）。

    main_name：一般**不用传**——入口名由 prog_name 派生（见 nuttx_symbol）。
    只有当调用方明确知道自己要接进哪个既有 app 骨架时才传，且必须与
    nuttx_symbol(prog_name) 一致，否则这里直接报错（fail-closed）：
    让它在生成期炸掉，比让板上链接失败再回头查要省事得多。
    """
    # PROGNAME：默认取模块名；跨域那版要复用板上已打开的
    # CONFIG_BENCHMARK_FIVELOOP，所以允许调用方显式指定。
    prog = prog_name or module_name
    want_sym = nuttx_symbol(prog)
    if main_name is None:
        main_name = want_sym
    elif main_name != want_sym:
        raise ValueError(
            "NuttX 入口名与 PROGNAME 不一致：入口=%r，但 PROGNAME=%r 对应的"
            "注册表符号是 %r。\n"
            "  NuttX 的 builtin 注册表按 PROGNAME 生成，引用的是 "
            "<PROGNAME 去横线>_main；\n"
            "  Application.mk 同时给 MAINSRC 传 -Dmain=<PROGNAME 去横线>_main。"
            "\n  两者必须一致，否则链接期 undefined reference。"
            % (main_name, prog, want_sym))

    ngroups = len(ir.groups)
    layout = " | ".join(
        "%s@%sµs[%s]" % (g.gid,
                        ("%.0f" % g.phase_us) if g.phase_us is not None else "?",
                        " + ".join(s.name for s in g.stages))
        for g in ir.groups)
    # 跨域观测：把 --digest-var 指定的全局量周期性打到串口。
    # 没有它，这一侧发生了什么完全看不见——而跨域实验的核心恰恰是"两边看到的值
    # 是否一致（差一拍）"。root 侧有 sysfs，inmate 侧只有 mini-UART。
    # 跨域逐拍证据：每 ECOS_XDOM_REPORT_EVERY 拍把各通道的 prod/cons/age 打到串口。
    # 串口是 inmate 唯一的窗口（root 侧有 sysfs），年龄证据不从这走就看不见。
    _xr = []
    if xdom:
        _xr.append("\t\tif (periods % ECOS_XDOM_REPORT_EVERY == 0)\n"
                   "\t\t\tecos_xdom_report_evidence();")
    dvs = getattr(ir, "digest_vars", None) or []
    if dvs:
        calls = ['printf("ecos: periods=%lu", (unsigned long)periods);']
        for v in dvs:
            calls.append('printf(" %s=%%ld", (long)%s);' % (v, v))
        calls.append('printf("\\n");')
        _xr.append("\t\tif (periods % ECOS_XDOM_REPORT_EVERY == 0) {\n"
                   + "\n".join("\t\t\t" + c for c in calls)
                   + "\n\t\t}")
    xdom_report = "\n".join(_xr)

    body_cases = "\n".join(
        "\tcase %d:\n\t\t%s();\n\t\tbreak;" % (i, g.body)
        for i, g in enumerate(ir.groups))
    phase_list = ",\n".join(
        "\t%du" % int(g.phase_us or 0) for g in ir.groups)
    upper = ("EXAMPLES_" + module_name).upper().replace("-", "_").replace(".", "_")

    phases = [((g.phase_us if g.phase_us is not None else 0.0), i)
              for i, g in enumerate(ir.groups)]
    first_g, last_g = min(phases)[1], max(phases)[1]

    if probe:
        probe_defs, probe_before, probe_after, probe_report = \
            _probe_blocks(DZ_BUCKETS)
        t_first_decl = "\t\tuint64_t t_first = 0;\n"
    else:
        probe_defs, probe_before, probe_after, probe_report = "", "", "", ""
        t_first_decl = ""

    return {
        MAIN_C: _MAIN_C % {
            "name": MAIN_C, "ngroups": ngroups, "nstages": len(ir.order),
            "period": int(ir.period_us or 0), "evidence": ir.evidence,
            "layout": layout, "phase_list": phase_list, "body_cases": body_cases,
            "first_g": first_g, "last_g": last_g, "main_name": main_name,
            "t_first_decl": t_first_decl,
            "probe_defs": probe_defs, "probe_before": probe_before,
            "probe_after": probe_after, "probe_report": probe_report,
            "xdom_report": xdom_report,
            "xdom_init": ("\t/* 跨域数据桥：先把两个环定下来（PR10-C） */\n"
                          "\tecos_xdom_init();\n" if xdom else ""),
            "xdom_include": ('#include "ecos_xdom.h"\t/* 跨域数据桥 */\n'
                             if xdom else ""),
            # 周期锚点：首拍真正跑起来的时刻（base 本身就是 µs）。
            # 不用 init 时刻——init 之后还要等环、建定时器，差好几百 µs。
            "xdom_anchor": ('\t\tif (periods == 0)\n'
                            '\t\t\tprintf("xdom anchor base_us=%llu now_us=%u period_us=%d\\n",\n'
                            '\t\t\t       (unsigned long long)base, ecos_xdom_now_us(),\n'
                            '\t\t\t       (int)ECOS_PERIOD_US);\n'
                            if xdom else ""),
        },
        MAKEFILE: _MAKEFILE % {"mod": module_name, "upper": upper,
                               "prog": prog, "main_name": main_name,
                               "xdom_srcs": " ecos_xdom.c" if xdom else ""},
        KCONFIG: _KCONFIG % {"mod": module_name, "upper": upper},
        README: _README % {"mod": module_name, "upper": upper},
    }
