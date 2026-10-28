#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fusion_codegen__生成融合代码.py — 把融合方案变成可编译的周期体驱动

它生成一个 C 文件，用**同一次释放**把同一组的阶段连着跑完，从而做到
"方案里有几组，每周期就释放几次"。两种模式共用同一份代码：

    plan  按方案分组：每组一次释放（G 次/周期）
    flat  不融合的对照：每个阶段一次释放（N 次/周期）

两者的**工作量完全相同**（同样的阶段、同样的顺序、同样的数据），
唯一的自变量是"每周期释放几次"。这样测出来的差异只能归于释放本身，
不会掺进代码差异——与项目其他实验的纪律一致。

测量沿用统一契约（`plc_probe__统一测量探针.h`）：
  - `start_lat`：**每次释放**一个样本（plan 模式 G 个/周期，flat 模式 N 个/周期）
  - `exec`：**每周期**一个样本 = 各阶段执行时间之**和**（真实工作量）
  - `span`：每周期一个样本 = 最后一个完成 − 第一次开始（含阶段之间的空等）
  - `response`：每周期一个样本 = 最后一个完成 − 第一次标称释放（端到端 R）
  - `exec_<stage>`：单阶段执行时间

  为什么要单独拆出 `span`：在"每阶段一次释放"的对照里，各阶段之间存在真实的空等，
  若把 `f_last − s_first` 当 exec，会把 600 µs 的睡眠算成"执行时间"——
  那是夹具造成的假象，不是工作量。

用法：
    python fusion_codegen__生成融合代码.py plan.json -o driver.c \\
        --stage-source "../../examples/ecos/four_stage_chain__四阶段示例.c" \\
        --call "stage_pid=stage_pid(1000)" --period-us 1000 --step-us 200 \\
        --cycles 60000
"""
import argparse
import io
import json
import sys


def load_json(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def c_ident(name):
    """阶段名 → 合法 C 标识符（函数名里可能有 . 或 -）。"""
    out = []
    for ch in name:
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    s = "".join(out)
    return s if not s[:1].isdigit() else "s_" + s


def main():
    ap = argparse.ArgumentParser(description="融合方案 → C 驱动")
    ap.add_argument("plan")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--stage-source", required=True,
                    help="含阶段实现的 .c（会 #include 进来）")
    ap.add_argument("--call", action="append", default=[],
                    help="阶段名=调用表达式，如 stage_pid=stage_pid(1000)")
    ap.add_argument("--probe-header", default="plc_probe__统一测量探针.h")
    ap.add_argument("--period-us", type=float, default=1000.0)
    ap.add_argument("--deadline-us", type=float, default=None)
    ap.add_argument("--step-us", type=float, default=200.0,
                    help="flat 模式里相邻阶段的相位间隔（plan 模式同样用它给各组错相）")
    ap.add_argument("--cycles", type=int, default=60000)
    ap.add_argument("--buckets", type=int, default=1024,
                    help="直方图桶数（µs）。实验的 response 可能远超 200，"
                         "默认给大一点，避免全部溢出到桶顶")
    ap.add_argument("--digest-var", action="append", default=[],
                    help="要纳入摘要的全局变量名（可重复）。每周期结束累加一次 "
                         "FNV-1a，打印 '# digest:'。两个模式的摘要必须相同——"
                         "这是「融合没有改变采样序列」的判据")
    ap.add_argument("--expect-order", default=None,
                    help="逗号分隔的阶段执行顺序（应为相位序）。plan 模式展平后"
                         "必须与之一致，否则延迟语义不成立")
    ap.add_argument("--entry", default="main",
                    help="入口函数名（默认 main；编成 NuttX builtin 时给 "
                         "fiveloop_main 这类名字，NSH 以该名调用）")
    ap.add_argument("--threads", action="store_true",
                    help="额外生成 multi 模式：每阶段一个真线程（pthread），"
                         "按各自相位释放。用于检验「融合省下的唤醒税」在真多任务"
                         "部署下是被抵掉还是保持")
    args = ap.parse_args()

    plan = load_json(args.plan)
    groups = plan.get("groups") or []
    if not groups:
        raise SystemExit("plan 里没有 groups")

    stage_names = [m for g in groups for m in g["members"]]

    # 顺序守卫：融合后的执行顺序必须还是相位序。若规划器或手工方案把它改了，
    # 环闭合边的间隔就不再 ≥ 一拍，采样序列会**静默地**变。
    if args.expect_order:
        want = [x.strip() for x in args.expect_order.split(",") if x.strip()]
        if want != stage_names:
            raise SystemExit(
                "执行顺序与相位序不一致：\n  期望 %s\n  实际 %s"
                % (" -> ".join(want), " -> ".join(stage_names)))
    calls = {}
    for spec in args.call:
        if "=" not in spec:
            raise SystemExit("--call 格式应为 名字=表达式：%r" % spec)
        k, v = spec.split("=", 1)
        calls[k.strip()] = v.strip()
    for s in stage_names:
        calls.setdefault(s, "%s()" % s)

    per = int(round(args.period_us * 1000))
    step = int(round(args.step_us * 1000))
    dl = int(round((args.deadline_us or args.period_us) * 1000))
    n_stage = len(stage_names)
    n_rel = len(groups)

    # flat 模式把 N 个阶段铺在一个周期里：相位跨了周期，越界的释放会与下一拍的
    # 第一次释放撞车（drift），测出来的 start_lat 就不是释放代价了。
    if (n_stage - 1) * step > per:
        raise SystemExit(
            "相位铺不下：(N-1)×step = %.0f µs > period %.0f µs。\n"
            "  请调小 --step-us 或加大 --period-us；否则 flat 模式的释放会跨到下一拍。"
            % ((n_stage - 1) * args.step_us, args.period_us))

    # 两种模式的"释放 → 阶段列表"表。plan 用组上的 release_phase_us，
    # 不得用组序号×step（两段合法方案会把 s3 错放到 200µs 而不是 400µs）。
    def _phase_ns(g, fallback_i):
        v = g.get("release_phase_us")
        if v is None:
            v = fallback_i * args.step_us
        return int(round(float(v) * 1000.0))

    plan_rel = [(g["id"], list(g["members"]), _phase_ns(g, i))
                for i, g in enumerate(groups)]
    flat_rel = [(s, [s], int(round(i * args.step_us * 1000.0)))
                for i, s in enumerate(stage_names)]

    L = []
    A = L.append
    A("/* 由 fusion_codegen__生成融合代码.py 生成，不要手改。")
    A(" *")
    A(" * 方案：%d 组 / %d 阶段  平台 %s"
      % (n_rel, n_stage, (plan.get("meta") or {}).get("platform", "?")))
    for g in groups:
        A(" *   %-4s %s" % (g["id"], " + ".join(g["members"])))
    A(" *")
    A(" * plan 模式：%d 次释放/周期；flat 模式：%d 次释放/周期。"
      % (n_rel, n_stage))
    A(" * 两者工作量相同，唯一自变量是释放次数。")
    A(" */")
    A("#include <stdio.h>")
    A("#include <stdlib.h>")
    A("#include <string.h>")
    A("#include <stdint.h>")
    A("#include <time.h>")
    A("#include <errno.h>")
    A("")
    A("/* 先定桶数再包含探针头：桶太少会让所有样本挤进桶顶，p50 变成假值 */")
    A("#define PLC_PROBE_BUCKETS %d" % args.buckets)
    A('#include "%s"' % args.probe_header)
    A('#include "%s"' % args.stage_source)
    A("")
    A("#define PERIOD_NS  %dLL" % per)
    A("#define DEADLINE_NS %dLL" % dl)
    A("#define STEP_NS    %dLL" % step)
    A("#define MAX_STAGES %d" % max(1, n_stage))
    A("")
    A("static struct plc_probe_series g_start, g_work, g_span, g_resp;")
    A("static struct plc_probe_series g_exec_stage[MAX_STAGES];")
    A("static struct plc_probe_series g_instant[MAX_STAGES];")
    A("static long long g_last_misses;")
    A("")
    if args.digest_var:
        A("/* 采样序列摘要：每周期结束时把指定全局量揉进 FNV-1a。")
        A(" * plan / flat 两个模式的摘要必须逐位相同——两模式的阶段顺序、"
          "初始值完全相同，")
        A(" * 唯一差别是释放次数；摘要不同就说明融合改变了数据流，"
          "而不是只改变了唤醒路径。 */")
        A("static uint32_t g_digest = 2166136261u;")
        A("#define DIGEST_MIX(x) do { g_digest ^= (uint32_t)(x); "
          "g_digest *= 16777619u; } while (0)")
        A("")
    A("static long long now_ns(void)")
    A("{")
    A("\tstruct timespec t;")
    A("\tclock_gettime(CLOCK_MONOTONIC, &t);")
    A("\treturn (long long)t.tv_sec * 1000000000LL + t.tv_nsec;")
    A("}")
    A("")
    A("static void bump_ns(struct timespec *t, long long ns)")
    A("{")
    A("\tt->tv_nsec += (long)(ns % 1000000000LL);")
    A("\tt->tv_sec += (time_t)(ns / 1000000000LL);")
    A("\twhile (t->tv_nsec >= 1000000000L) {")
    A("\t\tt->tv_nsec -= 1000000000L;")
    A("\t\tt->tv_sec += 1;")
    A("\t}")
    A("}")
    A("")

    # 每阶段的包装函数（调用表达式可能带参数，不能直接用函数指针）
    A("/* 阶段包装：调用表达式可以带实参 */")
    for s in stage_names:
        A("static void call_%s(void) { %s; }" % (c_ident(s), calls[s]))
    A("")
    A("struct stage_ent { const char *name; const char *series; void (*fn)(void); };")
    A("static const struct stage_ent kStages[MAX_STAGES] = {")
    for s in stage_names:
        A('\t{ "%s", "exec_%s", call_%s },'
          % (s, c_ident(s), c_ident(s)))
    A("};")
    A("")
    A("struct release_ent { int n; long long phase_ns; int idx[MAX_STAGES]; };")
    A("")

    def emit_release_table(cname, rels):
        A("static const struct release_ent %s[] = {" % cname)
        for name, members, phase_ns in rels:
            idxs = ", ".join(str(stage_names.index(m)) for m in members)
            A("\t{ %d, %dLL, { %s } },   /* %s @ %d µs */"
              % (len(members), phase_ns, idxs, name, int(phase_ns / 1000)))
        A("};")
        A("")

    emit_release_table("kPlan", plan_rel)
    emit_release_table("kFlat", flat_rel)

    if args.threads:
        A("#include <pthread.h>")
        A("#ifdef __linux__")
        A("#include <sched.h>")
        A("#endif")
        A("")
        A("/* ---------- multi：真多任务部署（--threads 才生成） ----------")
        A(" * 每个阶段一个线程，按各自相位 clock_nanosleep(ABSTIME) 释放。")
        A(" * 与 plan/flat 的可比性靠三条纪律：")
        A(" *   1) 工作量同源：同样的阶段函数、同样的调用表达式；")
        A(" *   2) 同核：进程用 taskset -c 3 启动，线程继承亲和性 ——")
        A(" *      所以隔离的是「线程化 + 调度器参与」，不是核数；")
        A(" *   3) 摘要同点：末相线程在自己的阶段跑完后做 DIGEST_MIX，")
        A(" *      与 plan/flat 的「每周期结束」是同一逻辑位置，值序一致。")
        A(" * 优先级按相位递减（早相 = 高优先），控制任务最常见的配置。 */")
        A("static long g_mcycles;")
        A("static long long g_mmiss;")
        A("static struct timespec g_mt0;")
        A("static struct plc_probe_series g_thr_start[MAX_STAGES];")
        A("static struct plc_probe_series g_thr_exec[MAX_STAGES];")
        A("static struct plc_probe_series g_thr_resp;")
        A("static const char *kThrStart[MAX_STAGES] = {")
        for s in stage_names:
            A('\t"start_%s",' % c_ident(s))
        A("};")
        A("")
        A("static void *thr_body(void *arg)")
        A("{")
        A("\tint idx = (int)(intptr_t)arg;")
        A("\tlong k;")
        A("#ifdef __linux__")
        A("\tstruct sched_param sp;")
        A("\tmemset(&sp, 0, sizeof(sp));")
        A("\tsp.sched_priority = 90 - idx;\t/* 早相高优先 */")
        A("\tsched_setscheduler(0, SCHED_FIFO, &sp);")
        A("#endif")
        A("\tfor (k = 0; k < g_mcycles; k++) {")
        A("\t\tstruct timespec due = g_mt0;")
        A("\t\tlong long r, s, prev, f, r_first;")
        A("\t\tbump_ns(&due, (long long)(k + 1) * PERIOD_NS"
          " + (long long)idx * STEP_NS);")
        A("\t\tr = (long long)due.tv_sec * 1000000000LL + due.tv_nsec;")
        A("\t\tif (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &due, NULL) != 0)")
        A("\t\t\treturn (void *)1;")
        A("\t\ts = now_ns();")
        A("\t\tplc_probe_add(&g_thr_start[idx], s - r);")
        A("\t\tprev = s;")
        A("\t\tkStages[idx].fn();")
        A("\t\tf = now_ns();")
        A("\t\tplc_probe_add(&g_thr_exec[idx], f - prev);")
        A("\t\tif (idx == MAX_STAGES - 1) {")
        A("\t\t\tr_first = r - (long long)idx * STEP_NS;")
        A("\t\t\tplc_probe_add(&g_thr_resp, f - r_first);")
        A("\t\t\tif (f - r_first > DEADLINE_NS)")
        A("\t\t\t\tg_mmiss++;")
        if args.digest_var:
            for v in args.digest_var:
                A("\t\t\tDIGEST_MIX(%s);" % v)
        A("\t\t}")
        A("\t}")
        A("\treturn NULL;")
        A("}")
        A("")
        A("static int run_multi(long cycles)")
        A("{")
        A("\tpthread_t tid[MAX_STAGES];")
        A("\tint i;")
        A("\tg_mcycles = cycles;")
        A("\tfor (i = 0; i < MAX_STAGES; i++) {")
        A("\t\tplc_probe_reset(&g_thr_start[i], kThrStart[i]);")
        A("\t\tplc_probe_reset(&g_thr_exec[i], kStages[i].series);")
        A("\t}")
        A("\tplc_probe_reset(&g_thr_resp, \"response\");")
        A("\tif (clock_gettime(CLOCK_MONOTONIC, &g_mt0) != 0)")
        A("\t\treturn 1;")
        A("\tbump_ns(&g_mt0, PERIOD_NS);")
        A("\tfor (i = 0; i < MAX_STAGES; i++)")
        A("\t\tif (pthread_create(&tid[i], NULL, thr_body,"
          " (void *)(intptr_t)i) != 0)")
        A("\t\t\treturn 1;")
        A("\tfor (i = 0; i < MAX_STAGES; i++) {")
        A("\t\tvoid *ret;")
        A("\t\tpthread_join(tid[i], &ret);")
        A("\t\tif (ret != NULL)")
        A("\t\t\treturn 1;")
        A("\t}")
        A("\tprintf(\"\\n# mode: multi  threads=%d  cycles=%ld\\n\","
          " MAX_STAGES, cycles);")
        A("\tplc_probe_head(PERIOD_NS, DEADLINE_NS, (unsigned long long)cycles);")
        A("\tfor (i = 0; i < MAX_STAGES; i++) {")
        A("\t\tplc_probe_line(&g_thr_start[i]);")
        A("\t\tplc_probe_line(&g_thr_exec[i]);")
        A("\t}")
        A("\tplc_probe_line(&g_thr_resp);")
        A("\tprintf(\"# deadline_miss: %lld/%ld (multi, 末相口径)\\n\","
          " g_mmiss, cycles);")
        if args.digest_var:
            A("\tprintf(\"# digest: %08x\\n\", g_digest);")
        A("\treturn 0;")
        A("}")
        A("")

    A("static int run_once(const char *mode, long cycles)")
    A("{")
    A("\tconst struct release_ent *rel;")
    A("\tint nrel, i, k, j;")
    A("\tstruct timespec next;")
    A("\tlong long misses = 0, overruns = 0;")
    A("")
    A("\t/* 同一进程连跑 plan/flat 时必须回到编译期初值，否则 digest 不可比 */")
    A("\tloop_reset();")
    if args.digest_var:
        A("\tg_digest = 2166136261u;")
    A("")
    A('\tif (strcmp(mode, "flat") == 0) {')
    A("\t\trel = kFlat;")
    A("\t\tnrel = (int)(sizeof(kFlat) / sizeof(kFlat[0]));")
    A("\t} else {")
    A("\t\trel = kPlan;")
    A("\t\tnrel = (int)(sizeof(kPlan) / sizeof(kPlan[0]));")
    A("\t}")
    A("")
    A("\tplc_probe_reset(&g_start, \"start_lat\");")
    A("\tplc_probe_reset(&g_work, \"exec\");")
    A("\tplc_probe_reset(&g_span, \"span\");")
    A("\tplc_probe_reset(&g_resp, \"response\");")
    A("\tfor (i = 0; i < MAX_STAGES; i++) {")
    A("\t\tif (i < %d) {" % n_stage)
    A("\t\t\tplc_probe_reset(&g_exec_stage[i], kStages[i].series);")
    A("\t\t\tplc_probe_reset(&g_instant[i], kStages[i].name);")
    A("\t\t} else {")
    A("\t\t\tplc_probe_reset(&g_exec_stage[i], \"unused\");")
    A("\t\t\tplc_probe_reset(&g_instant[i], \"unused\");")
    A("\t\t}")
    A("\t}")
    A("")
    A("\tif (clock_gettime(CLOCK_MONOTONIC, &next) != 0)")
    A("\t\treturn 1;")
    A("")
    A("\tfor (k = 0; k < cycles; k++) {")
    A("\t\tlong long r_first = 0, s_first = 0, f_last = 0, work = 0;")
    A("")
    A("\t\tbump_ns(&next, PERIOD_NS);")
    A("\t\tfor (j = 0; j < nrel; j++) {")
    A("\t\t\tstruct timespec due = next;")
    A("\t\t\tlong long r, s, prev;")
    A("\t\t\tint q;")
    A("")
    A("\t\t\t/* 第 j 次释放：用方案里的绝对相位，不是组序号×step */")
    A("\t\t\tbump_ns(&due, rel[j].phase_ns);")
    A("\t\t\tr = (long long)due.tv_sec * 1000000000LL + due.tv_nsec;")
    A("\t\t\tif (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &due, NULL) != 0)")
    A("\t\t\t\treturn 1;")
    A("\t\t\ts = now_ns();")
    A("\t\t\tplc_probe_add(&g_start, s - r);")
    A("\t\t\tif (j == 0) { r_first = r; s_first = s; }")
    A("")
    A("\t\t\tprev = s;")
    A("\t\t\tfor (q = 0; q < rel[j].n; q++) {")
    A("\t\t\t\tint idx = rel[j].idx[q];")
    A("\t\t\t\tlong long f;")
    A("\t\t\t\tkStages[idx].fn();")
    A("\t\t\t\tf = now_ns();")
    A("\t\t\t\tplc_probe_add(&g_exec_stage[idx], f - prev);")
    A("\t\t\t\tplc_probe_add(&g_instant[idx], f - r_first);")
    A("\t\t\t\twork += f - prev;")
    A("\t\t\t\tprev = f;")
    A("\t\t\t}")
    A("\t\t\tf_last = prev;")
    A("\t\t}")
    A("")
    A("\t\tplc_probe_add(&g_work, work);\t\t/* 真实工作量 */")
    A("\t\tplc_probe_add(&g_span, f_last - s_first);\t/* 含空等 */")
    A("\t\tplc_probe_add(&g_resp, f_last - r_first);")
    if args.digest_var:
        for v in args.digest_var:
            A("\t\tDIGEST_MIX(%s);" % v)
    A("\t\tif (f_last - r_first > DEADLINE_NS)")
    A("\t\t\tmisses++;")
    A("\t\tif (work > PERIOD_NS)")
    A("\t\t\toverruns++;")
    A("\t}")
    A("")
    A("\tprintf(\"\\n# mode: %s  releases_per_period=%d  cycles=%ld\\n\",")
    A("\t       mode, nrel, cycles);")
    A("\tplc_probe_head(PERIOD_NS, DEADLINE_NS, (unsigned long long)cycles);")
    A("\tplc_probe_line(&g_start);")
    A("\tplc_probe_line(&g_work);")
    A("\tplc_probe_line(&g_span);")
    A("\tplc_probe_line(&g_resp);")
    A("\tprintf(\"# deadline_miss: %lld/%ld (%s)\\n\", misses, cycles,")
    A("\t       misses ? \"FAIL\" : \"none\");")
    A("\tprintf(\"# exec_overrun: %lld/%ld\\n\", overruns, cycles);")
    if args.digest_var:
        A("\tprintf(\"# digest: %08x\\n\", g_digest);")
    A("\tfor (i = 0; i < %d; i++) {" % n_stage)
    A("\t\tplc_probe_line(&g_exec_stage[i]);")
    A('\t\tprintf("# instant_%s_us: min=%.3f avg=%.3f p50=%lu max=%.3f\\n",')
    A("\t\t       kStages[i].name,")
    A("\t\t       g_instant[i].min_ns / 1000.0,")
    A("\t\t       (g_instant[i].n ? (g_instant[i].sum_ns / 1000.0) / g_instant[i].n : 0),")
    A("\t\t       plc_probe_pctl(&g_instant[i], 500000),")
    A("\t\t       g_instant[i].max_ns / 1000.0);")
    A("\t}")
    A("\tplc_probe_hist(&g_start);")
    A("\tplc_probe_hist(&g_resp);")
    A("\tg_last_misses = misses;")
    A("\treturn 0;")
    A("}")
    A("")

    A("#ifdef __NuttX__")
    A('#include "ecos_shm_cmd.h"')
    A("#include <unistd.h>")
    A("extern void up_invalidate_dcache(uintptr_t start, uintptr_t end);")
    A("extern void up_clean_dcache(uintptr_t start, uintptr_t end);")
    A("")
    A("static int shmwait(void)")
    A("{")
    A("\tvolatile uint32_t *shm = (volatile uint32_t *)(ECOS_SHM_BASE + ECOS_SHM_OFF);")
    A("\tuintptr_t base = (uintptr_t)shm;")
    A("\tuintptr_t end = base + ECOS_SHM_WORDS * 4;")
    A("\tprintf(\"# shmwait: polling IVSHMEM @ 0x%08x\\n\",")
    A("\t       (unsigned)(ECOS_SHM_BASE + ECOS_SHM_OFF));")
    A("\tfor (;;) {")
    A("\t\tuint32_t mode_id, cycles;")
    A("\t\tconst char *mode;")
    A("\t\tint rc;")
    A("")
    A("\t\tup_invalidate_dcache(base, end);")
    A("\t\tif (shm[ECOS_W_MAGIC] != ECOS_SHM_MAGIC ||")
    A("\t\t    shm[ECOS_W_CMD] != ECOS_CMD_RUN) {")
    A("\t\t\tusleep(1000);")
    A("\t\t\tcontinue;")
    A("\t\t}")
    A("\t\tmode_id = shm[ECOS_W_MODE];")
    A("\t\tcycles = shm[ECOS_W_CYCLES];")
    A("\t\tshm[ECOS_W_CMD] = ECOS_CMD_IDLE;")
    A("\t\tshm[ECOS_W_STATUS] = ECOS_ST_RUNNING;")
    A("\t\tup_clean_dcache(base, end);")
    A("\t\tmode = (mode_id == ECOS_MODE_FLAT) ? \"flat\" : \"plan\";")
    A("\t\tif (cycles == 0 || cycles > 3600000u)")
    A("\t\t\tcycles = 1000;")
    A("\t\trc = run_once(mode, (long)cycles);")
    A("\t\tshm[ECOS_W_DIGEST] = 0;")
    if args.digest_var:
        A("\t\tshm[ECOS_W_DIGEST] = g_digest;")
    A("\t\tshm[ECOS_W_RESP_P50] = (uint32_t)plc_probe_pctl(&g_resp, 500000);")
    A("\t\tshm[ECOS_W_RESP_MAX] = (uint32_t)(g_resp.max_ns / 1000ull);")
    A("\t\tshm[ECOS_W_START_P50] = (uint32_t)plc_probe_pctl(&g_start, 500000);")
    A("\t\tshm[ECOS_W_MISS] = (uint32_t)g_last_misses;")
    A("\t\tshm[ECOS_W_EXEC_P50] = (uint32_t)plc_probe_pctl(&g_work, 500000);")
    A("\t\tshm[ECOS_W_NREL] = (mode_id == ECOS_MODE_FLAT) ? MAX_STAGES : (int)(sizeof(kPlan)/sizeof(kPlan[0]));")
    A("\t\tshm[ECOS_W_STATUS] = rc ? ECOS_ST_ERROR : ECOS_ST_DONE;")
    A("\t\tup_clean_dcache(base, end);")
    A("\t}")
    A("\treturn 0;")
    A("}")
    A("#endif")
    A("")
    A("int %s(int argc, char **argv)" % args.entry)
    A("{")
    A('\tconst char *mode = argc > 1 ? argv[1] : "plan";')
    A("\tlong cycles = argc > 2 ? atol(argv[2]) : %dL;" % args.cycles)
    A('\tif (strcmp(mode, "shmwait") == 0) {')
    A("#ifdef __NuttX__")
    A("\t\treturn shmwait();")
    A("#else")
    A("\t\tfprintf(stderr, \"shmwait 只在 NuttX inmate 上有意义\\n\");")
    A("\t\treturn 2;")
    A("#endif")
    A("\t}")
    if args.threads:
        A('\tif (strcmp(mode, "multi") == 0)')
        A("\t\treturn run_multi(cycles);")
    A("\treturn run_once(mode, cycles);")
    A("}")

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("写出 %s（plan %d 次释放/周期，flat %d 次/周期，%d 阶段）"
          % (args.out, n_rel, n_stage, n_stage))
    return 0


if __name__ == "__main__":
    sys.exit(main())
