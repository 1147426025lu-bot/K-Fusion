# -*- coding: utf-8 -*-
"""LET 部署合成（用户态 / NuttX 应用）：把已验证方案的时序变成 plc_let 作业表。

背景与诚实声明（PR9 文献纪律）
--------------------------------
LET（Logical Execution Time）**不是本项目的发明**。它来自 Giotto
（Henzinger 等，~2003，"Hen03b"）；其后 TDL（Timing Definition Language）已
提供**完整的商业化代码生成**——E-machine + 从既有 connect/sequencing/period/
WCRT 自动生成 TDL 程序，并把"输入在 LET 起点缓存、输出在 LET 终点提交"作为生成
产物（这就是 LET 的数据语义，不是新东西）。Ptolemy II、Simulink/ERIKA、AUTOSAR
也都有 LET 集成。因此本模块**不声称新算法、也不声称新的 LET 代码生成技术**。

K-Fusion 在这里的贡献是**组合层面、且对正确性有实际意义**的一点：

  · 同一份**已验证**的融合 IR（plan.verification.ok=true）既生成 ring-0 内核
    模块（linux_kernel_emitter），也生成这里的用户态 LET 部署——"验证过的时序"
    与"真正跑的时序"由同一工具链产出，不存在手写作业表悄悄偏离方案的可能
    （这正是 PR9 门禁要堵的"同一份事实两套解释"）。

  · 用户不再手写 g_jobs[] 与 main()（见 examples/plc_let_demo__LET演示/
    plc_let_demo__主程序.c）；周期 / 相位 / LET 窗口全部从方案推出，可溯源、
    可复核。

边界（与另两个后端一致）
------------------------
  · 本模块**只产出"作业表 + 入口脚手架"**；真正的调度——release 在逻辑时刻、
    按相位释放、超时/落后统计——仍由手写运行时 plc_let__LET.c 负责。它是对所有
    部署共用的调度库，和 stage_impl.o 一样是被链接的，而不是被重写的。
  · 周期体（ecos_fused_body.c）里仍然只有阶段调用、没有调度原语；作业表只是把
    每个 ecos_body_gN() 包成一个带 period/phase/let 的 plc_let_job。
  · 相位 / 周期单位：IR 用 µs，plc_let__LET.c 用 ns；本模块负责换算。
"""

import os

# 与 examples/plc_let_demo__主程序.c 的编译约定一致：运行时头靠 -I 引入。
_LET_H = """/* ecos_let_jobs.h —— 由 ECoS 融合方案生成的 LET 作业表声明；不要手改。
 *
 * 方案：%(ngroups)d 组 / %(nstages)d 阶段，周期 %(period)s µs
 * 生成依据：plan.verification.ok=%(verified)s，执行时间证据级别 %(evidence)s
 *
 * 调度（逻辑 release、按相位释放、超时/落后统计）由 plc_let__LET.c 负责；
 * 本文件只声明"每个组是一个 LET 作业"及其符号。
 */
#ifndef ECOS_LET_JOBS_H
#define ECOS_LET_JOBS_H

#include "plc_let__LET.h"	/* 运行时：plc_let_job / plc_let_runtime */

/* 阶段实现的声明在 ecos_fused_body.h（两个后端共用的周期体）。 */
#include "ecos_fused_body.h"

extern const struct plc_let_job %(name)s_jobs[];
extern const unsigned %(name)s_njobs;

#endif /* ECOS_LET_JOBS_H */
"""

_LET_C = """/* ecos_let_jobs.c —— 由 ECoS 融合方案生成的 LET 作业表；不要手改。
 *
 * 每个方案组 → 一个 plc_let_job：
 *   .fn       = ecos_body_gN()（周期体里该组的阶段调用序列）
 *   .period_ns= 方案周期（µs→ns）
 *   .phase_ns = 该组释放相位（µs→ns）
 *   .let_ns   = LET 窗口（默认 = 周期 × %(let_frac)s；组实测执行 ≈%(exec)s µs）
 *   .prio     = 组序（相位早的优先，避免同时就绪时抢错）
 *   .flags    = %(flags)s
 *
 * 多作业在同一线程里按逻辑时刻串行（LET 的标准单线程模型）；运行时负责
 * pick_ready / catch_up / 超时统计。
 */
#include "ecos_let_jobs.h"

%(stage_protos)s
%(job_fns)s

const struct plc_let_job %(name)s_jobs[] = {
%(entries)s
};

const unsigned %(name)s_njobs =
\tsizeof(%(name)s_jobs) / sizeof(%(name)s_jobs[0]);
"""

_LET_MAIN = """/* ecos_let_main.c —— 由 ECoS 融合方案生成的 LET 入口；不要手改。
 *
 * 复刻 examples/plc_let_demo__主程序.c 的手写入口，但作业表来自方案：
 *   signal → plc_let_init(STRICT) → plc_let_run → 打印统计。
 * 编译：gcc -O2 -I../../include ecos_let_main.c ecos_let_jobs.c \\
 *         ecos_fused_body.c src/runtime/plc_let__LET.c stage_impl.c -o let_app
 */
#include <signal.h>
#include <stdio.h>
#include <stdint.h>

#include "plc_let__LET.h"
#include "ecos_let_jobs.h"

volatile int shutdown;

#ifndef LET_RUN_LOOPS
#define LET_RUN_LOOPS %(loops)d
#endif

static void on_signal(int sig)
{
	(void)sig;
	shutdown = 1;
}

int main(void)
{
	struct plc_let_runtime rt;
	struct plc_let_stats st;

	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);

	if (plc_let_init(&rt, %(name)s_jobs, %(name)s_njobs,
			 PLC_LET_MODE_STRICT) != 0)
		return 1;

	printf("let: start %%u jobs period=%%lluus let_frac=%%s mode=STRICT\\n",
	       %(name)s_njobs,
	       (unsigned long long)(%(period)d),
	       "%(let_frac)s");

	plc_let_run(&rt);

	plc_let_get_stats(&rt, 0, &st);
	printf("let: exit releases=%%llu release_jitter_max_ns=%%lld\\n",
	       (unsigned long long)st.releases,
	       (long long)st.release_jitter_max_ns);
	plc_let_print_summary(stdout, &rt, "app", 0);
	return 0;
}
"""

_README = """%(name)s —— LET 用户态部署构建说明（由 ECoS 方案生成）

本目录是「用户态 LET 部署」的产物：让已验证的控制方案走 plc_let__LET.c 的
STRICT 逻辑-release 调度，而不是手写 g_jobs[] / main()。

编译（需要 gcc / 阶段实现；本机 Windows 无编译器，构建在目标侧完成）：

  gcc -O2 -I<kfusion>/include \\
      ecos_let_main.c ecos_let_jobs.c ecos_fused_body.c \\
      <kfusion>/src/runtime/plc_let__LET.c stage_impl.c \\
      -o %(name)s_let_app

  · stage_impl.c 是实现各阶段函数的源（即 emit.py 的 --source）。
  · ecos_fused_body.c 是周期体（两个后端共用，只有阶段调用，无调度原语）。
  · plc_let__LET.c 是调度库（逻辑 release + 按相位释放 + 超时/落后统计）。

运行：

  LET_RUN_LOOPS=500 ./%(name)s_let_app   # 跑 500 拍后自停
  # 或不设，按 Ctrl-C 停止；停止时打印 LetSummary（releases / jitter / overrun）

诚实声明：LET 时序模型（Giotto/Hen03b）与「从时序模型生成代码」(TDL 等) 都是
既有技术；本产物不声称新算法。它的意义是——这份 LET 部署的周期/相位/LET 窗口
全部由**已验证的方案**推出，与 ring-0 内核模块同源，不存在手写作业表偏离方案。
"""


def _ns(us):
    return int(round(float(us) * 1000.0)) if us is not None else 0


def _ident(s):
    out = []
    for ch in s or "":
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    t = "".join(out)
    return t if t and not t[:1].isdigit() else ("j_" + t)


def _call_stmt(stage):
    c = (stage.call or ("%s()" % stage.name)).strip()
    if c.endswith(";"):
        return c
    return c + ";"


def emit(ir, name, let_frac=0.8, skip_missed=True, loops=0):
    """从 FusionIR 生成 LET 作业表 + 入口。

    Args:
        ir: FusionIR（必须 period_us 已知、组相位已知）。
        name: 模块名（与 emit.py 的 name 同源）。
        let_frac: LET 窗口占周期的比例（默认 0.8，与 plc_let_demo 约定一致）。
        skip_missed: 是否给作业加 PLC_LET_SKIP_MISSED（严格周期：落后不追赶，
                    只暴露滞后）。默认开——与内核后端「固定周期控制律不该用追赶
                    抹掉迟到」的纪律一致。
        loops: LET_RUN_LOOPS 默认值（0 = 直到收到 SIGINT 才停）。

    Returns:
        {filename: text}（4 个文件）。
    """
    ngroups = len(ir.groups)
    period_us = int(ir.period_us or 0)
    period_ns = period_us * 1000
    if period_ns <= 0:
        raise ValueError("LET 部署需要非零周期（IR.period_us），得到 %r" % (ir.period_us,))
    if not (0.0 < let_frac <= 1.0):
        raise ValueError("let_frac 必须在 (0, 1]，得到 %r" % (let_frac,))

    # LET 窗口：默认 = 周期 × let_frac；上限等于周期（单周期作业不能超过其周期）。
    let_ns = min(int(round(period_ns * let_frac)), period_ns)
    flags = "PLC_LET_SKIP_MISSED" if skip_missed else "0"
    let_frac_str = ("%.2f" % let_frac).rstrip("0").rstrip(".")

    job_fns, entries = [], []
    exec_parts = []
    events = list(getattr(ir, "let_events", None) or [])
    prio = 1

    def add_job(jname, fn_name, body_lines, phase_us, window_ns, comment):
        nonlocal prio
        job_fns.append(
            "static void %s(void *ctx)\n{\n\t(void)ctx;\n%s}\t/* %s */"
            % (fn_name, body_lines, comment))
        entries.append(
            "\t{ .name = \"%s\", .fn = %s, .ctx = NULL,\n"
            "\t  .period_ns = %d, .phase_ns = %d,\n"
            "\t  .let_ns = %d, .prio = %d, .flags = %s },"
            % (jname, fn_name, period_ns, _ns(phase_us),
               max(int(window_ns), 1000), prio, flags))
        prio += 1

    for i, g in enumerate(ir.groups):
        phase_us = g.phase_us if g.phase_us is not None else 0.0
        exec_parts.append("%.2f" % g.exec_us)
        names = {s.name for s in g.stages}
        gevents = [e for e in events if e.get("stage") in names]
        defer = set()
        pin_us = []
        for e in gevents:
            kind = e.get("kind")
            at = float(e.get("at_us") or 0)
            pin_us.append(at)
            if kind == "output_commit":
                defer.add(e.get("stage"))
            elif kind == "input_snapshot" and abs(at - phase_us) > 1e-6:
                defer.add(e.get("stage"))

        compute = [s for s in g.stages if s.name not in defer]
        if gevents:
            # 计算 LET 窗口止于本拍最早的提交/快照钉扎
            later = [u for u in pin_us if u > phase_us + 1e-9]
            win = _ns(min(later) - phase_us) if later else let_ns
            if compute:
                lines = "".join("\t%s\t/* %s */\n" % (_call_stmt(s), s.name)
                                for s in compute)
                add_job(g.gid, "ecos_let_fn_%s" % g.body, lines, phase_us, win,
                        "组 %s 计算 @%.0f µs；钉扎阶段另表提交/采样" % (g.gid, phase_us))
            by_name = {s.name: s for s in g.stages}
            for e in sorted(gevents, key=lambda x: float(x.get("at_us") or 0)):
                st = by_name.get(e.get("stage"))
                if st is None:
                    continue
                at = float(e.get("at_us") or 0)
                remain = period_us - at
                win_e = _ns(remain) if remain > 0 else 1000
                kind = e.get("kind") or "let"
                fn = "ecos_let_fn_%s_%s" % (kind, _ident(st.name))
                lines = "\t%s\t/* %s @%.0f µs */\n" % (_call_stmt(st), st.name, at)
                add_job("%s_%s" % (kind, st.name), fn, lines, at, win_e,
                        "%s 钉扎 %s" % (kind, st.name))
        else:
            fn_name = "ecos_let_fn_%s" % g.body
            job_fns.append(
                "static void %s(void *ctx)\n{\n\t(void)ctx;\n\t%s();\t/* 组 %s："
                "相位 %.0f µs，执行 ≈%.2f µs */\n}"
                % (fn_name, g.body, g.gid, phase_us, g.exec_us))
            entries.append(
                "\t{ .name = \"%s\", .fn = %s, .ctx = NULL,\n"
                "\t  .period_ns = %d, .phase_ns = %d,\n"
                "\t  .let_ns = %d, .prio = %d, .flags = %s },"
                % (g.gid, fn_name, period_ns, _ns(phase_us), let_ns, prio, flags))
            prio += 1

    jobs_h = _LET_H % {
        "ngroups": ngroups,
        "nstages": len(ir.order),
        "period": ("%.0f" % ir.period_us) if ir.period_us else "?",
        "verified": "true" if ir.verified else "false（未验证，产物只能用于对照）",
        "evidence": ir.evidence,
        "name": name,
    }
    proto_lines, seen_fn = [], set()
    for g in ir.groups:
        for s in g.stages:
            raw = (s.call or ("%s()" % s.name)).strip().rstrip(";")
            fn = raw.split("(", 1)[0].strip()
            if not fn or fn in seen_fn:
                continue
            seen_fn.add(fn)
            inside = ""
            if "(" in raw:
                inside = raw[raw.find("(") + 1:raw.rfind(")")].strip()
            if inside:
                proto_lines.append("void %s();" % fn)
            else:
                proto_lines.append("void %s(void);" % fn)

    jobs_c = _LET_C % {
        "name": name,
        "let_frac": let_frac_str,
        "exec": "/".join(exec_parts) if exec_parts else "0",
        "flags": flags,
        "stage_protos": ("\n".join(proto_lines) + "\n") if proto_lines else "",
        "job_fns": "\n\n".join(job_fns),
        "entries": "\n".join(entries),
    }
    main_c = _LET_MAIN % {
        "name": name,
        "loops": int(loops),
        "period": period_us,
        "let_frac": let_frac_str,
    }
    readme = _README % {"name": name}
    return {
        "ecos_let_jobs.h": jobs_h,
        "ecos_let_jobs.c": jobs_c,
        "ecos_let_main.c": main_c,
        "README.let.txt": readme,
    }
