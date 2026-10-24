# -*- coding: utf-8 -*-
"""阶段清单：从用户源码里抽取阶段函数签名。

为什么不用 clang
----------------
本机（Windows）没有 clang/gcc；而这一层只需要"这些函数存在、参数表是什么"。
用保守的正则抽取**函数定义**（不是声明），拿不到就报错而不是猜：
猜错的后果是生成出调用不存在的函数的内核模块，insmod 时才炸。

抽取失败或函数缺失时**硬失败**（`StageMissing`），不做"先编出来再说"。
"""

import re

_DEF = re.compile(
    r"(?m)^[ \t]*(?P<ret>(?:static[ \t]+|inline[ \t]+|const[ \t]+)*"
    r"[A-Za-z_][\w \t\*]*?[ \t\*]+)"
    r"(?P<name>[A-Za-z_]\w*)[ \t]*\((?P<params>[^;{)]*)\)[ \t\r\n]*\{",
)


class StageMissing(Exception):
    pass


def scan_source(path):
    """返回 {函数名: {"ret":..., "params":..., "line":...}}（只含本文件里的定义）。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    out = {}
    for m in _DEF.finditer(text):
        ret = " ".join(m.group("ret").split())
        # static/inline 前缀去掉（生成的是 extern 声明）
        for kw in ("static ", "inline ", "const "):
            while ret.startswith(kw):
                ret = ret[len(kw):].strip()
        if not ret:
            ret = "void"
        params = " ".join(m.group("params").split()) or "void"
        line = text[:m.start()].count("\n") + 1
        out[m.group("name")] = {"ret": ret, "params": params, "line": line}
    return out


def prototypes_for(ir, source_path):
    """给 IR 里每个阶段函数生成 extern 声明；缺函数直接抛 StageMissing。"""
    defs = scan_source(source_path)
    protos, missing = [], []
    for g in ir.groups:
        for s in g.stages:
            fn = s.call.split("(")[0].strip()
            if fn not in defs:
                missing.append((s.name, fn))
                continue
            d = defs[fn]
            protos.append("%s %s(%s);" % (d["ret"], fn, d["params"]))
    if missing:
        raise StageMissing(
            "源码 %s 里找不到这些阶段函数定义：%s\n"
            "  → 阶段名必须与 DAG 的 fn 名一致；不接受「先编出来再说」。"
            % (source_path, ", ".join("%s→%s()" % (a, b) for a, b in missing)))
    # 去重（一个函数可能被多个阶段引用）
    seen, uniq = set(), []
    for p in protos:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq
