#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage_extract.py — Gate 1：从一份周期 C 抽出阶段骨架（不手写 DAG）

输入是 **K-C0 子集** 的周期程序，不是任意 C。抽取失败必须退出非零，
不得静默生成半成品内核模块。

证据链：
  1. Clang JSON AST（clang -Xclang -ast-dump=json）给出入口、直接调用、
     全局状态、goto / 间接调用 / 禁调用。
  2. 源注释 `kfusion:task ...` 给出周期/相位等**设计事实**（分析不猜相位）。
  3. 数据依赖仍由后续 LLVM EffectSummary + dag_build 证明；本工具只产出
     与 `stages.json` 同构的骨架，替代人手写的拓扑文件。

用法：
  python stage_extract.py five_stage_loop.c -o out/stages.json
  python stage_extract.py five.c --emit-kmod out/kmod --bind setpoint=1000
"""
from __future__ import print_function

import argparse
import io
import json
import os
import re
import subprocess
import sys

FORBIDDEN_CALLS = frozenset([
    "malloc", "calloc", "realloc", "free", "aligned_alloc", "alloca",
    "printf", "fprintf", "sprintf", "snprintf", "vprintf", "puts", "putchar",
    "scanf", "fopen", "fclose", "fread", "fwrite",
    "fork", "vfork", "execve", "execl", "system", "popen",
    "pthread_create", "pthread_join", "pthread_mutex_lock", "pthread_cond_wait",
    "sleep", "usleep", "nanosleep", "clock_nanosleep",
    "mmap", "munmap", "syscall", "ioctl", "dlopen", "dlsym",
    "longjmp", "setjmp", "sigsetjmp",
])

ENTRY_CANDIDATES = (
    "control_cycle", "plc_cycle", "plc_main", "plc_logic",
    "loop_main", "chain_main",
)

TASK_RE = re.compile(r"kfusion:task\s+(.+)")
KV_RE = re.compile(r"([A-Za-z_][\w]*)=(\S+)")


def load_json_file(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_task_comment(src_text):
    """从源注释读设计事实。相位/周期不是分析推出来的。"""
    out = {}
    for m in TASK_RE.finditer(src_text):
        for k, v in KV_RE.findall(m.group(1)):
            if v.endswith("*/"):
                v = v[:-2].strip()
            out[k] = v
    return out


def walk(node, fn):
    if not isinstance(node, dict):
        return
    fn(node)
    for child in node.get("inner") or []:
        walk(child, fn)


def collect_kind(root, kind):
    found = []

    def acc(n):
        if n.get("kind") == kind:
            found.append(n)

    walk(root, acc)
    return found


def qual_type(n):
    t = n.get("type") or {}
    return t.get("qualType") or t.get("desugaredQualType") or ""


def funcref_name(node):
    """CallExpr 的直接被调函数名；间接调用返回 None。"""
    if not isinstance(node, dict):
        return None
    if node.get("kind") == "DeclRefExpr":
        rd = node.get("referencedDecl") or {}
        if rd.get("kind") == "FunctionDecl":
            return rd.get("name")
        return None
    inner = node.get("inner") or []
    if node.get("kind") == "CallExpr":
        if not inner:
            return None
        return funcref_name(inner[0])
    for c in inner:
        name = funcref_name(c)
        if name:
            return name
    return None


def expr_text(node):
    """把常量实参还原成 C 文本；失败返回 None（K-C0 不允许说不清的实参）。"""
    if not isinstance(node, dict):
        return None
    kind = node.get("kind")
    if kind == "IntegerLiteral":
        return str(node.get("value"))
    if kind == "FloatingLiteral":
        return str(node.get("value"))
    if kind == "CharacterLiteral":
        return str(node.get("value"))
    if kind == "DeclRefExpr":
        rd = node.get("referencedDecl") or {}
        return rd.get("name")
    if kind == "UnaryOperator":
        inner = node.get("inner") or []
        if len(inner) == 1:
            sub = expr_text(inner[0])
            opcode = node.get("opcode") or node.get("valueCategory")
            # JSON 里 opcode 多为 '-'
            op = node.get("opcode") or ""
            if sub is not None:
                return (op or "-") + sub
    inner = node.get("inner") or []
    if kind in ("ImplicitCastExpr", "CStyleCastExpr", "ParenExpr",
                "ConstantExpr", "FullComment"):
        if len(inner) == 1:
            return expr_text(inner[0])
        for c in inner:
            t = expr_text(c)
            if t is not None:
                return t
    return None


def call_args(call):
    inner = call.get("inner") or []
    return inner[1:] if len(inner) > 1 else []


def dump_ast(clang, src, extra_args, tmp_path):
    cmd = [clang] + list(extra_args) + [
        "-Xclang", "-ast-dump=json", "-fsyntax-only",
        "-Wno-unknown-warning-option", src,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()
    if proc.returncode != 0:
        sys.stderr.write(err.decode("utf-8", "replace"))
        raise SystemExit("clang AST 转储失败（exit %d）" % proc.returncode)
    # JSON 可能很大；先落盘再 load，便于排错
    with open(tmp_path, "wb") as f:
        f.write(out)
    with io.open(tmp_path, encoding="utf-8") as f:
        return json.load(f)


def functions_with_body(tu):
    fns = {}

    def acc(n):
        if n.get("kind") != "FunctionDecl":
            return
        name = n.get("name")
        if not name:
            return
        inners = n.get("inner") or []
        body = None
        params = []
        for c in inners:
            if c.get("kind") == "CompoundStmt":
                body = c
            elif c.get("kind") == "ParmVarDecl":
                params.append({
                    "name": c.get("name"),
                    "type": qual_type(c),
                })
        if body is None:
            return
        fns[name] = {"node": n, "body": body, "params": params,
                     "type": qual_type(n)}

    walk(tu, acc)
    return fns


def file_scope_globals(tu):
    """TranslationUnit 下一层的 VarDecl（不是函数内局部）。"""
    globs = []
    for child in tu.get("inner") or []:
        if child.get("kind") == "VarDecl" and child.get("name"):
            globs.append({
                "name": child.get("name"),
                "type": qual_type(child),
                "has_init": any(
                    (c.get("kind") or "").endswith("Literal")
                    or "Init" in (c.get("kind") or "")
                    for c in (child.get("inner") or [])
                ),
            })
    return globs


def digest_vars(globs):
    """融合对照用的标量/数组元素名。数组本身是指针，不能直接 DIGEST_MIX。"""
    names = []
    for g in globs:
        t = g.get("type") or ""
        m = re.search(r"\[(\d+)\]", t)
        if m:
            n = int(m.group(1))
            for i in range(n):
                names.append("%s[%d]" % (g["name"], i))
        elif "*" in t:
            continue
        else:
            names.append(g["name"])
    return names


def direct_calls_in_order(body):
    calls = []

    def acc(n):
        if n.get("kind") == "CallExpr":
            callee = funcref_name(n)
            args = [expr_text(a) for a in call_args(n)]
            calls.append({
                "callee": callee,
                "args": args,
                "indirect": callee is None,
            })

    walk(body, acc)
    return calls


def has_kind(root, kinds):
    hit = []

    def acc(n):
        if n.get("kind") in kinds:
            hit.append(n.get("kind"))

    walk(root, acc)
    return hit


def type_is_vla(root):
    hit = []

    def acc(n):
        t = n.get("type") or {}
        if t.get("kind") == "VariableArrayType" or "VariableArrayType" in (
                t.get("qualType") or ""):
            hit.append(n.get("name") or n.get("kind"))
        if n.get("kind") == "VariableArrayType":
            hit.append("VariableArrayType")

    walk(root, acc)
    return hit


CONTROL_COND = ("IfStmt", "SwitchStmt", "ConditionalOperator")
CONTROL_LOOP = ("ForStmt", "WhileStmt", "DoStmt")
ASSIGN_OPS = frozenset([
    "=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "|=", "^=",
])
UNARY_MUTATE = frozenset(["++", "--"])


def _is_reset_fn(name):
    return name == "loop_reset" or (name or "").endswith("_reset")


def var_names_in(node):
    names = []

    def acc(n):
        if n.get("kind") != "DeclRefExpr":
            return
        rd = n.get("referencedDecl") or {}
        if rd.get("kind") == "VarDecl" and rd.get("name"):
            names.append(rd["name"])

    walk(node, acc)
    return names


def kc0_entry_shape(fns, entry, glob_names):
    """入口必须是直线直接调用。条件/重复/循环/入口写全局一律拒绝，不推断。"""
    violations = []
    info = fns.get(entry)
    if not info:
        return violations
    body = info["body"]
    glob_set = set(glob_names or [])

    def acc(n):
        kind = n.get("kind")
        if kind in CONTROL_COND:
            violations.append({
                "code": "E_KC0_CONDITIONAL_CALL",
                "fn": entry,
                "detail": "入口含 %s：K-C0 不保留条件调用，拒绝转换" % kind,
            })
        elif kind in CONTROL_LOOP:
            violations.append({
                "code": "E_KC0_LOOP_CALL",
                "fn": entry,
                "detail": "入口含 %s：K-C0 不保留循环调用，拒绝转换" % kind,
            })
        elif kind in ("BinaryOperator", "CompoundAssignOperator"):
            if n.get("opcode") in ASSIGN_OPS:
                inner = n.get("inner") or []
                lhs = inner[0] if inner else n
                hit = [nm for nm in var_names_in(lhs) if nm in glob_set]
                if hit:
                    violations.append({
                        "code": "E_KC0_ENTRY_STORE",
                        "fn": entry,
                        "detail": "入口写全局 %s（状态更新必须在阶段函数内）"
                                  % ",".join(hit),
                    })
        elif kind == "UnaryOperator" and n.get("opcode") in UNARY_MUTATE:
            hit = [nm for nm in var_names_in(n) if nm in glob_set]
            if hit:
                violations.append({
                    "code": "E_KC0_ENTRY_STORE",
                    "fn": entry,
                    "detail": "入口写全局 %s（%s）" % (",".join(hit), n.get("opcode")),
                })

    walk(body, acc)

    seq = []
    for call in direct_calls_in_order(body):
        cal = call["callee"]
        if not cal or cal == entry or _is_reset_fn(cal):
            continue
        if cal in fns or call["indirect"]:
            seq.append(cal if cal else "<indirect>")
    seen = {}
    for cal in seq:
        seen[cal] = seen.get(cal, 0) + 1
    for cal, n in sorted(seen.items()):
        if n > 1:
            violations.append({
                "code": "E_KC0_REPEAT_CALL",
                "fn": entry,
                "detail": "%s 被调用 %d 次：按函数名去重会丢掉程序行为，拒绝转换"
                          % (cal, n),
            })
    return violations


def kc0_check(fns, entry, stages, glob_names=None):
    violations = []
    violations.extend(kc0_entry_shape(fns, entry, glob_names))
    kinds_bad = ("GotoStmt", "IndirectGotoStmt", "GCCAsmStmt",
                 "CXXTryStmt", "LambdaExpr", "StmtExpr")
    for name in stages + [entry]:
        info = fns.get(name)
        if not info:
            continue
        bad = has_kind(info["body"], kinds_bad)
        if bad:
            violations.append({
                "code": "E_KC0_STMT",
                "fn": name,
                "detail": "禁止语句: " + ",".join(sorted(set(bad))),
            })
        vla = type_is_vla(info["node"])
        if vla:
            violations.append({
                "code": "E_KC0_VLA",
                "fn": name,
                "detail": "变长数组",
            })
        for call in direct_calls_in_order(info["body"]):
            if call["indirect"]:
                violations.append({
                    "code": "E_KC0_INDIRECT_CALL",
                    "fn": name,
                    "detail": "间接调用（函数指针）",
                })
                continue
            cal = call["callee"]
            if cal in FORBIDDEN_CALLS:
                violations.append({
                    "code": "E_KC0_FORBIDDEN_CALL",
                    "fn": name,
                    "detail": cal,
                })
            if any(a is None for a in call["args"]):
                violations.append({
                    "code": "E_KC0_OPAQUE_ARG",
                    "fn": name,
                    "detail": "%s(...) 实参无法还原为常量或标识符" % cal,
                })
    # 简单递归：阶段函数调用自身
    for name in stages:
        info = fns.get(name)
        if not info:
            continue
        for call in direct_calls_in_order(info["body"]):
            if call["callee"] == name:
                violations.append({
                    "code": "E_KC0_RECURSION",
                    "fn": name,
                    "detail": "自递归",
                })
    return violations


def format_call(callee, args):
    if not args:
        return "%s()" % callee
    return "%s(%s)" % (callee, ", ".join(args))


def apply_binds(call_expr, binds):
    out = call_expr
    for k, v in binds.items():
        out = re.sub(r"\b%s\b" % re.escape(k), v, out)
    return out


def emit_kmod(out_dir, src, stages_doc, binds):
    os.makedirs(out_dir, exist_ok=True)
    with io.open(src, encoding="utf-8") as f:
        body = f.read()
    body = body.replace(
        "#include <stdint.h>",
        "#ifdef __KERNEL__\n#include <linux/types.h>\n"
        "typedef u8 uint8_t; typedef u16 uint16_t; typedef u32 uint32_t;\n"
        "typedef u64 uint64_t; typedef s8 int8_t; typedef s16 int16_t;\n"
        "typedef s32 int32_t; typedef s64 int64_t;\n"
        "#else\n#include <stdint.h>\n#endif\n",
        1,
    )
    compute_path = os.path.join(out_dir, "kfusion_compute.c")
    with io.open(compute_path, "w", encoding="utf-8") as f:
        f.write("/* 由 stage_extract 从 %s 机械改头文件，计算体未手写复制 */\n"
                % os.path.basename(src))
        f.write(body)

    entry = stages_doc["task"]["entry"]
    params = stages_doc.get("entry_params") or []
    arglist = []
    for p in params:
        name = p["name"]
        if name in binds:
            arglist.append(binds[name])
        elif "int" in (p.get("type") or ""):
            arglist.append("0")
        else:
            arglist.append("0")
    call = "%s(%s)" % (entry, ", ".join(arglist))
    period = int(float(stages_doc["task"].get("period_us") or 1000))
    reset = stages_doc.get("reset")

    driver = []
    A = driver.append
    A("/* 由 stage_extract 生成：同一份 compute，kthread 周期调用入口。")
    A(" * 这不是 WCET 证据，也不是 LET 宿主；只证明计算体可进内核链接。 */")
    A("#include <linux/module.h>")
    A("#include <linux/kthread.h>")
    A("#include <linux/delay.h>")
    A("#include <linux/types.h>")
    A("typedef s32 int32_t;")
    A("")
    A("extern void %s(%s);" % (
        entry,
        ", ".join(p.get("type") or "int" for p in params) or "void"))
    if reset:
        A("extern void %s(void);" % reset)
    A("")
    A("static struct task_struct *g_task;")
    A("static unsigned long g_cycles;")
    A("static int cycle_thread(void *unused)")
    A("{")
    if reset:
        A("\t%s();" % reset)
    A("\twhile (!kthread_should_stop()) {")
    A("\t\t%s;" % call)
    A("\t\tg_cycles++;")
    A("\t\tusleep_range(%d, %d);" % (period, period))
    A("\t}")
    A("\treturn 0;")
    A("}")
    A("")
    A("static int __init kfusion_cycle_init(void)")
    A("{")
    A('\tg_task = kthread_run(cycle_thread, NULL, "kfusion_cycle");')
    A("\treturn PTR_ERR_OR_ZERO(g_task);")
    A("}")
    A("")
    A("static void __exit kfusion_cycle_exit(void)")
    A("{")
    A("\tif (!IS_ERR_OR_NULL(g_task))")
    A("\t\tkthread_stop(g_task);")
    A('\tpr_info("kfusion_cycle: cycles=%lu (not a WCET)\\n", g_cycles);')
    A("}")
    A("")
    A("module_init(kfusion_cycle_init);")
    A("module_exit(kfusion_cycle_exit);")
    A('MODULE_LICENSE("GPL");')
    A('MODULE_DESCRIPTION("K-Fusion Gate 1 generated cycle (same compute)");')
    A("")
    drv_path = os.path.join(out_dir, "kfusion_cycle.c")
    with io.open(drv_path, "w", encoding="utf-8") as f:
        f.write("\n".join(driver) + "\n")

    mk = []
    mk.append("# 由 stage_extract 生成。板上: make -C /lib/modules/$(uname -r)/build M=$PWD modules")
    mk.append("KERNELDIR ?= /lib/modules/$(shell uname -r)/build")
    mk.append("obj-m := kfusion_cycle.o")
    mk.append("kfusion_cycle-y := kfusion_cycle_mod.o kfusion_compute.o")
    mk.append("ccflags-y += -D__KERNEL__")
    mk.append("")
    mk.append("# 把生成的 kfusion_cycle.c 当成 _mod，避免与模块名撞 obj")
    mk.append("$(obj)/kfusion_cycle_mod.o: $(src)/kfusion_cycle.c")
    mk.append("")
    with io.open(os.path.join(out_dir, "Makefile"), "w", encoding="utf-8") as f:
        f.write("ifneq ($(KERNELRELEASE),)\n")
        f.write("obj-m := kfusion_g1.o\n")
        f.write("kfusion_g1-y := kfusion_cycle.o kfusion_compute.o\n")
        f.write("ccflags-y += -D__KERNEL__\n")
        f.write("else\n")
        f.write("KERNELDIR ?= /lib/modules/$(shell uname -r)/build\n")
        f.write("PWD := $(shell pwd)\n")
        f.write("all:\n")
        f.write("\t$(MAKE) -C $(KERNELDIR) M=$(PWD) modules\n")
        f.write("clean:\n")
        f.write("\t$(MAKE) -C $(KERNELDIR) M=$(PWD) clean\n")
        f.write("endif\n")

    return {"compute": compute_path, "driver": drv_path,
            "makefile": os.path.join(out_dir, "Makefile")}


def main():
    ap = argparse.ArgumentParser(description="K-C0 周期 C → stages.json")
    ap.add_argument("source")
    ap.add_argument("-o", "--out", required=True, help="stages.json 输出路径")
    ap.add_argument("--clang", default=os.environ.get("CLANG", "clang-16"))
    ap.add_argument("--clang-arg", action="append", default=[],
                    help="额外传给 clang 的参数（可重复）")
    ap.add_argument("--entry", default=None)
    ap.add_argument("--period-us", type=float, default=None)
    ap.add_argument("--deadline-us", type=float, default=None)
    ap.add_argument("--step-us", type=float, default=None,
                    help="设计事实：等距相位。不给且源注释也没有 → 相位未知")
    ap.add_argument("--offset-us", default=None,
                    help="逗号分隔的每阶段相位，覆盖 step_us")
    ap.add_argument("--name", default=None)
    ap.add_argument("--bind", action="append", default=[],
                    help="入口形参绑定，如 setpoint=1000（生成调用/内核用）")
    ap.add_argument("--emit-kmod", default=None,
                    help="写出 Linux kthread 模块源（同一份计算体）")
    ap.add_argument("--keep-ast", default=None, help="保留 clang JSON AST")
    args = ap.parse_args()

    src = os.path.abspath(args.source)
    if not os.path.isfile(src):
        raise SystemExit("找不到源文件 %s" % src)
    with io.open(src, encoding="utf-8") as f:
        text = f.read()
    meta = parse_task_comment(text)

    binds = {}
    for spec in args.bind:
        if "=" not in spec:
            raise SystemExit("--bind 格式 name=value：%r" % spec)
        k, v = spec.split("=", 1)
        binds[k.strip()] = v.strip()

    ast_path = args.keep_ast or (args.out + ".ast.json")
    ast_dir = os.path.dirname(os.path.abspath(ast_path)) or "."
    if not os.path.isdir(ast_dir):
        os.makedirs(ast_dir)
    tu = dump_ast(args.clang, src, args.clang_arg, ast_path)
    fns = functions_with_body(tu)
    globs = file_scope_globals(tu)

    entry = args.entry or meta.get("entry")
    if not entry:
        for cand in ENTRY_CANDIDATES:
            if cand in fns:
                entry = cand
                break
    if not entry or entry not in fns:
        raise SystemExit(
            "找不到周期入口（给 --entry 或在源里写 kfusion:task entry=...）。"
            " 已见函数: %s" % ", ".join(sorted(fns)))

    calls = direct_calls_in_order(fns[entry]["body"])
    stages = []
    call_map = {}
    for c in calls:
        if c["indirect"]:
            continue
        cal = c["callee"]
        if cal not in fns:
            continue
        if cal == entry:
            continue
        if cal.endswith("_reset") or cal == "loop_reset":
            continue
        # 重复调用另行由 E_KC0_REPEAT_CALL 拒绝；这里仍记第一次调用式供排错骨架。
        if cal not in stages:
            stages.append(cal)
            call_map[cal] = format_call(cal, c["args"])

    reset = None
    for name in fns:
        if name in ("loop_reset",) or name.endswith("_reset"):
            reset = name
            break

    violations = kc0_check(fns, entry, stages,
                           glob_names=[g["name"] for g in globs])
    # 入口调用了未在本 TU 定义、且不在允许名单外的函数
    defined = set(fns)
    for c in calls:
        cal = c["callee"]
        if cal and cal not in defined and cal not in FORBIDDEN_CALLS:
            violations.append({
                "code": "E_KC0_EXTERNAL_CALL",
                "fn": entry,
                "detail": cal,
            })
        if cal in FORBIDDEN_CALLS:
            violations.append({
                "code": "E_KC0_FORBIDDEN_CALL",
                "fn": entry,
                "detail": cal,
            })

    if violations:
        print("!! K-C0 失败闭合，不生成用户态/内核产物：", file=sys.stderr)
        for v in violations:
            print("   [%s] %s: %s" % (v["code"], v["fn"], v["detail"]),
                  file=sys.stderr)
        print("骨架已写出（仅供排错）: %s" % args.out, file=sys.stderr)
        # 仍写骨架方便对照，但绝不 emit-kmod
        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        with io.open(args.out, "w", encoding="utf-8") as f:
            json.dump({
                "extracted": False,
                "kc0": {"ok": False, "violations": violations},
                "source": src,
                "task": {"entry": entry},
                "stages": [],
            }, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return 2

    if not stages:
        raise SystemExit("入口 %s 没有抽到任何已定义的阶段调用（K-C0 要求直接调用）"
                         % entry)

    def as_num(v):
        if v is None:
            return None
        x = float(v)
        if abs(x - round(x)) < 1e-9:
            return int(round(x))
        return x

    period = as_num(args.period_us if args.period_us is not None
                    else meta.get("period_us"))
    deadline = as_num(args.deadline_us if args.deadline_us is not None
                      else meta.get("deadline_us"))
    step = as_num(args.step_us if args.step_us is not None
                  else meta.get("step_us"))
    task_name = args.name or meta.get("name") or os.path.splitext(
        os.path.basename(src))[0]

    offsets = None
    if args.offset_us:
        offsets = [float(x) for x in args.offset_us.split(",") if x.strip()]
        if len(offsets) != len(stages):
            raise SystemExit("--offset-us 个数 (%d) 与阶段数 (%d) 不一致"
                             % (len(offsets), len(stages)))

    stage_list = []
    for i, name in enumerate(stages):
        st = {
            "name": name,
            "fn": name,
            "depends_on": [stages[i - 1]] if i else [],
            "context": "thread",
            "call": apply_binds(call_map[name], binds),
        }
        if offsets is not None:
            st["offset_us"] = as_num(offsets[i])
        stage_list.append(st)

    doc = {
        "version": 1,
        "extracted": True,
        "source": src,
        "kc0": {
            "ok": not violations,
            "violations": violations,
        },
        "task": {
            "name": task_name,
            "entry": entry,
            "period_us": period,
            "deadline_us": deadline if deadline is not None else period,
            "step_us": step,
            "notes": "由 Clang AST 抽取；相位来自源注释或命令行，不是分析猜测",
        },
        "stages": stage_list,
        "globals": [g["name"] for g in globs],
        "globals_digest": digest_vars(globs),
        "globals_typed": globs,
        "entry_params": fns[entry]["params"],
        "reset": reset,
        "calls": {k: apply_binds(v, binds) for k, v in call_map.items()},
        "binds": binds,
        "observations": [],
    }

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with io.open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("extracted %d stages from %s:" % (len(stages), entry))
    for i, name in enumerate(stages):
        ph = ""
        if offsets is not None:
            ph = " @%.0fµs" % offsets[i]
        elif step is not None:
            ph = " @%.0fµs" % (step * i)
        print("  %d. %s%s  %s" % (i, name, ph, doc["calls"][name]))
    print("wrote %s" % args.out)

    if args.emit_kmod:
        paths = emit_kmod(args.emit_kmod, src, doc, binds)
        print("kmod sources:")
        for k, p in paths.items():
            print("  %s %s" % (k, p))

    if not args.keep_ast and os.path.isfile(ast_path) and ast_path.endswith(
            ".ast.json"):
        try:
            os.remove(ast_path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
