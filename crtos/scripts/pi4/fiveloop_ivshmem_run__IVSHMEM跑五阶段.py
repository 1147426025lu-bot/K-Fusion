#!/usr/bin/env python3
# fiveloop_ivshmem_run__IVSHMEM跑五阶段.py — root cell 经 UIO 下发 fiveloop plan/flat
#
# 前置：inmate 已 start，且 init 为 ecos_boot（fiveloopd 在轮询 0x1faf1080）。
# 用法：
#   python3 fiveloop_ivshmem_run__IVSHMEM跑五阶段.py [cycles=5000]
import json
import mmap
import os
import struct
import sys
import time

DEV = os.environ.get("IVSHMEM_DEV", "/dev/uio0")
MAP_INDEX = int(os.environ.get("IVSHMEM_MAP", "2"))
PAGE = 4096
LEN = 0x9000
OFF = 0x80
MAGIC = 0xEC05F10C
CMD_RUN = 1
ST_DONE = 2
ST_ERROR = 3
ALIVE = 0xF100D00D
ALIVE_OFF = OFF + 15 * 4
CMD_SEQ_OFF = OFF + 12 * 4
RESULT_SEQ_OFF = OFF + 13 * 4
ERROR_OFF = OFF + 14 * 4


def open_rw():
    fd = os.open(DEV, os.O_RDWR)
    m = mmap.mmap(fd, LEN, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=MAP_INDEX * PAGE)
    return fd, m


def unpack(m):
    return struct.unpack_from("<16I", m, OFF)


def wait_alive(m, timeout_s=8):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        alive = struct.unpack_from("<I", m, ALIVE_OFF)[0]
        if alive == ALIVE:
            print("inmate alive (heartbeat 0x%08X)" % alive, flush=True)
            return True
        time.sleep(0.1)
    print("NO_HEARTBEAT last=0x%08X" % struct.unpack_from("<I", m, ALIVE_OFF)[0],
          flush=True)
    return False


def run_mode(m, mode_id, cycles, timeout_s):
    previous = struct.unpack_from("<I", m, RESULT_SEQ_OFF)[0]
    seq = (previous + 1) & 0xFFFFFFFF
    if seq == 0:
        seq = 1
    # 清 status，最后写 cmd
    struct.pack_into("<12I", m, OFF,
                     MAGIC, 0, mode_id, cycles,
                     0, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<I", m, CMD_SEQ_OFF, seq)
    struct.pack_into("<I", m, RESULT_SEQ_OFF, 0)
    struct.pack_into("<I", m, ERROR_OFF, 0)
    struct.pack_into("<I", m, OFF + 4, CMD_RUN)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        words = unpack(m)
        magic, cmd, mode, cyc, digest, rp50, rmax, sp50, miss, st, ep50, nrel, cmd_seq, result_seq, error, alive = words
        if result_seq == seq and st in (ST_DONE, ST_ERROR):
            return {
                "status": st,
                "mode": "flat" if mode_id else "plan",
                "cycles": cyc,
                "digest": "%08x" % digest,
                "resp_p50_us": rp50,
                "resp_max_us": rmax,
                "start_p50_us": sp50,
                "miss": miss,
                "exec_p50_us": ep50,
                "releases_per_period": nrel,
                "command_seq": seq,
                "result_seq": result_seq,
                "protocol_error": error,
                "elapsed_s": round(time.time() - t0, 3),
            }
        time.sleep(0.05)
    return {"status": "timeout", "mode": "flat" if mode_id else "plan",
            "command_seq": seq, "result_seq": 0,
            "protocol_error": "RESULT_SEQ_TIMEOUT"}


def main():
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    if not os.path.exists(DEV):
        print("ERROR: 缺 %s" % DEV, file=sys.stderr)
        return 1
    timeout = max(30, cycles / 1000.0 + 20)
    fd, m = open_rw()
    try:
        if not wait_alive(m):
            return 3
        rows = []
        for mode_id in (0, 1):
            print(">> run mode=%d cycles=%d" % (mode_id, cycles), flush=True)
            row = run_mode(m, mode_id, cycles, timeout)
            print(json.dumps(row, ensure_ascii=False))
            rows.append(row)
            time.sleep(0.2)
    finally:
        m.close()
        os.close(fd)

    if len(rows) == 2 and rows[0].get("digest") and rows[0].get("digest") == rows[1].get("digest") \
            and rows[0].get("status") == ST_DONE and rows[1].get("status") == ST_DONE:
        print("DIGEST_OK", rows[0]["digest"])
        return 0
    print("DIGEST_FAIL", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
