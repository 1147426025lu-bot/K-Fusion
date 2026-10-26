#!/usr/bin/env python3
"""xipc_layout_check.py —— 不用编译器就能检查 ecos_xipc.h 的共享布局。

为什么需要它：C 里的 `XIPC_ASSERT_EQ` 只在**有编译器**时生效，而本机（开发机）
没有 gcc/clang。布局一旦漂移，症状是"两端都跑得动、数据就是不对"——那种 bug
上板调试一轮要十几分钟。所以自测里放一个纯 Python 的算术校验做前置闸门。

它模拟 C 的结构体布局：uint32_t = 4 字节、数组按元素数乘、按声明顺序累加
（没有对齐填充，因为所有成员都是 4 字节对齐的 uint32_t）。

退出码：0 全部通过｜1 有不符
"""
import io
import re
import sys

HDR = "kfusion/runtime/xshm/ecos_xipc.h"

# 必须与 ecos_xipc.h 以及两端代码里的假设一致
EXPECT = {
    "slot_bytes": 20,
    "head": 0x40,
    "tail": 0x80,
    "drops": 0xC0,
    "slot": 0x100,
    "ring_max": 12288,          # PR10-F：约束放宽到 peer 片 XIPC_PEER_STRIDE（0x3000）
    "rings": {"XIPC_RING_R2I": 0x0000, "XIPC_RING_I2R": 0x3000,
              "XIPC_STATS_OFF": 0x6000},
    "map_size": 0x9000,
    "peer_stride": 0x3000,
}

TYPE_SIZE = {
    "uint32_t": 4, "int32_t": 4, "uint64_t": 8, "int64_t": 8,
    "uint16_t": 2, "uint8_t": 1,
}


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def struct_body(text, name):
    m = re.search(r"struct\s+%s\s*\{(.*?)\};" % name, text, flags=re.S)
    return m.group(1) if m else None


def layout(body, known_structs=None):
    """返回 {字段名: (偏移, 字节数)} 与结构体总大小。"""
    known_structs = known_structs or {}
    fields, off = {}, 0
    # 形如:  volatile uint32_t pad0[12];   或   struct xipc_slot slot[XIPC_SLOT_N];
    for raw in body.split(";"):
        decl = raw.strip()
        if not decl:
            continue
        m = re.match(r"^(?:volatile\s+)?(?:struct\s+(\w+)|(\w+))(?:\s+(\w+))?"
                     r"\s*(?:\[\s*(\w+)\s*\])?$", decl)
        if not m:
            continue
        sub, base, name, arr = m.group(1), m.group(2), m.group(3), m.group(4)
        if not name:
            continue
        if sub:
            size = known_structs.get(sub)     # 嵌套结构体用已算出的 sizeof
        else:
            size = TYPE_SIZE.get(base)
        if size is None:
            continue
        count = 1
        if arr:
            if arr.isdigit():
                count = int(arr)
            elif arr in CONST:
                count = CONST[arr]
        fields[name] = (off, size * count)
        off += size * count
    return fields, off


def main():
    global HDR
    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "--header":
        HDR = argv[1]          # 负向测试用：指向一份被改坏的副本

    text = strip_comments(io.open(HDR, encoding="utf-8").read())

    global CONST
    CONST = {}
    for m in re.finditer(r"#define\s+(XIPC_\w+)\s+(0x[0-9a-fA-F]+|[0-9]+)[uUlL]*", text):
        CONST[m.group(1)] = int(m.group(2), 0)

    fails = []

    def check(what, got, want):
        ok = got == want
        print("  %s %-42s got=%s want=%s" % ("✅" if ok else "❌", what, got, want))
        if not ok:
            fails.append(what)

    # 常量
    check("XIPC_SLOT_BYTES", CONST.get("XIPC_SLOT_BYTES"), EXPECT["slot_bytes"])
    check("XIPC_MAP_SIZE", CONST.get("XIPC_MAP_SIZE"), EXPECT["map_size"])
    check("XIPC_PEER_STRIDE", CONST.get("XIPC_PEER_STRIDE"), EXPECT["peer_stride"])
    for k, v in EXPECT["rings"].items():
        check(k, CONST.get(k), v)

    # 三个片区不能互相重叠
    order = sorted(EXPECT["rings"].items(), key=lambda kv: kv[1])
    for (n1, o1), (n2, o2) in zip(order, order[1:]):
        check("%s 与 %s 不重叠" % (n1, n2), o2 - o1 >= 4096, True)

    # 环形结构体
    sbody = struct_body(text, "xipc_slot")
    if sbody is None:
        print("❌ 找不到 struct xipc_slot")
        return 1
    _sf, ssize = layout(sbody)
    check("sizeof(struct xipc_slot)", ssize, EXPECT["slot_bytes"])

    body = struct_body(text, "xipc_ring")
    if body is None:
        print("❌ 找不到 struct xipc_ring")
        return 1
    fields, size = layout(body, {"xipc_slot": ssize})
    for key in ("head", "tail", "drops"):
        if key in fields:
            check("offsetof(%s)" % key, fields[key][0], EXPECT[key])
        else:
            print("❌ 结构体里没有字段 %s" % key)
            fails.append(key)

    if "slot" in fields:
        check("offsetof(slot)", fields["slot"][0], EXPECT["slot"])
        check("sizeof(slot 数组)", fields["slot"][1],
              EXPECT["slot_bytes"] * CONST.get("XIPC_SLOT_N", 0))
    else:
        print("❌ 结构体里没有字段 slot")
        fails.append("slot")

    check("sizeof(struct xipc_ring) <= peer 片(%d)" % EXPECT["ring_max"],
          size <= EXPECT["ring_max"], True)

    # 两端必须 include 同一份头
    for src in ("kfusion/runtime/xshm/root_side.c",
                "crtos/jailhouse_overlay_pi4/nuttx/apps/examples/xipc/xipc_main.c"):
        t = io.open(src, encoding="utf-8").read()
        check("%s include ecos_xipc.h" % src.split("/")[-1],
              '#include "ecos_xipc.h"' in t, True)

    # 仓库里不能有第二份头（真源只有 kfusion/runtime/xshm/ecos_xipc.h）
    import os
    dup = []
    for root, _dirs, files in os.walk("."):
        if any(x in root for x in (".git", ".workbuddy", "build")):
            continue
        for f in files:
            if f == "ecos_xipc.h":
                p = os.path.join(root, f).replace("\\", "/")
                if not p.endswith(HDR):
                    dup.append(p)
    check("没有第二份 ecos_xipc.h", dup, [])

    print()
    if fails:
        print("失败 %d 项：%s" % (len(fails), ", ".join(fails)))
        return 1
    print("布局校验全部通过")
    return 0


CONST = {}

if __name__ == "__main__":
    sys.exit(main())
