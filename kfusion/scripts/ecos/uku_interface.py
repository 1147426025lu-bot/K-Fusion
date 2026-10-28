# -*- coding: utf-8 -*-
"""UKU 后端合法性门禁（PR-5B.1）。

第一版只允许恰好三组 U | K | U、一个内核组、ioctl 邮箱容量内的标量 int32。
不通过则不得 deployment_ready，生成器必须拒绝，不得截断或猜 g_in/g_out。
"""
from __future__ import print_function

UKU_MAX = 8
U = "linux_userspace"
K = "linux_kernel"
SCALAR = frozenset(("int32_t", "int32", "i32", "uint32_t", "uint32", "u32"))


def region_name(raw):
    if not raw:
        return None
    s = raw[1:] if str(raw).startswith("@") else str(raw)
    s = s.strip()
    return s or None


def kernel_groups(plan):
    return [g for g in (plan.get("groups") or [])
            if g.get("domain") == K]


def shape_errors(plan):
    groups = plan.get("groups") or []
    if len(groups) != 3:
        return [{"code": "E_UKU_GROUP_SHAPE",
                 "msg": "UKU v1 只允许恰好三组 U|K|U，实际 %d 组" % len(groups)}]
    doms = [g.get("domain") for g in groups]
    if doms != [U, K, U]:
        return [{"code": "E_UKU_GROUP_SHAPE",
                 "msg": "UKU v1 域序必须是 U|K|U，实际 %s" % doms}]
    kg = kernel_groups(plan)
    if len(kg) != 1:
        return [{"code": "E_UKU_GROUP_SHAPE",
                 "msg": "UKU v1 中间恰好一个内核组，实际 %d" % len(kg)}]
    return []


def _type_of(teir, name):
    types = teir.get("region_types") or {}
    if name in types:
        return str(types[name])
    for n in teir.get("nodes") or []:
        tmap = n.get("region_types") or n.get("types") or {}
        if name in tmap:
            return str(tmap[name])
    return None


def _unsupported_type(teir, name):
    if not name:
        return "E_UKU_TYPE", "空区域名"
    if "." in name or "[" in name or "*" in name:
        return "E_UKU_TYPE", "%s 像结构体/数组/指针，UKU v1 只支持标量" % name
    ty = _type_of(teir, name)
    if ty is None:
        # 无声明时只接受简单标识符（典型 g_*）
        if not name.replace("_", "").isalnum() or name[0].isdigit():
            return "E_UKU_TYPE", "%s 不是合法标量名" % name
        return None
    if ty not in SCALAR:
        return "E_UKU_TYPE", "%s 类型 %s 不受支持" % (name, ty)
    return None


def _call_has_args(expr):
    if not expr or "(" not in expr:
        return False
    inner = expr[expr.find("(") + 1:expr.rfind(")")]
    return bool(inner.strip())


def collect_kernel_io(plan, teir):
    nodes = {n["name"]: n for n in (teir.get("nodes") or [])}
    kg = kernel_groups(plan)
    kmem = []
    for g in kg:
        kmem.extend(g.get("members") or [])
    reads, writes = [], []
    for m in kmem:
        n = nodes.get(m) or {}
        for r in n.get("reads") or []:
            x = region_name(r)
            if x and x not in reads:
                reads.append(x)
        for r in n.get("writes") or []:
            x = region_name(r)
            if x and x not in writes:
                writes.append(x)
    kset = set(kmem)
    for e in teir.get("edges") or []:
        if e.get("kind") not in ("data", "call"):
            continue
        frm, to = e.get("from"), e.get("to")
        for r in e.get("regions") or []:
            x = region_name(r)
            if not x:
                continue
            if frm not in kset and to in kset and x not in reads:
                reads.append(x)
            if frm in kset and to not in kset and x not in writes:
                writes.append(x)
    return kmem, reads, writes


def window_errors(contract):
    if not contract:
        return []
    ctr = contract.get("timing_constraints") or {}
    bad = []
    for key in ("sample", "actuator", "output"):
        w = ctr.get(key)
        if not w:
            continue
        items = w if isinstance(w, list) else [w]
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("earliest_us") is not None or it.get("latest_us") is not None:
                bad.append({
                    "code": "E_UKU_WINDOW",
                    "msg": ("UKU v1 尚未验证相位窗口，拒绝 %s 约束 %s"
                            % (key, it.get("stage") or "")),
                })
    return bad


def call_errors(plan, teir, calls):
    calls = calls or {}
    nodes = {n["name"]: n for n in (teir.get("nodes") or [])}
    kmem = []
    for g in kernel_groups(plan):
        kmem.extend(g.get("members") or [])
    bad = []
    for m in kmem:
        expr = calls.get(m)
        if expr is None:
            expr = (nodes.get(m) or {}).get("call") or (nodes.get(m) or {}).get("expr")
        if _call_has_args(expr):
            bad.append({
                "code": "E_UKU_ABI",
                "msg": "内核阶段 %s 带参数 %s，UKU v1 无调用 ABI" % (m, expr),
            })
    return bad


def check(plan, teir, contract=None, calls=None):
    """返回 {ok, errors, interface}。ok 才可 deployment_ready。"""
    errors = []
    errors.extend(shape_errors(plan))
    if errors:
        return {"ok": False, "errors": errors, "interface": None}
    # 相位窗口改由 STKP 串行时间线（Ĉ 同一份 exec）裁剪，不再在此 fail-closed。
    errors.extend(call_errors(plan, teir, calls))
    kmem, reads, writes = collect_kernel_io(plan, teir)
    if not reads or not writes:
        errors.append({
            "code": "E_UKU_NO_REGIONS",
            "msg": "内核组没有可分析的读写区域，拒绝猜测 g_in/g_out",
        })
    if len(reads) > UKU_MAX:
        errors.append({
            "code": "E_UKU_CAPACITY",
            "msg": "内核读 %d 个状态，邮箱上限 %d，拒绝截断" % (len(reads), UKU_MAX),
        })
    if len(writes) > UKU_MAX:
        errors.append({
            "code": "E_UKU_CAPACITY",
            "msg": "内核写 %d 个状态，邮箱上限 %d，拒绝截断" % (len(writes), UKU_MAX),
        })
    for name in reads + writes:
        t = _unsupported_type(teir, name)
        if t:
            errors.append({"code": t[0], "msg": t[1]})
    iface = {
        "adapter": "uku_ioctl",
        "kernel_stages": kmem,
        "copy_in": list(reads),
        "copy_out": list(writes),
        "device": "/dev/kfusion_uku",
        "max": UKU_MAX,
    }
    ok = not errors
    return {"ok": ok, "errors": errors, "interface": iface if ok else None}


def codes(result):
    return [e.get("code") for e in (result.get("errors") or [])]
