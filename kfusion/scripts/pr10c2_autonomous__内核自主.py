#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr10c2_autonomous__内核自主.py — PR-10C 第二步：去掉每拍 ioctl 的对照组。

第一步（空 ioctl 地板价）证明：部分内核化的往返里 ~95% 是握手。但那还不能回答
「内核执行本身到底行不行」——因为每拍都要用户态来敲一次门。

本实验把**同一份计算**改成内核自主周期执行：hrtimer 到点直接跑周期体，
用户态只在开头 insmod、结尾读一次统计，**每拍没有任何跨界**。
于是 r/s/f 三个量测的就是内核自己：释放抖动(r)、启动延迟(s)、执行完成(f)。

与 mixed（UKU，每拍 ioctl）对照时只比**同类量**：
  · 执行时间    ：mixed 的 kernel_compute  vs  自主的 exec
  · 尾部风险    ：mixed 的 ioctl 往返 p99/max  vs  自主的 start_lat/exec p99/max
**不比较**端到端 response——自主部署里用户态不参与每拍，两者语义不同。

诚实边界：
  · 这是「同一份计算的另一种部署」，不是同一份二进制：源码去掉了用户态钉扎
    （否则阶段进不了内核），差异记录在报告里。
  · 内核自主模块由方案生成（hrtimer 宿主），不是手写；周期/优先级走模块参数。
  · 单机实测，不是 WCET；读 sysfs 前先 frozen=1 保证同拍快照。

用法：
  python3 kfusion/scripts/pr10c2_autonomous__内核自主.py --period-us 1000 --seconds 5
"""
from __future__ import print_function

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)

SRC = os.path.join(KF, "examples", "ecos", "mixed_pid_stateful__积分混合.c")
ECOS_FROM_C = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
OUT_ROOT = os.path.join(KF, "docs", "pr10c_boundary")

_RSF = re.compile(
    r"^\s+(\w+)\s+n=(\d+)\s+over=(\d+)\s+min=(-?\d+)ns\s+p50=(\d+)us\s+"
    r"p99=(\d+)us\s+avg=(-?\d+)ns\s+max=(-?\d+)ns", re.M)


def run(cmd, env=None):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=env)
    out = p.communicate()[0]
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return p.returncode, out


def read_sys(mod, name):
    p = "/sys/module/%s/parameters/%s" % (mod, name)
    try:
        with io.open(p, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def write_sys(mod, name, val):
    p = "/sys/module/%s/parameters/%s" % (mod, name)
    try:
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(str(val))
        return True
    except Exception:
        return False


def make_variant(out_dir):
    """去掉用户态钉扎，让三个阶段都能进内核（差异必须写进报告）。"""
    os.makedirs(out_dir, exist_ok=True)
    text = io.open(SRC, encoding="utf-8").read()
    before = text
    text = re.sub(r"(?m)^\s*/\*\s*kfusion:pin\s+linux_userspace\s*\*/\s*\n", "",
                  text)
    changed = text != before
    p = os.path.join(out_dir, "src_autonomous.c")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p, changed


def parse_rsf(text):
    d = {}
    for m in _RSF.finditer(text):
        d[m.group(1)] = {
            "n": int(m.group(2)), "over": int(m.group(3)),
            "min_ns": int(m.group(4)), "p50_us": int(m.group(5)),
            "p99_us": int(m.group(6)), "avg_ns": int(m.group(7)),
            "max_ns": int(m.group(8)),
        }
    head = re.search(r"miss=(\d+) periods=(\d+)", text)
    if head:
        d["_head"] = {"miss": int(head.group(1)), "periods": int(head.group(2))}
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period-us", type=int, default=1000)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--out", default=OUT_ROOT)
    a = ap.parse_args()

    out = os.path.join(a.out, "autonomous_p%d" % a.period_us)
    os.makedirs(out, exist_ok=True)
    gen = os.path.join(out, "gen")
    src_p, pin_removed = make_variant(out)

    env = dict(os.environ)
    env.update({"OUT_DIR": gen, "CYCLES": "1"})
    rc, log = run(["bash", ECOS_FROM_C, src_p], env=env)
    if rc != 0:
        print("流水线失败：\n%s" % "\n".join(log.splitlines()[-8:]),
              file=sys.stderr)
        return 3

    # 内核域便宜 → STKP 应把阶段放进内核（自主执行才有意义）
    st = os.path.join(gen, "stages.json")
    names = [s["name"] for s in
             (json.load(io.open(st, encoding="utf-8")).get("stages") or [])] \
        if os.path.isfile(st) else []
    cost = {
        "_note": "PR-10C2：内核域便宜，逼出内核自主部署",
        "linux_userspace": {n: {"value": 50.0, "evidence": "scenario"}
                            for n in names},
        "linux_kernel": {n: {"value": 0.05, "evidence": "scenario"}
                         for n in names},
    }
    cost_p = os.path.join(out, "stage_cost.json")
    with io.open(cost_p, "w", encoding="utf-8") as f:
        json.dump(cost, f, ensure_ascii=False, indent=2)
        f.write("\n")
    env.update({"STAGE_COST": cost_p,
                "IOCTL_COST": json.dumps({"value": 30.0, "evidence": "scenario"})})
    rc, log = run(["bash", ECOS_FROM_C, src_p], env=env)
    with io.open(os.path.join(out, "pipeline.log"), "w", encoding="utf-8") as f:
        f.write(log)
    if rc != 0:
        print("流水线失败（第二遍）：\n%s" % "\n".join(log.splitlines()[-8:]),
              file=sys.stderr)
        return 3

    plan = json.load(io.open(os.path.join(gen, "plan.json"), encoding="utf-8"))
    doms = [g.get("domain") for g in (plan.get("groups") or [])]
    ko = None
    kd = os.path.join(gen, "kmod_hrtimer")
    if os.path.isdir(kd):
        for fn in sorted(os.listdir(kd)):
            if fn.endswith(".ko"):
                ko = os.path.join(kd, fn)
                break
    if not ko:
        print("没有 kmod_hrtimer/.ko（选中方案不是纯内核域？domains=%s）" % doms,
              file=sys.stderr)
        return 4

    mod = os.path.basename(ko)[:-3]
    rc, ins = run(["insmod", ko, "period_us=%d" % a.period_us])
    if rc != 0:
        print("insmod 失败：%s" % ins.strip()[:300], file=sys.stderr)
        return 4
    try:
        expect = int(a.seconds * 1e6 / a.period_us)
        time.sleep(a.seconds)
        # 同拍快照：先冻结再读
        write_sys(mod, "frozen", 1)
        rsf = read_sys(mod, "rsf_probe") or ""
        periods = read_sys(mod, "periods")
        missed = read_sys(mod, "missed")
        write_sys(mod, "frozen", 0)
    finally:
        run(["rmmod", mod])

    with io.open(os.path.join(out, "rsf.txt"), "w", encoding="utf-8") as f:
        f.write(rsf)

    doc = {
        "kind": "pr10c2_autonomous",
        "period_us": a.period_us,
        "seconds": a.seconds,
        "expected_periods": expect,
        "periods": periods, "missed": missed,
        "domains": doms,
        "source_pin_removed": pin_removed,
        "rsf": parse_rsf(rsf),
        "raw_head": rsf.splitlines()[:1],
        "boundary": ("同一份计算换成内核自主周期执行；去掉用户态钉扎是唯一源码差异。"
                     "不比较端到端 response（语义不同）。实测非 WCET。"),
    }
    with io.open(os.path.join(out, "autonomous.json"), "w",
                 encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    r = doc["rsf"]
    print("domains=%s  periods=%s missed=%s（期望 %d）"
          % (doms, periods, missed, expect), file=sys.stderr)
    for k in ("start_lat", "exec", "response"):
        if k in r:
            v = r[k]
            print("  %-10s n=%d avg=%.3fµs p99=%dµs max=%.3fµs over=%d"
                  % (k, v["n"], v["avg_ns"] / 1000.0, v["p99_us"],
                     v["max_ns"] / 1000.0, v["over"]), file=sys.stderr)
    print("结果：%s" % os.path.join(out, "autonomous.json"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
