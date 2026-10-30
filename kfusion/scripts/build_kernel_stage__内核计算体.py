#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_kernel_stage__内核计算体.py — 用 LLVM Pass 生成内核计算体目标文件。

为什么必须单独有这一步：
  `linux_kernel_emitter.py` 的 Kbuild 里写的是 `stage_impl.o`，注释也写着
  「由现有 LLVM Pass 生成」；但流水线实际只把原始 C 复制进 kmod 目录，
  由 kbuild 现场编译。那样 .ko 里的计算体**从未经过 PLCFusionPass**，
  论文里不能说"编译器把选中的计算函数转换成了内核可链接代码"。

本脚本把这条链补上：

    task.c --clang--> task.ll --opt(plc-fusion-remap)--> task.kernel.ll --llc--> stage_impl.o

三条硬规矩（fail-closed，不许静默回落）：
  1. Pass 报错就退出，绝不退回"直接编译原 C"。
  2. `.o` 里有未定义符号就退出——内核模块里不能出现意外外部依赖。
  3. 记录 sha256（源/LL/目标文件/Pass 库）与选中的函数集合，便于事后复核
     「这个 .ko 到底是由哪次转换产生的」。

函数选择：当前 `plc-fusion-remap` 没有"只处理这些函数"的选项，所以本步先对整模块
做重映射，再用 `internalize` 把公开符号收敛到计划里入选的内核函数（外加它们用的
全局）。这样 .o 里不会留下未被选中的阶段实现。
"""
from __future__ import print_function

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys


def run(cmd, what):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.communicate()[0]
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    if p.returncode != 0:
        sys.stderr.write("!! %s 失败（rc=%d）：\n%s\n" % (what, p.returncode, out))
        sys.exit(3)
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def kernel_roots(plan):
    """计划里 domain=linux_kernel 的组包含的阶段函数。"""
    roots, groups = [], []
    for g in plan.get("groups") or []:
        if g.get("domain") != "linux_kernel":
            continue
        groups.append(g.get("id"))
        for m in g.get("members") or []:
            if m not in roots:
                roots.append(m)
    return roots, groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="原始周期 C 程序")
    ap.add_argument("--plan", required=True, help="已验证的 plan.json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--pass-so", required=True)
    ap.add_argument("--clang", default="clang")
    ap.add_argument("--opt", default="opt")
    ap.add_argument("--llc", default="llc")
    ap.add_argument("--extra-public", default="",
                    help="额外保留的全局符号，逗号分隔（状态变量等）")
    ap.add_argument("--whole-module", action="store_true",
                    help="不做 internalize（调试用；正式部署不要开）")
    a = ap.parse_args()

    out = os.path.abspath(a.out_dir)
    if not os.path.isdir(out):
        os.makedirs(out)
    if not os.path.isfile(a.pass_so):
        sys.stderr.write("!! 缺 Pass 库 %s\n" % a.pass_so)
        return 2

    with io.open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    roots, groups = kernel_roots(plan)
    if not roots:
        sys.stderr.write("!! 计划里没有 linux_kernel 组的阶段，不该走内核计算体构建\n")
        return 2

    ll = os.path.join(out, "task.kernel.ll")
    mapped = os.path.join(out, "task.kernel.mapped.ll")
    obj = os.path.join(out, "stage_impl.o")

    # 与流水线 step 2 完全一样的 flags：保证这里的 IR 就是被分析的那份
    # -fno-common 很关键：否则 C 的暂定定义会变成 common symbol，
    # modpost 会把它们当成「模块引用但未定义」而拒绝链接。
    run([a.clang, "-S", "-emit-llvm", "-O2", "-fno-inline",
         "-fno-inline-functions", "-fno-builtin", "-fno-common",
         "-o", ll, a.source], "clang → IR")

    remap = run([a.opt, "-load-pass-plugin", a.pass_so,
                 "-passes=plc-fusion-remap", ll, "-S", "-o", mapped],
                "plc-fusion-remap（内核化重映射）")
    if "error" in remap.lower():
        sys.stderr.write("!! Pass 输出里有 error：\n%s\n" % remap)
        return 3

    src_ir = mapped
    if not a.whole_module:
        extra = [s.strip() for s in a.extra_public.split(",") if s.strip()]
        pub = ",".join(roots + extra)
        src_ir = os.path.join(out, "task.kernel.selected.ll")
        run([a.opt, "-passes=internalize", "-internalize-public-api-list=" + pub,
             mapped, "-S", "-o", src_ir], "internalize（收敛到入选函数）")

    run([a.llc, "-filetype=obj", src_ir, "-o", obj], "llc → 目标文件")
    # 再出一份**汇编**：kbuild 不收「没有 .cmd 的预编译 .o」，但会正常汇编 .s。
    # .s 同样是 llc 的直接产物，所以「.ko 里的计算体来自 LLVM」这条依然成立。
    asm = os.path.join(out, "stage_impl.S")
    run([a.llc, "-filetype=asm", src_ir, "-o", asm], "llc → 汇编")

    # 未定义符号必须为空：内核模块里不允许意外外部依赖
    p = subprocess.Popen(["nm", "-u", obj], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    und = p.communicate()[0]
    if isinstance(und, bytes):
        und = und.decode("utf-8", "replace")
    undef = [ln.strip() for ln in und.splitlines() if ln.strip()]
    if undef:
        sys.stderr.write("!! stage_impl.o 有未定义符号，拒绝交付：\n  %s\n"
                         % "\n  ".join(undef[:10]))
        return 4

    manifest = {
        "kind": "kernel_stage_build", "version": 1,
        "source": os.path.abspath(a.source),
        "plan": os.path.abspath(a.plan),
        "kernel_groups": groups,
        "kernel_roots": roots,
        "internalized": not a.whole_module,
        "pass_so": os.path.abspath(a.pass_so),
        "tools": {"clang": a.clang, "opt": a.opt, "llc": a.llc},
        "sha256": {
            "source": sha256(a.source),
            "task_ll": sha256(ll),
            "mapped_ll": sha256(mapped),
            "selected_ll": sha256(src_ir) if src_ir != mapped else None,
            "stage_obj": sha256(obj),
            "stage_asm": sha256(asm),
            "pass_so": sha256(a.pass_so),
        },
        "bytes": os.path.getsize(obj),
        "asm_bytes": os.path.getsize(asm),
        "note": ("计算体来自 Clang/LLVM + PLCFusionPass；不是原始 C 的直接编译。"
                 "未定义符号为空；函数集合收敛到计划里入选的内核组。"),
    }
    mp = os.path.join(out, "kernel_stage.manifest.json")
    with io.open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("kernel_stage: %s (%d bytes) + %s (%d bytes, 供 kbuild), roots=%s"
          % (obj, manifest["bytes"], asm, manifest["asm_bytes"],
             ",".join(roots)))
    print("manifest: %s" % mp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
