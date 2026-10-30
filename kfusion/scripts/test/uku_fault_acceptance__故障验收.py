#!/usr/bin/env python3
"""uku_fault_acceptance__故障验收.py — 验收「复制失败后状态不再悄悄推进」。

要证明的三件事：
  1. 正常路径：逐拍 seq / 输入 / 输出 / 持久状态与参考执行一致；
  2. copy_to_user 失败（模块参数注入）时：阶段函数**已经执行过**，
     所以持久状态前进了一次（与参考执行同一拍相同），但调用返回错误；
  3. 失败后重试同一 seq 必须被拒（-EIO），且持久状态**不再前进**；
     显式 RESET 会清空持久状态（契约），之后才能重新开始。

为什么不能只测 2 和 3：没有 1 的参考值，"状态前进了一次"就无法证实——
失败后读到任何数都可以被解释成"没执行"或"执行了两次"。所以两相必须比对。

用法（root）：
  python3 uku_fault_acceptance__故障验收.py --module <kfusion_uku.ko> \
      --seqs 8 --fault-at 5 -o /tmp/uku_fault.json
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import io
import json
import os
import struct
import subprocess
import sys

DEV = "/dev/kfusion_uku"
MAGIC = 0x554B5531
MAX = 8

# struct kfusion_uku_msg：u32 magic, u32 seq, i32 n_in, i32 n_out,
#                         i32 in[8], i32 out[8], i64 k_start, i64 k_end  → 96 B
MSG_FMT = "<IIii8i8iqq"
MSG_SIZE = struct.calcsize(MSG_FMT)
ST_FMT = "<Ii"          # struct kfusion_uku_state: u32 magic, i32 value
ST_SIZE = struct.calcsize(ST_FMT)


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("K") << 8) | nr


IOC_MSG = _ioc(3, 1, MSG_SIZE)      # _IOWR('K', 1, msg)
IOC_STATE = _ioc(3, 2, ST_SIZE)     # _IOWR('K', 2, state)
IOC_RESET = _ioc(0, 3, 0)           # _IO('K', 3)


def insmod(path, args=()):
    subprocess.run(["rmmod", "kfusion_uku"], capture_output=True)
    cmd = ["insmod", path] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("insmod 失败：%s\n%s" % (" ".join(cmd), r.stderr))


def rmmod():
    subprocess.run(["rmmod", "kfusion_uku"], capture_output=True)


# 字段偏移（与 MSG_FMT 一致，别手算错一次就全盘读错）
OFF_OUT0 = 48          # out[0]
OFF_KEND = 88          # k_end_ns


def call_seq(fd, seq, value):
    """发一次内核阶段请求。返回 (rc, errno, out0, k_end_ns)。

    rc=0 成功；否则 rc=-errno（与内核返回值一致）。
    """
    # 22 个字段：magic,seq,n_in,n_out + in[8] + out[8] + k_start + k_end
    args = ([MAGIC, seq & 0xFFFFFFFF, 1, 1, value] + [0] * 7
            + [0] * 8 + [0, 0])
    buf = bytearray(struct.pack(MSG_FMT, *args))
    try:
        # mutate_flag=True 时缓冲区被就地改写；不要用返回值当数据
        # （ioctl 本身返回 0，Python 会把那个 int 返回给你）。
        fcntl.ioctl(fd, IOC_MSG, buf, True)
        out0 = struct.unpack_from("<i", buf, OFF_OUT0)[0]
        k_end = struct.unpack_from("<q", buf, OFF_KEND)[0]
        return 0, 0, out0, k_end
    except OSError as e:
        return -e.errno, e.errno, None, None


def read_state(fd):
    buf = bytearray(ST_SIZE)
    try:
        fcntl.ioctl(fd, IOC_STATE, buf, True)
        magic, val = struct.unpack(ST_FMT, bytes(buf))
        return val if magic == MAGIC else None
    except OSError:
        return None


def do_reset(fd):
    try:
        fcntl.ioctl(fd, IOC_RESET, b"")
        return 0
    except OSError as e:
        return -e.errno


def run_phase(seqs, values, fault_at=0, verbose=True):
    """跑一轮：返回逐拍记录 + 故障后的重试与复位结果。"""
    fd = os.open(DEV, os.O_RDWR)
    trace = []
    try:
        for i, seq in enumerate(seqs):
            rc, en, out0, _ = call_seq(fd, seq, values[i])
            st = read_state(fd)
            trace.append({"seq": seq, "rc": rc, "errno": en,
                          "out": out0, "state_after": st})
            if verbose:
                print("  seq=%-2d rc=%-3d state=%s" % (seq, rc, st))
        doc = {"trace": trace}
        if fault_at:
            hit = [t for t in trace if t["seq"] == fault_at]
            if not hit:
                doc["fault_ran"] = False
                return doc
            doc["state_at_fault"] = hit[0]["state_after"]
            # 重试同一 seq：必须被拒，且状态不得再前进
            rc2, en2, out2, _ = call_seq(fd, fault_at, values[seqs.index(fault_at)])
            doc["retry"] = {"rc": rc2, "errno": en2,
                            "state_after": read_state(fd)}
            # 显式复位：契约是清空持久状态
            doc["reset_rc"] = do_reset(fd)
            doc["state_after_reset"] = read_state(fd)
            # 复位后应能重新开始（seq 从 1）
            rc3, en3, out3, _ = call_seq(fd, 1, values[0])
            doc["after_reset_call"] = {"rc": rc3, "errno": en3,
                                       "state_after": read_state(fd)}
        return doc
    finally:
        os.close(fd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", required=True)
    ap.add_argument("--seqs", type=int, default=8)
    ap.add_argument("--fault-at", type=int, default=5)
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("需要 root（insmod/rmmod）")
    if not os.path.isfile(a.module):
        raise SystemExit("模块不存在：%s" % a.module)

    seqs = list(range(1, a.seqs + 1))
    values = [((i * 37) % 601) - 300 for i in range(a.seqs)]

    print("▶ 相 1/2：参考执行（不注入故障）")
    insmod(a.module)
    ref = run_phase(seqs, values)
    rmmod()

    print("▶ 相 2/2：注入 copy_to_user 失败（fault_at_seq=%d）" % a.fault_at)
    insmod(a.module, ["fault_at_seq=%d" % a.fault_at])
    flt = run_phase(seqs, values, fault_at=a.fault_at)
    rmmod()

    doc = {"kind": "uku_fault_acceptance", "version": 1,
           "module": os.path.abspath(a.module),
           "seqs": seqs, "values": values, "fault_at": a.fault_at,
           "reference": ref, "faulted": flt, "checks": []}
    chk = []

    def add(name, ok, detail=""):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})

    rt = ref["trace"]
    ft = flt["trace"]
    # 1) 正常路径与自身一致（顺序、状态单调）
    add("参考执行全部成功", all(t["rc"] == 0 for t in rt),
        ",".join(str(t["rc"]) for t in rt))
    # 2) 故障前各拍状态与参考相同（同输入 ⇒ 同状态）
    same_before = all(rt[i]["state_after"] == ft[i]["state_after"]
                      for i in range(len(seqs))
                      if seqs[i] < a.fault_at)
    add("故障前逐拍状态与参考一致", same_before)
    # 3) 故障拍返回 -EFAULT
    fh = [t for t in ft if t["seq"] == a.fault_at]
    add("故障拍返回 EFAULT", bool(fh) and fh[0]["rc"] == -errno.EFAULT,
        "rc=%s" % (fh[0]["rc"] if fh else None))
    # 4) 故障拍的持久状态 == 参考同一拍（证明阶段确实执行过一次）
    ref_at = [t for t in rt if t["seq"] == a.fault_at]
    add("故障拍状态已前进且与参考同拍相同",
        bool(ref_at) and fh and fh[0]["state_after"] == ref_at[0]["state_after"],
        "fault=%s ref=%s" % (fh[0]["state_after"] if fh else None,
                             ref_at[0]["state_after"] if ref_at else None))
    # 5) 重试被拒（-EIO）且状态不再前进
    retry = flt.get("retry") or {}
    add("重试被拒（EIO）", retry.get("rc") == -errno.EIO, "rc=%s" % retry.get("rc"))
    add("重试后状态未再前进",
        retry.get("state_after") == (fh[0]["state_after"] if fh else None),
        "retry=%s fault=%s" % (retry.get("state_after"),
                               fh[0]["state_after"] if fh else None))
    # 6) 复位清空持久状态，之后可重新开始
    add("RESET 返回 0", flt.get("reset_rc") == 0, "rc=%s" % flt.get("reset_rc"))
    add("RESET 后持久状态清零", flt.get("state_after_reset") == 0,
        "state=%s" % flt.get("state_after_reset"))
    ar = flt.get("after_reset_call") or {}
    add("RESET 后可重新开始", ar.get("rc") == 0, "rc=%s" % ar.get("rc"))

    doc["checks"] = chk
    doc["pass"] = all(c["ok"] for c in chk)

    if a.out:
        d = os.path.dirname(os.path.abspath(a.out))
        if d:
            os.makedirs(d, exist_ok=True)
        with io.open(a.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")

    print()
    for c in chk:
        print("%s %s%s" % ("PASS" if c["ok"] else "FAIL", c["check"],
                           ("  [" + c["detail"] + "]") if c["detail"] else ""))
    print("\n%s" % ("全部通过" if doc["pass"] else "存在未通过项"))
    return 0 if doc["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
