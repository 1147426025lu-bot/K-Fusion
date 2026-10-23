#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_hist_xcheck__直方图交叉验证.py — 新 hist 输出 vs 旧 `# Histogram` 段

为什么需要：
  BM2 保留了两份 start_lat 直方图：
    ① 旧 cyclictest 兼容段（`# Histogram` 后的 `006ld 006lu` 行）
    ② 新契约段（`# hist_start_lat_us: <桶> <计数>`）
  两份都来自同一个 `g_p_start.hist[]`。若两份不一致，说明探针改造引入了偏差，
  所有基于新 hist 的 CDF 都不可信。所以每次动探针都跑一遍。

用法：
  python probe_hist_xcheck__直方图交叉验证.py <日志> [<日志> ...]

退出码：0 = 全部一致；1 = 有不一致（并打印差异桶）；2 = 找不到旧格式段
"""
import io
import re
import sys

OLD_ROW = re.compile(r"^(\d{6})\s+(\d+)\s*$")
NEW_ROW = re.compile(r"^#\s*hist_start_lat_us:\s*(\d+)\s+(\d+)\s*$")
DMESG = re.compile(r"^\s*(?:\[\s*[\d.]+\]\s*)?(?:PLCPROBE\s+)?")


def parse(path):
    lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
    old, in_hist = {}, False
    for ln in lines:
        if ln.strip() == "# Histogram":
            in_hist = True
            continue
        if in_hist:
            m = OLD_ROW.match(ln)
            if m:
                old[int(m.group(1))] = int(m.group(2))
                continue
            if ln.strip().startswith("#"):
                in_hist = False

    new = {}
    for ln in lines:
        m = NEW_ROW.match(DMESG.sub("", ln))
        if m and int(m.group(2)):
            new[int(m.group(1))] = int(m.group(2))
    return old, new


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    rc = 0
    for path in sys.argv[1:]:
        old, new = parse(path)
        old_nz = {k: v for k, v in old.items() if v}
        name = path.rsplit("/", 1)[-1]
        if not old_nz:
            print(f"⚠️  {name}: 没有旧格式 `# Histogram` 段（无法交叉验证）")
            rc = max(rc, 2)
            continue
        if not new:
            print(f"❌ {name}: 没有 `# hist_start_lat_us` 段（探针版本太旧？）")
            rc = 1
            continue
        diff = {k: (old_nz.get(k, 0), new.get(k, 0))
                for k in set(old_nz) | set(new)
                if old_nz.get(k, 0) != new.get(k, 0)}
        tot_o, tot_n = sum(old_nz.values()), sum(new.values())
        if diff or tot_o != tot_n:
            print(f"❌ {name}: 不一致（桶 {len(old_nz)} vs {len(new)}，"
                  f"总数 {tot_o} vs {tot_n}）")
            for k in sorted(diff)[:10]:
                print(f"     桶 {k}: 旧={diff[k][0]} 新={diff[k][1]}")
            rc = 1
        else:
            print(f"✅ {name}: {len(old_nz)} 个非零桶逐桶一致，总数 {tot_o}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
