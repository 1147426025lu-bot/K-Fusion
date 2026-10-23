#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_report__统一探针报告.py — 解析「r/s/f v1」统一探针块并输出对照表

为什么需要它：
  2026-10-22 的审查指出，三套探针记录的量不同（间隔偏差 / 唤醒延迟 / 端到端响应），
  混着相除会得出不成立的倍数。BM2 已改为按统一契约输出：

      # probe: r/s/f v1
      # period_ns: 1000000
      # samples: 900000
      # stat_start_lat_us: min=.. avg=.. p50=.. p90=.. p99=.. p99.9=.. p99.99=.. max=.. over=.. n=..
      # stat_exec_us:      ...
      # stat_response_us:  ...
      # deadline_miss: 0/900000 (none)
      # exec_overrun: 0/900000

  本脚本只认这个块；老日志（只有 `# Max Latencies` + 直方图）会明确报「旧格式，无 R」，
  而不是悄悄拿一个不可比的数去凑表。

用法：
  python probe_report__统一探针报告.py <log> [<log> ...]
  python probe_report__统一探针报告.py --csv out.csv <log> ...
"""
import io
import re
import sys

SERIES = ("start_lat", "exec", "response")
FIELDS = ("min", "avg", "p50", "p90", "p99", "p99.9", "p99.99", "max", "over", "n")
PAT = {
    "probe": re.compile(r"^#\s*probe:\s*(.+?)\s*$"),
    "period": re.compile(r"^#\s*period_ns:\s*(\d+)"),
    "samples": re.compile(r"^#\s*samples:\s*(\d+)"),
    "deadline": re.compile(r"^#\s*deadline_ns:\s*(\d+)"),
    "release": re.compile(r"^#\s*release_id:\s*(.+?)\s*$"),
    "miss": re.compile(r"^#\s*deadline_miss:\s*(\d+)/(\d+)"),
    "overrun": re.compile(r"^#\s*exec_overrun:\s*(\d+)/(\d+)"),
}
SER_PAT = re.compile(r"^#\s*stat_(\w+)_us:\s*(.+)$")

# 内核侧经 printk 输出，dmesg 会加「[ 时间戳] PLCPROBE 」前缀；
# 这里统一剥掉，让同一台解析器同时吃用户态与内核态两块文本。
DMESG_PAT = re.compile(r"^\s*(?:\[\s*[\d.]+\]\s*)?(?:PLCPROBE\s+)?")


def parse(path):
    txt = io.open(path, encoding="utf-8", errors="replace").read()
    info = {"path": path, "probe": None, "period_ns": None, "samples": None,
            "deadline_ns": None, "release": None,
            "miss": None, "overrun": None, "series": {}}
    for raw in txt.splitlines():
        ln = DMESG_PAT.sub("", raw)
        if not ln.startswith("#"):
            continue
        m = PAT["probe"].match(ln)
        if m:
            info["probe"] = m.group(1)
            continue
        m = PAT["period"].match(ln)
        if m:
            info["period_ns"] = int(m.group(1))
            continue
        m = PAT["samples"].match(ln)
        if m:
            info["samples"] = int(m.group(1))
            continue
        m = PAT["deadline"].match(ln)
        if m:
            info["deadline_ns"] = int(m.group(1))
            continue
        m = PAT["release"].match(ln)
        if m:
            info["release"] = m.group(1)
            continue
        m = PAT["miss"].match(ln)
        if m:
            info["miss"] = (int(m.group(1)), int(m.group(2)))
            continue
        m = PAT["overrun"].match(ln)
        if m:
            info["overrun"] = (int(m.group(1)), int(m.group(2)))
            continue
        m = SER_PAT.match(ln)
        if m and m.group(1) in SERIES:
            kv = {}
            for part in m.group(2).split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    kv[k] = v
            info["series"][m.group(1)] = kv
    return info


def fmt(info):
    out = []
    name = info["path"].rsplit("/", 1)[-1]
    if not info["probe"]:
        out.append(f"== {name}")
        out.append("   ❌ 旧格式：没有 `# probe:` 块 → 只有 start_lat（且未必是同一指标），"
                   "**没有端到端响应 R，不能进 J(θ)**")
        return out
    dl = info.get("deadline_ns")
    out.append(f"== {name}   probe={info['probe']}  period={info['period_ns']}ns  "
               f"deadline={dl if dl else '?'}ns  samples={info['samples']}")
    if info.get("release"):
        out.append(f"   release_id: {info['release']}")
    out.append("   %-11s %9s %9s %6s %6s %6s %7s %8s %9s %6s %5s"
               % ("series", "min", "avg", "p50", "p90", "p99", "p99.9",
                  "p99.99", "max", "over", "neg"))
    for s in SERIES:
        kv = info["series"].get(s)
        if not kv:
            continue
        out.append("   %-11s %9s %9s %6s %6s %6s %7s %8s %9s %6s %5s"
                   % (s, kv.get("min", "?"), kv.get("avg", "?"), kv.get("p50", "?"),
                      kv.get("p90", "?"), kv.get("p99", "?"), kv.get("p99.9", "?"),
                      kv.get("p99.99", "?"), kv.get("max", "?"),
                      kv.get("over", "?"), kv.get("neg", "-")))
        if kv.get("neg") and kv["neg"] not in ("0", "?", "-"):
            out.append(f"      ⚠️ {s} 有 {kv['neg']} 个负值样本"
                       f"（min_signed={kv.get('min_signed', '?')} µs）"
                       "——已计入直方图绝对值，别当成『提前完成』抹掉")
    if info["miss"]:
        m, n = info["miss"]
        pct = 100.0 * m / n if n else 0.0
        out.append(f"   deadline_miss : {m}/{n} ({pct:.6f}%)"
                   + ("  ⚠️ 有违约" if m else "  ✅ 无违约"))
    if info["overrun"]:
        m, n = info["overrun"]
        out.append(f"   exec_overrun  : {m}/{n}（仅表示单周期算得久，**不等于** deadline miss）")
    if "over" in (info["series"].get("response") or {}) and \
            info["series"]["response"].get("over") not in (None, "0"):
        out.append("   ⚠️ response 有样本触到桶顶 199 µs → p99.99 是**下界**")
    return out


def main():
    args = sys.argv[1:]
    csv = None
    if args and args[0] == "--csv":
        csv = args[1]
        args = args[2:]
    if not args:
        raise SystemExit(__doc__)
    infos = [parse(p) for p in args]
    for info in infos:
        print("\n".join(fmt(info)) + "\n")
    if csv:
        rows = ["file,series,min,avg,p50,p90,p99,p99.9,p99.99,max,over,n"]
        for info in infos:
            if not info["probe"]:
                rows.append(f"{info['path']},LEGACY,,,,,,,,,,")
                continue
            for s in SERIES:
                kv = info["series"].get(s, {})
                rows.append(",".join([info["path"], s] +
                                     [kv.get(f, "") for f in FIELDS]))
        io.open(csv, "w", encoding="utf-8", newline="\n").write("\n".join(rows) + "\n")
        print(f"-> {csv}")


if __name__ == "__main__":
    main()
