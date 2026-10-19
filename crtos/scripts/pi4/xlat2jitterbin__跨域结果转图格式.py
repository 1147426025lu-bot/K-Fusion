#!/usr/bin/env python3
# xlat2jitterbin__跨域结果转图格式.py
#
# 把 root 侧 xlat_collect 产出的 XLATREC1 二进制转成出图脚本能吃的
# jitter.bin v2 格式（两份）：
#   1) <out>.jitter.bin  —— inmate 内核内工作抖动（rec.jitter_ns）
#   2) <out>.latency.bin —— 跨域回传延迟（lat_raw − min(lat_raw)，ns）
#
# v2 头（56 字节，小端）：magic u32 | version u32 | cycles u64 |
#                         min_ns s64 | max_ns s64 | samples u32 |
#                         hist_bins u32 | hist_lo s64 | hist_step s64
#
# 用法:
#   python3 xlat2jitterbin__跨域结果转图格式.py <rec.bin> <out_prefix>

import struct
import sys
from pathlib import Path

import numpy as np

JITTER_MAGIC = 0x504C434A
VERSION = 2
HDR = "<IIQqqIIqq"
REC_DTYPE = np.dtype([("lat", "<i8"), ("jit", "<i4"), ("seq", "<u4")])
REC_SIZE = 16
FILE_HDR = 32           # "XLATREC1" + version + rec_size + n + interval


def write_v2(path: Path, vals: np.ndarray, cycles: int) -> None:
    """vals: int64 小端数组。8h 档 2700 万样本用 numpy 直写，避免逐条 struct 的开销。"""
    n = int(vals.size)
    if n == 0:
        raise SystemExit(f"no samples for {path}")
    v = vals.astype("<i8", copy=False)
    mn, mx = int(v.min()), int(v.max())
    with path.open("wb") as f:
        f.write(struct.pack(HDR, JITTER_MAGIC, VERSION, cycles, mn, mx,
                            n, 0, 0, 0))
        v.tofile(f)


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    src = Path(sys.argv[1])
    prefix = sys.argv[2]
    seg_sec = 60
    for i, a in enumerate(sys.argv[3:], start=3):
        if a == "--seg" and i + 1 < len(sys.argv):
            seg_sec = int(sys.argv[i + 1])

    data = src.read_bytes()
    if len(data) < FILE_HDR or data[:8] != b"XLATREC1":
        raise SystemExit(f"{src}: 不是 XLATREC1 格式")
    ver, rec_size, n, interval_us = struct.unpack("<IIQQ", data[8:32])
    if rec_size != REC_SIZE:
        raise SystemExit(f"rec_size={rec_size} != {REC_SIZE}")
    body = data[FILE_HDR:FILE_HDR + n * REC_SIZE]
    if len(body) < n * REC_SIZE:
        raise SystemExit(f"数据截断：期望 {n} 条，实得 {len(body) // REC_SIZE} 条")

    arr = np.frombuffer(body, dtype=REC_DTYPE)
    jitter = arr["jit"].astype(np.int64)
    lat_raw = arr["lat"].astype(np.int64)

    # 校准分两层：
    # 1) 先在「跳变点」处切大段——相邻样本差 >1ms 视为时钟跳变（实测 8h 有一次 50ms）
    # 2) 大段内按 sub_sec 子段取 min
    #
    # 为什么不能用全局 min：8h 实测出现过一次约 50ms 的时钟跳变
    # （NuttX tickless 时间补偿疑点），全局 min 会被这一次事件拉低，
    # 导致其余 99.99% 的样本被整体抬高 50ms —— 分位数完全失真。
    # 若只用固定 60s 段，跳变恰好落在段中间时，该段前半仍会被抬高 50ms
    # （实测 p99.9 仍为 50ms），所以必须先按跳变切分再细分。
    sub_sec = 10
    sub = max(1, int(sub_sec * 1_000_000 / max(1, interval_us)))

    lat = lat_raw.copy()
    d = np.diff(lat_raw)
    jumps = np.where(np.abs(d) > 1_000_000)[0]
    bounds = [0] + [int(j) + 1 for j in jumps] + [int(n)]

    seg_mins = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        for k in range(a, b, sub):
            e = min(k + sub, b)
            s = lat_raw[k:e]
            m = int(s.min())
            seg_mins.append(m)
            lat[k:e] = s - m

    seg_mins = np.asarray(seg_mins, dtype=np.int64)
    drift_us = (seg_mins[-1] - seg_mins[0]) / 1000.0
    span_s = n * interval_us / 1e6
    span_h = span_s / 3600.0
    ppm = -(drift_us * 1000.0) / (span_s * 1e9) * 1e6 if span_s > 0 else 0.0

    jpath = Path(f"{prefix}.jitter.bin")
    lpath = Path(f"{prefix}.latency.bin")
    write_v2(jpath, jitter, n)
    write_v2(lpath, lat, n)

    def stat(name: str, vals: np.ndarray) -> str:
        return (f"{name}: n={vals.size} min={vals.min()/1000:.3f}us "
                f"med={np.median(vals)/1000:.3f}us "
                f"p99={np.percentile(vals, 99)/1000:.3f}us "
                f"p99.9={np.percentile(vals, 99.9)/1000:.3f}us "
                f"p99.99={np.percentile(vals, 99.99)/1000:.3f}us "
                f"max={vals.max()/1000:.3f}us")

    print(stat("inmate jitter", jitter))
    print(stat("xdomain latency (seg-calibrated)", lat))
    print(f"校准: sub={sub_sec}s 子段数={len(seg_mins)} 跳变切点={len(jumps)} "
          f"基线首末差={drift_us:.3f}us 跨度={span_h:.2f}h 等效={ppm*1000:.3f}ppb")
    if jumps.size:
        worst = int(np.argmax(np.abs(d[jumps])))
        print(f"⚠ 检测到 {jumps.size} 次时钟跳变，最大 {np.abs(d[jumps]).max()/1e6:.3f}ms "
              f"@ 第 {jumps[worst] * interval_us / 1e6:.0f}s")
    print(f"-> {jpath}")
    print(f"-> {lpath}")


if __name__ == "__main__":
    main()
