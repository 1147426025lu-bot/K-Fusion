#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 /dev/kf_let_log 导出逐次 LET 读写样本为 CSV/NPZ。

样本口径：
  op=1 write → W=t_after, d=nominal；抖动 |W-d|
  op=2 read  → R=t_after, r=nominal；抖动 |R-r|
  status≠0 不计有效抖动，另行统计 skip/hold/err
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import struct
import sys

MAGIC = 0x4B464C54
HDR_FMT = "<IIIIIIII"  # 8×u32
HDR_SIZE = struct.calcsize(HDR_FMT)
# cycle_id u32, op/status/ctx/cpu u8×4, nominal/before/after s64×3, version u32
SAMP_FMT = "<IBBBBqqiI"
# Actually: u32 + 4×u8 + 3×s64 + u32 — but packing may pad.
# C __packed: u32,u8,u8,u8,u8,s64,s64,s64,u32 = 4+4+24+4 = 36
SAMP_FMT = "<I4B3qI"
SAMP_SIZE = struct.calcsize(SAMP_FMT)

OP_NAME = {1: "write", 2: "read"}
ST_NAME = {0: "ok", 1: "skip", 2: "hold", 3: "err"}
# 臂标识：0=kthread(B 组) 1=hrtimer_hard(D 组) 2=手写硬中断(C 组) 3=用户态(A 组) 4=用户态忙等(诊断)
ARM_NAME = {0: "thread", 1: "irq_hard", 2: "handwritten_irq", 3: "userspace",
            4: "userspace_spin"}


def pctl(vals, q):
    """最近秩分位数（不插值）：小样本下不会凭空造出中间值。

    vals 需已排序。q ∈ [0,100]。空表返回 None。
    """
    if not vals:
        return None
    if q <= 0:
        return vals[0]
    if q >= 100:
        return vals[-1]
    # 最近秩：ceil(q/100 * n)
    import math
    k = int(math.ceil(q / 100.0 * len(vals)))
    k = max(1, min(k, len(vals)))
    return vals[k - 1]


def ctx_name(c: int) -> str:
    parts = []
    if c & 0x01:
        parts.append("hardirq")
    if c & 0x02:
        parts.append("softirq")
    if c & 0x04:
        parts.append("task")
    if c & 0x08:
        parts.append("nmi")
    return "+".join(parts) if parts else "unknown"


def load_bin(path: str):
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < HDR_SIZE:
        raise SystemExit("日志过短")
    magic, hver, sb, n, drops, period_us, arm_path, _ = struct.unpack_from(HDR_FMT, raw, 0)
    if magic != MAGIC:
        raise SystemExit("magic 不符: %#x" % magic)
    if sb != SAMP_SIZE:
        # 容忍编译器对齐差异：以头里的 sample_bytes 为准重解
        pass
    samples = []
    off = HDR_SIZE
    use = sb if sb else SAMP_SIZE
    for i in range(n):
        chunk = raw[off:off + use]
        if len(chunk) < use:
            break
        if use == SAMP_SIZE:
            cycle, op, status, ctx, cpu, nom, tb, ta, ver = struct.unpack(SAMP_FMT, chunk)
        else:
            # 回退：按字段手工切（假定无尾填充、与 __packed 一致）
            cycle, op, status, ctx, cpu = struct.unpack_from("<I4B", chunk, 0)
            nom, tb, ta = struct.unpack_from("<3q", chunk, 8)
            ver = struct.unpack_from("<I", chunk, 32)[0]
        samples.append({
            "idx": i,
            "cycle_id": cycle,
            "op": op,
            "op_name": OP_NAME.get(op, str(op)),
            "status": status,
            "status_name": ST_NAME.get(status, str(status)),
            "ctx": ctx,
            "ctx_name": ctx_name(ctx),
            "cpu": cpu,
            "nominal_ns": nom,
            "t_before_ns": tb,
            "t_after_ns": ta,
            "version": ver,
            "jitter_ns": abs(ta - nom) if status == 0 else None,
            "dur_ns": ta - tb,
        })
        off += use
    hdr = {
        "magic": magic,
        "hdr_version": hver,
        "sample_bytes": sb,
        "n_samples": n,
        "drops": drops,
        "period_us": period_us,
        "arm_path": arm_path,
        "arm_name": ARM_NAME.get(arm_path, "path%d" % arm_path),
    }
    return hdr, samples


def summarize(samples):
    def max_jit(op):
        vals = [s["jitter_ns"] for s in samples
                if s["op"] == op and s["status"] == 0 and s["jitter_ns"] is not None]
        return max(vals) if vals else None

    def signed_extrema(op):
        late = early = None
        for s in samples:
            if s["op"] != op or s["status"] != 0:
                continue
            d = s["t_after_ns"] - s["nominal_ns"]
            if late is None or d > late:
                late = d
            if early is None or -d > early:
                early = -d if d < 0 else (early if early is not None else 0)
        # early: max(r-R) when R<r; if never early, 0
        if early is None:
            early = 0
        if late is None:
            late = 0
        # recompute early properly
        early = 0
        late = 0
        for s in samples:
            if s["op"] != op or s["status"] != 0:
                continue
            d = s["t_after_ns"] - s["nominal_ns"]
            if d > late:
                late = d
            if -d > early:
                early = -d
        return late, early

    # 版本语义：同 cycle 内 write 的 version 应等于随后 read 的 version；
    # 连续 ok write 的 version 应严格递增 1。
    ver_mismatch = 0
    ver_nonmono = 0
    by_cycle = {}
    for s in samples:
        if s["status"] != 0:
            continue
        by_cycle.setdefault(s["cycle_id"], {})[s["op_name"]] = s
    last_w = None
    for cid in sorted(by_cycle.keys()):
        ev = by_cycle[cid]
        w, r = ev.get("write"), ev.get("read")
        if w and r and w["version"] != r["version"]:
            ver_mismatch += 1
        if w:
            if last_w is not None and w["version"] != last_w + 1:
                ver_nonmono += 1
            last_w = w["version"]

    rd_late, rd_early = signed_extrema(2)
    wr_late, wr_early = signed_extrema(1)

    def dist(op):
        """返回该 op 的抖动分布（|t_after-nominal|，单位 ns，仅 status=ok）。

        只报分位数不报均值：均值会被个别异常拍拉偏，跨轮比较会造出假波动。
        """
        vals = sorted(s["jitter_ns"] for s in samples
                      if s["op"] == op and s["status"] == 0
                      and s["jitter_ns"] is not None)
        return {
            "n": len(vals),
            "p50_ns": pctl(vals, 50),
            "p90_ns": pctl(vals, 90),
            "p99_ns": pctl(vals, 99),
            "p999_ns": pctl(vals, 99.9),
            "max_ns": vals[-1] if vals else None,
            "mean_ns": (sum(vals) // len(vals)) if vals else None,
        }

    return {
        "n": len(samples),
        "n_read_ok": sum(1 for s in samples if s["op"] == 2 and s["status"] == 0),
        "n_write_ok": sum(1 for s in samples if s["op"] == 1 and s["status"] == 0),
        "n_skip": sum(1 for s in samples if s["status"] == 1),
        "n_hold": sum(1 for s in samples if s["status"] == 2),
        "n_err": sum(1 for s in samples if s["status"] == 3),
        "max_read_jitter_ns": max_jit(2),
        "max_write_jitter_ns": max_jit(1),
        "max_read_late_ns": rd_late,
        "max_read_early_ns": rd_early,
        "max_write_late_ns": wr_late,
        "max_write_early_ns": wr_early,
        "dist_read": dist(2),
        "dist_write": dist(1),
        "version_mismatch_cycles": ver_mismatch,
        "version_nonmono_writes": ver_nonmono,
        "ctx_counts": {},
    }


def trim_warmup(samples, sec):
    """丢弃开跑后前 sec 秒的样本。

    必要性（实测证据）：A 组用户态臂在开跑约 4.5s 内稳定在 8.7us，之后跳到
    22us 并长期稳定；5s 短测因 90% 样本落在瞬态里而给出 8.7us，与 60s 长测的
    22us 冲突。预热期是启动瞬态（tick/调速/缓存状态未定），不是被测对象，
    必须截掉且在报告中注明截断秒数，否则短测与长测的数会互相矛盾。
    """
    if sec <= 0:
        return samples, 0
    noms = [s["nominal_ns"] for s in samples if s["status"] == 0]
    if not noms:
        return samples, 0
    cut = min(noms) + int(sec * 1e9)
    kept = [s for s in samples if s["nominal_ns"] >= cut]
    return kept, len(samples) - len(kept)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default="/dev/kf_let_log")
    ap.add_argument("--bin", help="已保存的二进制；优先于 --dev")
    ap.add_argument("-o", "--out-dir", required=True)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--warmup-sec", type=float, default=0.0,
                    help="丢弃开跑后前 N 秒样本（默认 0；长测建议 10）")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    src = args.bin or args.dev
    raw_path = os.path.join(args.out_dir, "%s.kf_let_log.bin" % args.tag)
    if args.bin:
        with open(args.bin, "rb") as f:
            data = f.read()
        open(raw_path, "wb").write(data)
    else:
        with open(args.dev, "rb") as f:
            data = f.read()
        open(raw_path, "wb").write(data)
    hdr, samples = load_bin(raw_path)
    n_raw = len(samples)
    samples, n_trimmed = trim_warmup(samples, args.warmup_sec)
    summary = summarize(samples)
    summary["warmup_sec"] = args.warmup_sec
    summary["n_raw"] = n_raw
    summary["n_trimmed"] = n_trimmed
    # ctx
    from collections import Counter
    summary["ctx_counts"] = dict(Counter(s["ctx_name"] for s in samples))

    csv_path = os.path.join(args.out_dir, "%s.samples.csv" % args.tag)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(samples[0].keys()) if samples else
                           ["idx", "cycle_id", "op_name", "status_name"])
        w.writeheader()
        for s in samples:
            w.writerow(s)

    meta = {"hdr": hdr, "summary": summary, "tag": args.tag, "source": src}
    json.dump(meta, open(os.path.join(args.out_dir, "%s.meta.json" % args.tag),
                         "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print("wrote", csv_path, "n=%d" % len(samples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
