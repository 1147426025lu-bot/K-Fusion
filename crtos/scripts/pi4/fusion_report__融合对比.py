#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fusion_report__融合对比.py — 对比「按方案分组释放」与「每阶段一次释放」

为什么必须做分解
----------------
直接把两种模式的 response 相除会得到一个很吓人的倍数（≈27×），但其中大部分是
**相位等待**——那是我们人为把第 j 个阶段排到 r+j·STEP 造成的，换个 STEP 就变。
真正由"释放次数"决定、换布局也不变的量是：

    每周期唤醒税 = 释放次数 × 单次唤醒延迟

所以本脚本把 response 拆成三项，只有第二项可以归因于融合：

    response(flat) ≈ (N−1)·STEP + 单次唤醒 + 工作量

用法：
    python fusion_report__融合对比.py run_plan.log run_flat.log [--json out.json]

输入是统一契约（`# probe:` 块）的日志；解析直接复用 probe_report，避免两套口径。
"""
import argparse
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_probe_report():
    """复用 probe_report 的解析器，保证与主流程同一口径。"""
    for cand in ("probe_report__统一探针报告.py",):
        p = os.path.join(HERE, "probe_report__统一探针报告.py")
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("probe_report", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("找不到 probe_report__统一探针报告.py（同目录）")


def num(info, series, key, default=None):
    kv = (info.get("series") or {}).get(series) or {}
    v = kv.get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def mode_of(info):
    """从日志里取 `# mode:` 行（driver 自己打的）。"""
    return info.get("mode") or "?"


def collect(path, pr):
    info = pr.parse(path)
    # probe_report 只保留 start_lat/exec/response 三条（主口径，别改它）。
    # 这里用**同一个 SER_PAT** 补读 span 与 exec_<stage>，保证解析口径一致。
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            ln = pr.DMESG_PAT.sub("", raw)
            m = pr.SER_PAT.match(ln)
            if not m or m.group(1) in pr.SERIES:
                continue
            kv = {}
            for part in m.group(2).split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    kv[k] = v
            info["series"][m.group(1)] = kv
    # driver 额外打的 `# mode: plan  releases_per_period=1  cycles=60000`
    mode, nrel = "?", None
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if ln.startswith("# mode:"):
                parts = ln.split()
                mode = parts[2] if len(parts) > 2 else "?"
                for t in parts:
                    if t.startswith("releases_per_period="):
                        nrel = int(t.split("=", 1)[1])
                break
    return {
        "file": os.path.basename(path), "mode": mode, "releases": nrel,
        "samples": info.get("samples"),
        "period_ns": info.get("period_ns"),
        "start_p50": num(info, "start_lat", "p50"),
        "start_avg": num(info, "start_lat", "avg"),
        "start_p99": num(info, "start_lat", "p99"),
        "start_max": num(info, "start_lat", "max"),
        "start_n": num(info, "start_lat", "n"),
        "exec_avg": num(info, "exec", "avg"),
        "span_avg": num(info, "span", "avg"),
        "resp_avg": num(info, "response", "avg"),
        "resp_p50": num(info, "response", "p50"),
        "resp_p99": num(info, "response", "p99"),
        "resp_max": num(info, "response", "max"),
        "miss": info.get("miss"),
    }, info


def main():
    ap = argparse.ArgumentParser(description="融合方案 vs 每阶段一次释放")
    ap.add_argument("plan_log")
    ap.add_argument("flat_log")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    pr = load_probe_report()
    plan, pinfo = collect(args.plan_log, pr)
    flat, finfo = collect(args.flat_log, pr)

    # 每周期唤醒税：释放次数 × 单次唤醒均值
    def tax(d):
        return (d["releases"] or 0) * (d["start_avg"] or 0.0)

    t_plan, t_flat = tax(plan), tax(flat)
    saved = t_flat - t_plan
    period_us = (plan["period_ns"] or 0) / 1000.0

    # flat 的 response 分解
    step_us = None
    if flat["releases"] and flat["releases"] > 1 and flat["span_avg"] is not None:
        step_us = flat["span_avg"] / (flat["releases"] - 1)
    decomp = None
    if step_us is not None:
        phase = (flat["releases"] - 1) * step_us
        decomp = {
            "phase_wait_us": round(phase, 2),
            "wakeup_us": round(flat["start_avg"] or 0, 2),
            "work_us": round(flat["exec_avg"] or 0, 2),
            "sum_us": round(phase + (flat["start_avg"] or 0) + (flat["exec_avg"] or 0), 2),
            "measured_us": round(flat["resp_avg"] or 0, 2),
        }

    # 每个阶段执行时间（证明工作量没被改变）
    stages = {}
    for k, v in (pinfo.get("series") or {}).items():
        if k.startswith("exec_"):
            stages.setdefault(k[5:], {})["plan"] = float(v.get("avg", 0))
    for k, v in (finfo.get("series") or {}).items():
        if k.startswith("exec_"):
            stages.setdefault(k[5:], {})["flat"] = float(v.get("avg", 0))

    print("== 释放结构对比 ==")
    print("  %-22s %14s %14s" % ("", "plan（按方案）", "flat（每阶段）"))
    rows = [
        ("每周期释放次数", plan["releases"], flat["releases"], "%d"),
        ("start_lat p50 (µs)", plan["start_p50"], flat["start_p50"], "%.0f"),
        ("start_lat p99 (µs)", plan["start_p99"], flat["start_p99"], "%.0f"),
        ("单次唤醒 avg (µs)", plan["start_avg"], flat["start_avg"], "%.2f"),
        ("**每周期唤醒税 (µs)**", t_plan, t_flat, "%.2f"),
        ("工作量 exec avg (µs)", plan["exec_avg"], flat["exec_avg"], "%.2f"),
        ("跨度 span avg (µs)", plan["span_avg"], flat["span_avg"], "%.2f"),
        ("**响应 R avg (µs)**", plan["resp_avg"], flat["resp_avg"], "%.2f"),
        ("R p99 (µs)", plan["resp_p99"], flat["resp_p99"], "%.0f"),
        ("R max (µs)", plan["resp_max"], flat["resp_max"], "%.1f"),
    ]
    for name, a, b, f in rows:
        fa = (f % a) if isinstance(a, (int, float)) else str(a)
        fb = (f % b) if isinstance(b, (int, float)) else str(b)
        print("  %-22s %14s %14s" % (name, fa, fb))

    print("== 归因 ==")
    print("  释放次数 %d → %d，每周期少唤醒 %d 次"
          % (flat["releases"] or 0, plan["releases"] or 0,
             (flat["releases"] or 0) - (plan["releases"] or 0)))
    print("  **确定性收益：唤醒税 %.2f → %.2f µs（省 %.2f µs/周期，占 %.1f%% 周期）**"
          % (t_flat, t_plan, saved, 100.0 * saved / period_us if period_us else 0))
    if decomp:
        print("  R 的差不能整笔记在融合头上——flat 的 R 里有相位等待：")
        print("    相位等待 %.1f + 单次唤醒 %.1f + 工作量 %.1f = %.1f（实测 %.1f）"
              % (decomp["phase_wait_us"], decomp["wakeup_us"], decomp["work_us"],
                 decomp["sum_us"], decomp["measured_us"]))
        print("    相位等待取决于 STEP（本次 %.0f µs）的排布，换布局就变；"
              "唤醒税不随布局变。" % step_us)

    if stages:
        print("== 单阶段执行时间（检查工作量是否被改变）==")
        print("  %-16s %9s %9s %8s" % ("stage", "plan", "flat", "差"))
        for s, kv in sorted(stages.items()):
            a, b = kv.get("plan"), kv.get("flat")
            d = (b - a) if (a is not None and b is not None) else None
            print("  %-16s %9.3f %9.3f %8s"
                  % (s, a if a is not None else float("nan"),
                     b if b is not None else float("nan"),
                     ("%+.3f" % d) if d is not None else "-"))

    for d, nm in ((plan, "plan"), (flat, "flat")):
        if d["miss"]:
            m, n = d["miss"]
            print("  %s deadline_miss: %d/%d" % (nm, m, n))

    if args.json:
        out = {"plan": plan, "flat": flat,
               "wakeup_tax_us": {"plan": round(t_plan, 3), "flat": round(t_flat, 3),
                                 "saved": round(saved, 3)},
               "decomposition_flat": decomp,
               "stage_exec_us": {k: {kk: round(vv, 4) for kk, vv in v.items()}
                                 for k, v in stages.items()}}
        with io.open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
