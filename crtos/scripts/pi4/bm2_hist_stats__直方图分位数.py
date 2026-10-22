#!/usr/bin/env python3
"""从 bm2pid 日志直方图算分位数与阈值计数（1 µs 桶，桶顶 199 封顶）。

用法： python bm2_hist_stats__直方图分位数.py <log> [<log> ...]
注：桶 199 是"≥199 µs"的聚合桶（HIST_BUCKETS=200），所以 p99.99 及以上的桶值要按封顶读。
"""
import io
import re
import sys

THRESHOLDS = (50, 100, 150, 199)
QUANTILES = (("min", 0.0), ("p50", 0.5), ("p90", 0.9), ("p99", 0.99),
             ("p99.9", 0.999), ("p99.99", 0.9999))


def load(path):
    txt = io.open(path, encoding="utf-8", errors="replace").read()
    buckets = {}
    for ln in txt.splitlines():
        m = re.match(r"^\s*(\d{6})\s+(\d+)\s*$", ln)
        if m and int(m.group(2)):
            buckets[int(m.group(1))] = int(m.group(2))
    mx = re.search(r"Max Latencies:\s*(\d+)", txt)
    ex = re.search(r"exec_us: avg=([\d.]+) max=([\d.]+)", txt)
    n = sum(buckets.values())
    return buckets, n, (int(mx.group(1)) if mx else None), \
        ((ex.group(1), ex.group(2)) if ex else ("?", "?"))


def quant(buckets, n, q):
    if q <= 0:
        return min(buckets)
    c, t = 0, n * q
    for k in sorted(buckets):
        c += buckets[k]
        if c >= t:
            return k


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for path in sys.argv[1:]:
        b, n, mx, ex = load(path)
        print("== %s" % path)
        print("   n=%d  exec avg=%s max=%s  max_header=%s µs  非零桶=%d"
              % (n, ex[0], ex[1], mx, len(b)))
        line = "   " + "  ".join("%s=%s" % (k, quant(b, n, q)) for k, q in QUANTILES)
        print(line + "  (µs，桶顶 199)")
        for th in THRESHOLDS:
            s = sum(v for k, v in b.items() if k >= th)
            print("   >=%3d µs : %7d (%.4f%%)" % (th, s, 100.0 * s / n))


if __name__ == "__main__":
    main()
