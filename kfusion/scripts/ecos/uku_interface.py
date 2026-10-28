# -*- coding: utf-8 -*-
"""UKU 后端合法性门禁（PR-5B.1）。

第一版只允许恰好三组 U | K | U、一个内核组、ioctl 邮箱容量内的标量 int32。
不通过则不得 deployment_ready，生成器必须拒绝，不得截断或猜 g_in/g_out。
"""
from __future__ import print_function

UKU_MAX = 8
U = "linux_userspace"
K = "linux_kernel"
# 生成器固定 extern int32_t；未知类型与 uint32_t 一律拒绝。
UKU_OK_TYPE = frozenset(("int32_t", "int32", "i32"))


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


def _norm_type(ty):
    s = str(ty).strip()
    for w in ("const", "volatile", "restrict"):
        s = s.replace(w, " ")
    return " ".join(s.split())


def _type_of(teir, name):
    types = teir.get("region_types") or {}
    if name in types:
        return str(types[name])
    alt = "@" + name if not str(name).startswith("@") else name
    if alt in types:
        return str(types[alt])
    for n in teir.get("nodes") or []:
        tmap = n.get("region_types") or n.get("types") or {}
        if name in tmap:
            return str(tmap[name])
    for g in teir.get("globals") or []:
        if isinstance(g, dict) and g.get("name") == name:
            return g.get("type")
    return None


def _unsupported_type(teir, name):
    if not name:
        return "E_UKU_TYPE", "空区域名"
    if "." in name or "[" in name or "*" in name:
        return "E_UKU_TYPE", "%s 像结构体/数组/指针，UKU v1 只支持标量" % name
    ty = _type_of(teir, name)
    if ty is None or not str(ty).strip():
        return "E_UKU_TYPE_UNKNOWN", (
            "%s 无 Clang 类型证据，UKU v1 只接受已证明的 int32_t" % name)
    nt = _norm_type(ty)
    if "[" in nt or "*" in nt:
        return "E_UKU_TYPE", "%s 类型 %s 不受支持" % (name, ty)
    if nt not in UKU_OK_TYPE:
        return "E_UKU_TYPE", "%s 类型 %s 不是 int32_t（生成器只声明 int32_t）" % (
            name, ty)
    return None


def kernel_persistent_regions(plan, teir):
    kset = set()
    for g in kernel_groups(plan):
        kset.update(g.get("members") or [])
    found = []
    for n in teir.get("nodes") or []:
        if n.get("name") not in kset:
            continue
        for p in n.get("persistent") or []:
            x = region_name(p)
            if x and x not in found:
                found.append(x)
    for p in teir.get("persistent_state") or []:
        if p.get("writer") in kset:
            x = region_name(p.get("region"))
            if x and x not in found:
                found.append(x)
    return found


def persistent_errors(plan, teir):
    """无状态 UKU 仍拒绝任意 persistent。会话协议只放行一个已证明的 int32 积分。"""
    found = kernel_persistent_regions(plan, teir)
    if not found:
        return []
    if len(found) != 1:
        return [{
            "code": "E_UKU_PERSISTENT",
            "msg": "会话协议只允许一个 int32 积分状态，实际 %s" % ",".join(found),
        }]
    name = found[0]
    t = _unsupported_type(teir, name)
    if t:
        return [{
            "code": "E_UKU_PERSISTENT",
            "msg": "会话状态 %s 不合格：%s" % (name, t[1]),
        }]
    return []


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


def _call_expr(teir, node, calls, name):
    if calls and name in calls:
        return calls[name]
    cmap = (teir or {}).get("calls") or {}
    if name in cmap:
        return cmap[name]
    if node:
        return node.get("call") or node.get("expr")
    return None


def call_errors(plan, teir, calls):
    nodes = {n["name"]: n for n in (teir.get("nodes") or [])}
    kmem = []
    for g in kernel_groups(plan):
        kmem.extend(g.get("members") or [])
    bad = []
    for m in kmem:
        node = nodes.get(m) or {}
        expr = _call_expr(teir, node, calls, m)
        if not expr:
            bad.append({
                "code": "E_UKU_ABI_UNKNOWN",
                "msg": "内核阶段 %s 无调用表达式，STKP 不得假设 %s()" % (m, m),
            })
            continue
        if _call_has_args(expr) or node.get("params"):
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
    perr = persistent_errors(plan, teir)
    errors.extend(perr)
    kmem, reads, writes = collect_kernel_io(plan, teir)
    session_vars = []
    if not perr:
        session_vars = kernel_persistent_regions(plan, teir)
        if len(session_vars) == 1:
            sess = session_vars[0]
            reads = [x for x in reads if x != sess]
            writes = [x for x in writes if x != sess]
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
    sess = session_vars[0] if len(session_vars) == 1 else None
    iface = {
        "adapter": "uku_ioctl",
        "kernel_stages": kmem,
        "copy_in": list(reads),
        "copy_out": list(writes),
        "device": "/dev/kfusion_uku",
        "max": UKU_MAX,
        "session": ({"kind": "integ_i32", "region": sess} if sess else None),
        "session_vars": ([sess] if sess else []),
    }
    ok = not errors
    return {"ok": ok, "errors": errors, "interface": iface if ok else None}


def codes(result):
    return [e.get("code") for e in (result.get("errors") or [])]
