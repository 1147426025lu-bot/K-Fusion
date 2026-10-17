#!/usr/bin/env python3
# ivshmem_probe__跨域共享内存.py — root cell Linux 侧访问 Jailhouse IVSHMEM
#
# 用途：验证 NuttX(Jailhouse 非根 cell) 与 root cell 之间的跨域共享内存通路。
#
# 原理：root cell 里 Jailhouse IVSHMEM 以 PCI 设备(110a:4106) + uio_ivshmem 暴露，
#       /dev/uio0 的 map2 = rw_section @ phys 0x1faf1000 (9KB)。
#       mmap 偏移 = map_index * 4096。
#
# 与 inmate 侧 apps/examples/xshm 约定同一布局（32 位字）：
#   off 0x00 ROOT_MAGIC    root 写，inmate 读
#   off 0x04 INMATE_SEQ    inmate 每轮 +1
#   off 0x08 INMATE_ECHO   inmate 回写读到的 magic
#   off 0x0C INMATE_STATE  1 = running
#
# 用法:
#   python3 ivshmem_probe__跨域共享内存.py poke      # 写入 ROOT_MAGIC(0xA5A5C0DE)
#   python3 ivshmem_probe__跨域共享内存.py watch     # 持续打印四个字段
#   python3 ivshmem_probe__跨域共享内存.py read      # 只读一次
import mmap
import os
import struct
import sys
import time

DEV = os.environ.get("IVSHMEM_DEV", "/dev/uio0")
MAP_INDEX = int(os.environ.get("IVSHMEM_MAP", "2"))   # map2 = rw_section
PAGE = 4096
LEN = 0x9000
MAGIC = 0xA5A5C0DE


def open_rw():
    fd = os.open(DEV, os.O_RDWR)
    m = mmap.mmap(fd, LEN, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=MAP_INDEX * PAGE)
    return fd, m


def fmt(vals):
    root_magic, seq, echo, state = vals
    return ("ROOT_MAGIC=0x%08X  INMATE_SEQ=%-8d  INMATE_ECHO=0x%08X  INMATE_STATE=%d"
            % (root_magic, seq, echo, state))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "read"

    if not os.path.exists(DEV):
        print("ERROR: 缺 %s —— 需要 root cell 里 Jailhouse IVSHMEM 已就绪（jailhouse enable）" % DEV,
              file=sys.stderr)
        return 1

    fd, m = open_rw()
    try:
        if mode == "poke":
            struct.pack_into("<I", m, 0, MAGIC)
            print("已向 %s map%d off 0x00 写入 ROOT_MAGIC = 0x%08X" % (DEV, MAP_INDEX, MAGIC))
            print("现在在 inmate(NSH) 里运行 `xshm` 应看到『ROOT_MAGIC 变为 0xA5A5C0DE』")
            return 0

        if mode == "read":
            print(fmt(struct.unpack_from("<IIII", m, 0)))
            return 0

        # watch
        print("监视 %s map%d (phys 0x1faf1000, %d 字节)，Ctrl-C 退出" % (DEV, MAP_INDEX, LEN))
        last = None
        while True:
            cur = struct.unpack_from("<IIII", m, 0)
            if cur != last:
                print(fmt(cur))
                last = cur
            time.sleep(0.2)
    finally:
        m.close()
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
