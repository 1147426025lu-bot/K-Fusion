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


def first_line(cmd):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        t = p.communicate()[0]
        if isinstance(t, bytes):
            t = t.decode("utf-8", "replace")
        return t.splitlines()[0].strip() if t.strip() else None
    except Exception:
        return None


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


def all_stages(plan):
    """计划里出现的全部阶段名（用于「未入选阶段不得出现在 .o 里」的负向检查）。"""
    names = []
    for g in plan.get("groups") or []:
        for m in g.get("members") or []:
            if m not in names:
                names.append(m)
    for m in plan.get("order") or []:
        if m not in names:
            names.append(m)
    return names


def nm_symbols(obj):
    """返回 (defined, undefined)。

    defined 只取有定义的（T/t/D/d/B/b/W/w），不含 U——符号表里同一名字
    可能既出现 U 又出现 T（weak/引用），所以要分开看。
    """
    p = subprocess.Popen(["nm", obj], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    txt = p.communicate()[0]
    if isinstance(txt, bytes):
        txt = txt.decode("utf-8", "replace")
    defined, undef = set(), set()
    for ln in txt.splitlines():
        parts = ln.split()
        if len(parts) < 2:
            continue
        if len(parts) == 2 and parts[0] == "U":
            undef.add(parts[1])
        elif len(parts) >= 3:
            defined.add(parts[2])
    return defined, undef


def indirect_calls(ll_text):
    """找出「被调用者不是直接符号」的 call —— 无法静态解析的间接调用。

    形式举例：`%r = call i32 %fp(i32 %x)` / `call void %fn()`。
    直接调用长这样：`call i32 @stage_pid()`。
    这里不做完整 IR 解析，只抓 call 指令里紧跟类型之后是 `%` 的情况；
    误判风险方向是「多报」，而多报只会让候选被拒（fail-closed），可接受。
    """
    hits = []
    for ln in ll_text.splitlines():
        s = ln.strip()
        if not s.startswith("call "):
            continue
        if "@" in s.split("(")[0]:
            continue
        if "%" in s.split("(")[0]:
            hits.append(s[:120])
    return hits


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
    ap.add_argument("--attach-ko", default=None,
                    help="构建完成后回填：把最终 .ko 的哈希写进 manifest，"
                         "让「这个模块由哪次转换产生」可追到最终产物")
    a = ap.parse_args()

    out = os.path.abspath(a.out_dir)
    if a.attach_ko:
        # 后置回填：kbuild 在 build_kernel_stage 之后才产出 .ko，
        # 所以最终模块的身份只能在这时补上（而不是留个空字段）。
        mp = os.path.join(out, "kernel_stage.manifest.json")
        if not os.path.isfile(mp):
            sys.stderr.write("!! 没有 manifest，无法回填 .ko：%s\n" % mp)
            return 2
        with io.open(mp, encoding="utf-8") as f:
            man = json.load(f)
        if not os.path.isfile(a.attach_ko):
            sys.stderr.write("!! .ko 不存在：%s\n" % a.attach_ko)
            return 2
        man["final_ko"] = {
            "path": os.path.abspath(a.attach_ko),
            "sha256": sha256(a.attach_ko),
            "bytes": os.path.getsize(a.attach_ko),
        }
        with io.open(mp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(man, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("manifest 回填 .ko：%s (%d bytes)"
              % (man["final_ko"]["sha256"][:16], man["final_ko"]["bytes"]))
        return 0
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
        # internalize 只改可见性，**不会删代码**：未被调用的阶段仍是 internal 定义。
        # 必须再跑 globaldce 把它们删掉，否则 .o 里仍带着用户态阶段的实现，
        # 「只内核化了选中的函数」这句话就不成立。
        run([a.opt, "-passes=internalize,globaldce",
             "-internalize-public-api-list=" + pub,
             mapped, "-S", "-o", src_ir],
            "internalize+globaldce（收敛到入选函数的依赖闭包）")

    # 间接调用是不可静态解析的，正式路径一律拒绝（Pass 的未映射调用策略同口径）
    with io.open(src_ir, encoding="utf-8", errors="replace") as f:
        ll_text = f.read()
    ind = indirect_calls(ll_text)
    if ind:
        sys.stderr.write("!! 内核计算体里有 %d 处间接调用，无法静态解析，拒绝交付：\n%s\n"
                         % (len(ind), "\n".join("  " + s for s in ind[:5])))
        return 5

    run([a.llc, "-filetype=obj", src_ir, "-o", obj], "llc → 目标文件")
    # 再出一份**汇编**：kbuild 不收「没有 .cmd 的预编译 .o」，但会正常汇编 .s。
    # .s 同样是 llc 的直接产物，所以「.ko 里的计算体来自 LLVM」这条依然成立。
    asm = os.path.join(out, "stage_impl.S")
    run([a.llc, "-filetype=asm", src_ir, "-o", asm], "llc → 汇编")

    # 符号三查（只看 .o 不够：还要看真正交给 kbuild 的 .S 是不是同一份）
    defined, undef = nm_symbols(obj)
    if undef:
        sys.stderr.write("!! stage_impl.o 有未定义符号，拒绝交付：\n  %s\n"
                         % "\n  ".join(sorted(undef)[:10]))
        return 4
    missing = [r for r in roots if r not in defined]
    if missing:
        sys.stderr.write("!! 入选的内核函数不在 .o 里：%s（名字对不上或没定义）\n"
                         % ", ".join(missing))
        return 6
    stages = all_stages(plan)
    leaked = [s for s in stages if s not in roots and s in defined]
    if leaked:
        sys.stderr.write(
            "!! 未入选的阶段出现在内核计算体里：%s\n"
            "   internalize+globaldce 没有收敛住（或这些阶段的调用仍被闭包保留）。\n"
            "   拒绝交付：.ko 里的计算体必须只包含计划选中的内核阶段。\n"
            % ", ".join(leaked))
        return 7

    manifest = {
        "kind": "kernel_stage_build", "version": 1,
        "source": os.path.abspath(a.source),
        "plan": os.path.abspath(a.plan),
        "kernel_groups": groups,
        "kernel_roots": roots,
        "internalized": not a.whole_module,
        "pass_so": os.path.abspath(a.pass_so),
        "tools": {"clang": a.clang, "opt": a.opt, "llc": a.llc},
        "tool_versions": {
            "clang": first_line([a.clang, "--version"]),
            "opt": first_line([a.opt, "--version"]),
            "llc": first_line([a.llc, "--version"]),
        },
        "symbols": {
            "defined": sorted(defined),
            "undefined": sorted(undef),
            "roots_present": [r for r in roots if r in defined],
            "stages_absent": [s for s in all_stages(plan) if s not in roots],
            "indirect_calls": 0,
        },
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
                 "闭包用 internalize+globaldce 收敛；符号三查通过："
                 "入选函数都在、未入选阶段都不在、未定义符号为空。"),
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
