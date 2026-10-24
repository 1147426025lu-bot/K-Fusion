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
"""

BODY_H = "ecos_fused_body.h"
BODY_C = "ecos_fused_body.c"
MAIN_C = "ecos_nuttx_main.c"
MAKEFILE = "Makefile"
KCONFIG = "Kconfig"
README = "README.build.txt"

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
#include <stdio.h>
#include <time.h>
#include <errno.h>

#include "ecos_fused_body.h"

#define ECOS_GROUPS %(ngroups)d
#define ECOS_PERIOD_US %(period)d

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

int main(int argc, char *argv[])
{
	struct timespec now;
	uint64_t base;
	int i;

	clock_gettime(CLOCK_MONOTONIC, &now);
	base = (uint64_t)now.tv_sec * 1000000u + (uint64_t)now.tv_nsec / 1000u;

	for (;;) {
		base += (uint64_t)ECOS_PERIOD_US;
		for (i = 0; i < ECOS_GROUPS; i++) {
			ecos_sleep_until(base + ecos_phase_us[i]);
			ecos_run_body(i);
		}
	}

	return 0;
}
"""

_MAKEFILE = """# 由 ECoS 方案生成。放到 NuttX 的 apps/<类别>/%(mod)s/ 下使用。
include $(APPDIR)/Make.defs

PROGNAME  = %(mod)s
PRIORITY  = SCHED_PRIORITY_DEFAULT
STACKSIZE = 8192
MODULE    = $(CONFIG_%(upper)s)

MAINSRC = ecos_nuttx_main.c
CSRCS   = ecos_fused_body.c stage_impl.c

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


def emit(ir, module_name):
    ngroups = len(ir.groups)
    layout = " | ".join(
        "%s@%sµs[%s]" % (g.gid,
                        ("%.0f" % g.phase_us) if g.phase_us is not None else "?",
                        " + ".join(s.name for s in g.stages))
        for g in ir.groups)
    body_cases = "\n".join(
        "\tcase %d:\n\t\t%s();\n\t\tbreak;" % (i, g.body)
        for i, g in enumerate(ir.groups))
    phase_list = ",\n".join(
        "\t%du" % int(g.phase_us or 0) for g in ir.groups)
    upper = ("EXAMPLES_" + module_name).upper().replace("-", "_").replace(".", "_")

    return {
        MAIN_C: _MAIN_C % {
            "name": MAIN_C, "ngroups": ngroups, "nstages": len(ir.order),
            "period": int(ir.period_us or 0), "evidence": ir.evidence,
            "layout": layout, "phase_list": phase_list,
            "body_cases": body_cases,
        },
        MAKEFILE: _MAKEFILE % {"mod": module_name, "upper": upper},
        KCONFIG: _KCONFIG % {"mod": module_name, "upper": upper},
        README: _README % {"mod": module_name, "upper": upper},
    }
