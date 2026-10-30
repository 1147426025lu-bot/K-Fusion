#!/usr/bin/env python3
"""soak_ecos__长测.py — 对生成的 PLC 驱动做定长浸泡，产出可出图的原始日志。

为什么要有它（而不是直接跑 driver）：
  1) 段长要按**拍数**算，不是按秒——1 kHz 下 15min=90 万拍、1h=360 万、8h=2880 万，
     必须由脚本算好再传给驱动，否则「跑 15 分钟」这种说法在不同周期下含义不同；
  2) 每段都要绑定平台身份（内核/抢占/隔离档位/调度类/亲和），否则跨段比较会被
     审稿问「你怎么知道两台机器一样」；
  3) 原始输出（含 rtlat 全样本直方图与抽稀时间线）必须原样留档，统计量只能是派生量。

输出：
  <out_dir>/run.log        驱动原始输出（stdout+stderr 全留）
  <out_dir>/soak.json      段元信息 + 解析出的统计 + 隔离取证快照
  <out_dir>/cmd.txt        实际执行的命令行（可原样复现）

用法：
  python3 soak_ecos__长测.py --driver /tmp/x/driver --minutes 15 \
      --sched fifo99 --label l4 --out-dir /tmp/soak_15m
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def read(p):
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return None


def load_iso_evidence():
    """复用隔离取证脚本（同目录），避免两套口径。"""
    import importlib.util
    p = os.path.join(HERE, "iso_evidence__隔离取证.py")
    if not os.path.isfile(p):
        return None
    spec = importlib.util.spec_from_file_location("iso_evidence", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pctl(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def parse_stat_block(text, name):
    """探针 `# stat_<name>_us:` 一行里同时有 min/avg/p50/p99/max/over/neg。"""
    m = re.search(r"# stat_%s_us: (.*)" % re.escape(name), text)
    if not m:
        return None
    out = {}
    for tok in m.group(1).split():
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        try:
            out[k] = float(v) if "." in v else int(v)
        except ValueError:
            out[k] = v
    return out


def parse_rtlat(text):
    """细直方图 + 抽稀样本（由驱动 trace 模式产出）。"""
    out = {"hist": None, "samples": 0, "stats": None, "stride": None}
    mh = re.search(r"rtlat HIST bin_ns=(-?\d+) lo_ns=(-?\d+) nbins=(\d+)", text)
    md = re.search(r"rtlat HISTDATA([^\n]*)", text)
    if mh and md:
        bins = {}
        total = 0
        for tok in md.group(1).split():
            if ":" not in tok:
                continue
            b, c = tok.split(":", 1)
            try:
                bins[int(b)] = int(c)
                total += int(c)
            except ValueError:
                pass
        out["hist"] = {
            "bin_ns": int(mh.group(1)), "lo_ns": int(mh.group(2)),
            "nbins": int(mh.group(3)), "bins": bins, "n": total,
        }
    ms = re.search(r"rtlat STATS (.*)", text)
    if ms:
        out["stats"] = ms.group(1).strip()
    mt = re.search(r"rtlat START .*stride=(\d+)", text)
    if mt:
        out["stride"] = int(mt.group(1))
    out["samples"] = len(re.findall(r"rtlat SAMPLE \d+ -?\d+", text))
    return out


def hist_pctl(hist, q):
    """从细直方图算分位（全样本，不是抽稀序列）。"""
    if not hist or not hist["n"]:
        return None
    target = q * hist["n"]
    acc = 0
    for b in sorted(hist["bins"]):
        acc += hist["bins"][b]
        if acc >= target:
            return hist["lo_ns"] + (b + 0.5) * hist["bin_ns"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--minutes", type=float, required=True)
    ap.add_argument("--period-us", type=float, default=1000.0)
    ap.add_argument("--sched", default="fifo99", choices=("cfs", "fifo99", "fifo80"))
    ap.add_argument("--pin", type=int, default=3)
    ap.add_argument("--mode", default="plan")
    ap.add_argument("--label", default="")
    ap.add_argument("--level", default="")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--iso-cgroup", default=None,
                    help="把测量进程放进该 cgroup 再钉核（cgroup 分区隔离时必须）；"
                         "默认自动用 /sys/fs/cgroup/kfusion_iso")
    ap.add_argument("--timeout-slack", type=float, default=0.35,
                    help="允许的超时比例（RT 核上迟到会被算进拍数，留 35%% 余量）")
    a = ap.parse_args()

    out = os.path.abspath(a.out_dir)
    os.makedirs(out, exist_ok=True)

    cycles = int(round(a.minutes * 60.0 * 1e6 / a.period_us))
    cmd = ["taskset", "-c", str(a.pin)]
    if a.sched.startswith("fifo"):
        prio = a.sched[4:] or "99"
        cmd += ["chrt", "-f", prio]
    cmd += [a.driver, a.mode, str(cycles), "trace"]

    # cgroup 分区隔离下，探针核的 CPU 已被 partition root 从父分区摘走：
    # 不在那个 cgroup 里的进程 `taskset -c 3` 会直接 EINVAL（实测）。
    # 所以测量进程必须在**分区内**启动，否则「L4 + 钉核」根本跑不起来——
    # 早先这里 rc=1/0.0s 的失败就是这个原因，不是驱动坏了。
    iso_cg = a.iso_cgroup
    if iso_cg is None:
        cand = "/sys/fs/cgroup/kfusion_iso"
        iso_cg = cand if os.path.isdir(cand) else ""
    if iso_cg and os.path.isdir(iso_cg):
        inner = " ".join(cmd)
        cmd = ["sh", "-c",
               "echo $$ > %s/cgroup.procs 2>/dev/null || true; exec %s"
               % (iso_cg, inner)]
        print("  (在 %s 内启动：CPU%d 已属该 cgroup 分区)" % (iso_cg, a.pin))

    wall_est = cycles * a.period_us / 1e6
    timeout = max(60.0, wall_est * (1.0 + a.timeout_slack) + 120.0)

    with io.open(os.path.join(out, "cmd.txt"), "w", encoding="utf-8") as f:
        f.write(" ".join(cmd) + "\n")

    t0 = time.time()
    print("▶ %s  cycles=%d (%.1f min @ %.0f µs)  est=%.0f s  timeout=%.0f s"
          % (" ".join(cmd[:6]), cycles, a.minutes, a.period_us, wall_est, timeout))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        rc = r.returncode
        text = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        rc = 124
        text = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes)
                else (e.stdout or ""))
        text += "\n# TIMEOUT after %.0f s\n" % timeout
    wall = time.time() - t0

    with io.open(os.path.join(out, "run.log"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write(text)

    rt = parse_rtlat(text)
    doc = {
        "kind": "soak_ecos",
        "version": 1,
        "label": a.label,
        "level": a.level,
        "rc": rc,
        "cmd": cmd,
        "minutes_planned": a.minutes,
        "period_us": a.period_us,
        "cycles_planned": cycles,
        "wall_s": round(wall, 3),
        "wall_ratio": round(wall / wall_est, 4) if wall_est else None,
        "sched": a.sched,
        "pin_cpu": a.pin,
        "iso_cgroup": iso_cg or None,
        "kernel": {
            "release": read("/proc/sys/kernel/osrelease"),
            "version": read("/proc/version"),
            "realtime": read("/sys/kernel/realtime"),
        },
        "boot": {
            "isolcpus": next((t for t in (read("/proc/cmdline") or "").split()
                              if t.startswith("isolcpus=")), None),
            "nohz_full": next((t for t in (read("/proc/cmdline") or "").split()
                               if t.startswith("nohz_full=")), None),
        },
        "rt_runtime_us": read("/proc/sys/kernel/sched_rt_runtime_us"),
        "stat": {
            "start_lat": parse_stat_block(text, "start_lat"),
            "exec": parse_stat_block(text, "exec"),
            "response": parse_stat_block(text, "response"),
            "span": parse_stat_block(text, "span"),
        },
        "miss": (re.search(r"# deadline_miss: (\d+)/(\d+)", text).groups()
                 if re.search(r"# deadline_miss: (\d+)/(\d+)", text) else None),
        "digest": (re.search(r"# digest: (\w+)", text).group(1)
                   if re.search(r"# digest: (\w+)", text) else None),
        "rtlat": {
            "stride": rt["stride"],
            "n_samples_kept": rt["samples"],
            "fine_hist_n": (rt["hist"] or {}).get("n"),
            "p50_us": (hist_pctl(rt["hist"], 0.5) or 0) / 1000.0 if rt["hist"] else None,
            "p99_us": (hist_pctl(rt["hist"], 0.99) or 0) / 1000.0 if rt["hist"] else None,
            "p999_us": (hist_pctl(rt["hist"], 0.999) or 0) / 1000.0 if rt["hist"] else None,
            "p9999_us": (hist_pctl(rt["hist"], 0.9999) or 0) / 1000.0 if rt["hist"] else None,
            "max_us": ((rt["hist"]["lo_ns"] + max(rt["hist"]["bins"]) * rt["hist"]["bin_ns"])
                       / 1000.0 if rt["hist"] and rt["hist"]["bins"] else None),
            "under": None,
        },
    }
    if rt["stats"]:
        m = re.search(r"under=(\d+) over=(\d+)", rt["stats"])
        if m:
            doc["rtlat"]["under_n"] = int(m.group(1))
            doc["rtlat"]["fine_over_n"] = int(m.group(2))

    ev = load_iso_evidence()
    if ev:
        doc["iso_evidence"] = ev.collect(a.pin)

    with io.open(os.path.join(out, "soak.json"), "w", encoding="utf-8",
                 newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    st = doc["stat"]["start_lat"] or {}
    rs = doc["stat"]["response"] or {}
    print("  rc=%s wall=%.1fs (est %.0f)  start_lat p50=%s p99=%s max=%s | "
          "resp p99=%s max=%s | miss=%s"
          % (rc, wall, wall_est, st.get("p50"), st.get("p99"), st.get("max"),
             rs.get("p99"), rs.get("max"), doc["miss"]))
    print("  → %s/soak.json" % out)
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    sys.exit(main())
