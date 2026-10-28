#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_exec.py —— 把 ecos_stage 的 exec_probe 实测输出转成 --exec-us JSON。

闭环用途（消除 ast_to_plan 里 exec_us 的“占位”诚实边界）：
  1) 先用默认 exec-us 生成并加载内核模块；
  2) 读取 /sys/module/<name>/parameters/exec_probe 的输出（或本脚本的 stdin/文件）；
  3) 本脚本解析每阶段的 max（ns，实时系统取最坏情况 WCET，而不是 avg），
     输出一个以 µs 为单位的 exec-us 字典；
  4) 把该 JSON 作为 --exec-us 重新喂给 auto_kernelize.sh / ast_to_plan，
     即可得到“执行时间来自实测”的真实计划。

用法：
  python3 measure_exec.py exec_probe.txt > exec_us.json
  cat /sys/module/plc_cycle/parameters/exec_probe | python3 measure_exec.py

输出示例（µs）：
  {"stage_sample": 0.241, "stage_pid": 0.167, "stage_clamp": 0.204}

说明：
  - 用 max 而非 avg：实时调度需要最坏情况边界，avg 不能证明不会超时。
  - 真要在论文里使用，应做足够长/多工况的采样，并叠加安全裕度（见 GATE2_TEIR.md）。
  - 此工具只做“实测→回填”的格式转换，不编造数据。
"""
import argparse
import json
import re
import sys


_LINE = re.compile(
    r"^\s*\[\s*(\d+)\s*\]\s*(\S+):\s*max=(\d+)\s*ns\s+avg=(\d+)\s*ns\s+n=(\d+)",
    re.IGNORECASE,
)


def parse_exec_probe(text):
    """从 exec_probe 文本解析每阶段 max/avg (ns)。返回 {name: (max_ns, avg_ns, n)}。"""
    out = {}
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        idx, name, mx, avg, n = m.groups()
        out[name] = (int(mx), int(avg), int(n))
    return out


def to_exec_us(parsed, use_max=True):
    """把解析结果转成 exec-us 字典（µs）。use_max=True 取最坏情况。"""
    d = {}
    for name, (mx, avg, n) in parsed.items():
        ns = mx if use_max else avg
        d[name] = round(ns / 1000.0, 3)  # ns -> µs
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description="exec_probe 实测 → exec-us JSON")
    ap.add_argument("input", nargs="?", help="exec_probe 文本文件；缺省读 stdin")
    ap.add_argument("--avg", action="store_true",
                    help="用 avg 而不是 max（默认用 max=WCET）")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="安全裕度系数，乘到每个值上（默认 1.0）")
    a = ap.parse_args(argv)

    if a.input:
        with open(a.input, encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    parsed = parse_exec_probe(text)
    if not parsed:
        sys.stderr.write("measure_exec: 未解析到任何阶段实测行；"
                         "请确认输入来自 exec_probe 输出。\n")
        return 2

    exec_us = to_exec_us(parsed, use_max=not a.avg)
    if a.scale != 1.0:
        exec_us = {k: round(v * a.scale, 3) for k, v in exec_us.items()}

    json.dump(exec_us, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    n_total = sum(v[2] for v in parsed.values())
    sys.stderr.write("measure_exec: 解析到 %d 个阶段、共 %d 采样；"
                     "%s 值（µs）：%s\n"
                     % (len(parsed), n_total,
                        "max(WCET)" if not a.avg else "avg", exec_us))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
