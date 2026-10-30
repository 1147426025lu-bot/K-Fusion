#!/usr/bin/env python3
"""plan_extend__计划扩展.py — 把 STKP 的 plan.json 补成「能指导代码生成」的计划。

为什么要单独一步：
  现在的 plan.json 只有 `groups[].{members,domain,release_phase_us}`。它能画图、
  能算成本，但**不足以生成代码**：谁负责释放、跨域传什么、持久状态归谁、
  哪个相位是硬约束——这些信息不写进计划，生成器就只能靠猜，而猜出来的东西
  没法验证。审查第 5 条要的正是这一层。

本脚本做两件事：
  1. `extend`：从 plan + teir 推出缺失字段并写新计划（不覆盖输入）；
  2. `verify`：对**最终计划**做结构与语义校验，不过就拒绝（fail-closed）。

字段设计（新增部分）：
  version            计划格式版本（与 STKP 的 version 区分）
  task/period_us/deadline_us   任务身份与时间契约
  groups[].phase_us           释放相位（µs，绝对）
  groups[].phase_binding      "required" = 时间契约要求不得改动；
                              "advisory" = 仅供优化，可被融合调整
  state_ownership             {变量: "user"|"kernel"} —— 跨拍状态只允许单一所有者
  handoffs[]                  {from,to,fields,method} —— 只在域边界出现
  release_owner               "user_driver"|"kernel_host" —— 同一任务只能有一个
  runtime                     {cpu, sched, priority}
  provenance                  生成来源（脚本版本 + 输入哈希），便于复现

校验规则（verify，任一不过就退出非 0）：
  V1 版本/必需字段齐全；未知顶层字段一律拒绝（防止拼错字段名被静默忽略）
  V2 每组 domain ∈ {user, kernel}，phase_us ≥ 0 且 < period
  V3 release_owner 唯一，且与「是否存在 kernel 组」一致
  V4 state_ownership 覆盖 teir 里所有跨拍状态；同一变量不得有两个所有者
  V5 handoffs 必须恰好对应域边界（相邻两组域不同处）；字段不得跨域重复
  V6 runtime.sched ∈ {cfs, fifo}；fifo 时 priority ∈ [1,99]
  V7 相位硬约束：phase_binding=required 的组不得被融合（组内成员数必须等于原始阶段数）

用法：
  plan_extend__计划扩展.py extend --plan p.json --teir t.teir.json -o plan_v2.json
  plan_extend__计划扩展.py verify --plan plan_v2.json [--teir t.teir.json]
  plan_extend__计划扩展.py selftest
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys

PLAN_VERSION = 2
ALLOWED_TOP = {
    "version", "task", "period_us", "deadline_us", "groups", "state_ownership",
    "handoffs", "release_owner", "runtime", "provenance", "meta", "order",
    "domains", "cost", "schedule", "chosen", "note",
}
DOMAINS = ("user", "kernel")


def loadj(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def norm_domain(d):
    """把各处的域写法归一：linux_userspace→user, linux_kernel→kernel。"""
    if d in ("user", "linux_userspace"):
        return "user"
    if d in ("kernel", "linux_kernel"):
        return "kernel"
    return d


def norm_var(v):
    """teir 里的变量名带 @ 前缀（@g_integ），计划里不该带。统一去掉。"""
    return v[1:] if isinstance(v, str) and v.startswith("@") else v


def teir_stages(teir):
    """teir 的阶段表：新格式叫 nodes，老格式叫 stages。"""
    return (teir.get("stages") or teir.get("nodes") or [])


def teir_task(teir):
    t = teir.get("task") or {}
    return t if isinstance(t, dict) else {}


def teir_self_carry(teir):
    """teir 里声明的跨拍状态（多种写法全部收进来，名字去掉 @）。"""
    out = []

    def add(name):
        name = norm_var(name)
        if name and name not in out:
            out.append(name)

    for k in ("self_carry", "state_vars", "persistent"):
        for v in (teir.get(k) or []):
            add(v if isinstance(v, str) else (v.get("name") or v.get("var")))
    # persistent_state: [{region: "@g_integ", writer: "stage_pid", kind: "carry"}]
    for v in (teir.get("persistent_state") or []):
        if isinstance(v, dict):
            add(v.get("region") or v.get("name"))
        else:
            add(v)
    for st in teir_stages(teir):
        for v in (st.get("persistent") or []) + (st.get("self_carry") or []):
            add(v)
    return out


def build_extended(plan, teir, src_hash=None, teir_hash=None):
    """在 plan 上补字段。**不猜**：能从输入推的才填，推不出来的留 None 让 verify 报。"""
    groups = []
    for g in plan.get("groups") or []:
        dom = norm_domain(g.get("domain"))
        phase = g.get("release_phase_us")
        if phase is None:
            phase = g.get("phase_us")
        groups.append({
            "id": g.get("id"),
            "members": list(g.get("members") or []),
            "domain": dom,
            "phase_us": float(phase) if phase is not None else None,
            # 默认按时间契约要求（required）：把相位改成 advisory 必须显式声明，
            # 否则融合会把「必须在 400µs 更新输出」的阶段提前，语义就变了。
            "phase_binding": g.get("phase_binding") or "required",
        })

    meta = plan.get("meta") or {}
    tk = teir_task(teir)
    period = plan.get("period_us") or meta.get("period_us") or tk.get("period_us")
    deadline = (plan.get("deadline_us") or meta.get("deadline_us")
                or tk.get("deadline_us") or period)
    task_name = (plan.get("task") or meta.get("task") or tk.get("name")
                 or tk.get("entry"))

    has_kernel = any(g["domain"] == "kernel" for g in groups)
    # 释放负责人：有内核宿主时由内核定时器负责；纯用户态/UKU 由用户驱动负责。
    # UKU 的中段虽然进内核，但**释放仍由用户线程发起**（同步 ioctl），
    # 所以不能因为存在 kernel 组就判成 kernel_host。
    owner = plan.get("release_owner")
    if owner is None:
        owner = "kernel_host" if (has_kernel and meta.get("host") == "hrtimer") \
            else "user_driver"

    carry = teir_self_carry(teir)
    ownership = dict(plan.get("state_ownership") or {})
    for v in carry:
        if v in ownership:
            continue
        # 状态归「持有它的阶段」所在的域。持有 = 该阶段在 self_carry 里声明了它，
        # 或者它写这个变量（写者即所有者）。两处都推不出唯一域就留 None，
        # 让 verify 报错——不能默认成 user，那等于偷偷替编译器做决定。
        doms = []
        for g in groups:
            for st in teir_stages(teir):
                if st.get("name") not in (g["members"] or []):
                    continue
                vars_here = ([norm_var(x) for x in (st.get("persistent") or [])]
                             + [norm_var(x) for x in (st.get("self_carry") or [])]
                             + [norm_var(x) for x in (st.get("writes") or [])])
                if v in vars_here:
                    doms.append(g["domain"])
        ownership[v] = doms[-1] if doms and len(set(doms)) == 1 else None

    handoffs = []
    for i in range(1, len(groups)):
        a, b = groups[i - 1], groups[i]
        if a["domain"] == b["domain"]:
            continue
        # 跨域字段：a 里阶段写的、b 里阶段读的全局量
        wa, rb = [], []
        for st in teir_stages(teir):
            if st.get("name") in (a["members"] or []):
                wa += [norm_var(x) for x in (st.get("writes") or [])]
            if st.get("name") in (b["members"] or []):
                rb += [norm_var(x) for x in (st.get("reads") or [])]
        fields = []
        for v in wa + rb:
            if v not in fields and v not in ownership:
                fields.append(v)
        handoffs.append({
            "from": a["domain"], "to": b["domain"],
            "fields": fields,
            "method": "ioctl" if has_kernel else "shared_memory",
        })

    doc = dict(plan)
    doc.update({
        "version": PLAN_VERSION,
        "task": task_name,
        "period_us": float(period) if period is not None else None,
        "deadline_us": float(deadline) if deadline is not None else None,
        "groups": groups,
        "state_ownership": ownership,
        "handoffs": handoffs,
        "release_owner": owner,
        "runtime": dict(plan.get("runtime") or {
            "cpu": 3, "sched": "fifo", "priority": 80, "timer": "absolute",
        }),
        "provenance": {
            "generator": "plan_extend__计划扩展.py",
            "plan_version": PLAN_VERSION,
            "input_plan_sha256": src_hash,
            "input_teir_sha256": teir_hash,
        },
    })
    return doc


def verify(doc, teir=None):
    """返回 (errors, warnings)。errors 非空即拒绝。"""
    errs, warns = [], []

    for k in doc:
        if k not in ALLOWED_TOP:
            errs.append("V1 未知顶层字段 %r（拼错会被静默忽略，所以直接拒）" % k)

    if doc.get("version") != PLAN_VERSION:
        errs.append("V1 version 应为 %d，实际 %r" % (PLAN_VERSION, doc.get("version")))
    for k in ("task", "period_us", "deadline_us", "groups", "state_ownership",
              "handoffs", "release_owner", "runtime"):
        if k not in doc or doc[k] is None:
            errs.append("V1 缺字段 %r" % k)
    if errs:
        return errs, warns

    period = float(doc["period_us"])
    deadline = float(doc["deadline_us"])
    if deadline > period:
        warns.append("deadline(%s) > period(%s)：跨拍期限，当前生成器只支持 D≤T"
                     % (deadline, period))

    owner = doc["release_owner"]
    if owner not in ("user_driver", "kernel_host"):
        errs.append("V3 release_owner 非法：%r" % owner)

    has_kernel = False
    seen_ids = set()
    for g in doc["groups"]:
        gid = g.get("id")
        if gid in seen_ids:
            errs.append("V2 组 id 重复：%r" % gid)
        seen_ids.add(gid)
        if g.get("domain") not in DOMAINS:
            errs.append("V2 组 %r domain 非法：%r" % (gid, g.get("domain")))
        if g.get("domain") == "kernel":
            has_kernel = True
        ph = g.get("phase_us")
        if ph is None or float(ph) < 0 or float(ph) >= period:
            errs.append("V2 组 %r phase_us 越界：%r（要求 0 ≤ ph < %s）"
                        % (gid, ph, period))
        if g.get("phase_binding") not in ("required", "advisory"):
            errs.append("V7 组 %r phase_binding 非法：%r"
                        % (gid, g.get("phase_binding")))
        if g.get("phase_binding") == "required" and not (g.get("members") or []):
            errs.append("V7 组 %r 是硬相位但没有成员" % gid)

    if owner == "kernel_host" and not has_kernel:
        errs.append("V3 release_owner=kernel_host 但计划里没有 kernel 组")
    if owner == "user_driver" and has_kernel:
        # UKU 形态是合法的：释放由用户线程发起，中段同步进内核。
        warns.append("V3 有 kernel 组但 release_owner=user_driver"
                     "（UKU 同步形态）；必须确保宿主不再另起定时器")

    own = doc["state_ownership"]
    carry = teir_self_carry(teir or {})
    for v in carry:
        if v not in own:
            errs.append("V4 跨拍状态 %r 没有声明所有者" % v)
    for v, o in own.items():
        if o not in DOMAINS:
            errs.append("V4 状态 %r 所有者非法：%r（必须是 user/kernel）" % (v, o))

    # V5 域边界必须有 handoff，且不能多出无对应边界的 handoff
    boundaries = []
    gs = doc["groups"]
    for i in range(1, len(gs)):
        if gs[i - 1]["domain"] != gs[i]["domain"]:
            boundaries.append((gs[i - 1]["domain"], gs[i]["domain"]))
    got = [(h.get("from"), h.get("to")) for h in doc["handoffs"]]
    for b in boundaries:
        if b not in got:
            errs.append("V5 域边界 %s→%s 没有 handoff（跨域数据会丢）" % b)
    for h in doc["handoffs"]:
        if (h.get("from"), h.get("to")) not in boundaries:
            errs.append("V5 handoff %s→%s 没有对应的域边界"
                        % (h.get("from"), h.get("to")))
        if h.get("from") not in DOMAINS or h.get("to") not in DOMAINS:
            errs.append("V5 handoff 域非法：%r" % (h,))
        if not h.get("fields"):
            warns.append("V5 handoff %s→%s 没有字段：如果确实不需要传数据可忽略"
                         % (h.get("from"), h.get("to")))

    rt = doc["runtime"]
    if rt.get("sched") not in ("cfs", "fifo"):
        errs.append("V6 runtime.sched 非法：%r" % rt.get("sched"))
    if rt.get("sched") == "fifo":
        pr = rt.get("priority")
        if not isinstance(pr, int) or not (1 <= pr <= 99):
            errs.append("V6 fifo 时 priority 必须在 1..99：%r" % pr)
    if not isinstance(rt.get("cpu"), int) or rt["cpu"] < 0:
        errs.append("V6 runtime.cpu 非法：%r" % rt.get("cpu"))

    return errs, warns


def cmd_extend(a):
    plan = loadj(a.plan)
    teir = loadj(a.teir) if a.teir else {}
    doc = build_extended(plan, teir,
                         src_hash=sha256_file(a.plan),
                         teir_hash=sha256_file(a.teir) if a.teir else None)
    errs, warns = verify(doc, teir)
    for w in warns:
        print("⚠ %s" % w)
    if errs and not a.force:
        sys.stderr.write("计划校验失败（%d 项）：\n  %s\n"
                         % (len(errs), "\n  ".join(errs)))
        return 3
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("写出 %s（%d 组，owner=%s，状态 %d 个，handoff %d 个）"
          % (a.out, len(doc["groups"]), doc["release_owner"],
             len(doc["state_ownership"]), len(doc["handoffs"])))
    return 0


def cmd_verify(a):
    doc = loadj(a.plan)
    teir = loadj(a.teir) if a.teir else None
    errs, warns = verify(doc, teir)
    for w in warns:
        print("⚠ %s" % w)
    if errs:
        sys.stderr.write("拒绝（%d 项）：\n  %s\n" % (len(errs), "\n  ".join(errs)))
        return 1
    print("计划校验通过：%d 组，owner=%s" % (len(doc["groups"]), doc["release_owner"]))
    return 0


def _fixture(**over):
    doc = {
        "version": PLAN_VERSION, "task": "t", "period_us": 1000.0,
        "deadline_us": 1000.0,
        "groups": [
            {"id": "g0", "members": ["sample"], "domain": "user",
             "phase_us": 0.0, "phase_binding": "required"},
            {"id": "g1", "members": ["pid"], "domain": "kernel",
             "phase_us": 200.0, "phase_binding": "required"},
            {"id": "g2", "members": ["sink"], "domain": "user",
             "phase_us": 400.0, "phase_binding": "required"},
        ],
        "state_ownership": {"integ": "kernel"},
        "handoffs": [
            {"from": "user", "to": "kernel", "fields": ["in"], "method": "ioctl"},
            {"from": "kernel", "to": "user", "fields": ["out"], "method": "ioctl"},
        ],
        "release_owner": "user_driver",
        "runtime": {"cpu": 3, "sched": "fifo", "priority": 80},
        "provenance": {"generator": "selftest"},
    }
    doc.update(over)
    return doc


def selftest():
    ok = 0

    def chk(name, cond):
        nonlocal ok
        if not cond:
            raise SystemExit("FAIL: %s" % name)
        ok += 1
        print("  ✓ %s" % name)

    teir = {"stages": [
        {"name": "sample", "writes": ["in"], "self_carry": []},
        {"name": "pid", "reads": ["in"], "writes": ["out"], "self_carry": ["integ"]},
        {"name": "sink", "reads": ["out"]},
    ]}

    e, _ = verify(_fixture(), teir)
    chk("正常计划通过", not e)

    e, _ = verify(_fixture(version=1), teir)
    chk("版本不符被拒", any("V1" in x for x in e))

    e, _ = verify(_fixture(bogus_field=1), teir)
    chk("未知字段被拒", any("未知顶层字段" in x for x in e))

    bad = _fixture()
    bad["groups"][0]["phase_us"] = 1200.0
    e, _ = verify(bad, teir)
    chk("相位越界被拒", any("V2" in x for x in e))

    e, _ = verify(_fixture(release_owner="nobody"), teir)
    chk("释放负责人非法被拒", any("V3" in x for x in e))

    all_user = _fixture(release_owner="kernel_host")
    for g in all_user["groups"]:
        g["domain"] = "user"
    all_user["handoffs"] = []
    e, _ = verify(all_user, teir)
    chk("无 kernel 组却声称 kernel_host 被拒", any("V3" in x for x in e))

    bad = _fixture(state_ownership={})
    e, _ = verify(bad, teir)
    chk("跨拍状态缺所有者被拒", any("V4" in x for x in e))

    bad = _fixture(handoffs=[{"from": "user", "to": "kernel", "fields": ["in"]}])
    e, _ = verify(bad, teir)
    chk("缺域边界 handoff 被拒", any("V5" in x for x in e))

    bad = _fixture(runtime={"cpu": 3, "sched": "fifo", "priority": 0})
    e, _ = verify(bad, teir)
    chk("fifo 优先级越界被拒", any("V6" in x for x in e))

    bad = _fixture()
    bad["groups"][0]["phase_binding"] = "whatever"
    e, _ = verify(bad, teir)
    chk("phase_binding 非法被拒", any("V7" in x for x in e))

    # extend：能从 teir 推出跨拍状态与 handoff 字段
    doc = build_extended(
        {"version": 1, "meta": {"task": "t", "period_us": 1000},
         "groups": [{"id": "g0", "members": ["sample"], "domain": "linux_userspace",
                     "release_phase_us": 0},
                    {"id": "g1", "members": ["pid"], "domain": "linux_kernel",
                     "release_phase_us": 200},
                    {"id": "g2", "members": ["sink"], "domain": "linux_userspace",
                     "release_phase_us": 400}]}, teir)
    e, _ = verify(doc, teir)
    chk("extend 产物能通过校验", not e)
    chk("extend 推出状态所有者", doc["state_ownership"].get("integ") == "kernel")
    chk("extend 推出两条域边界 handoff", len(doc["handoffs"]) == 2)

    print("\nselftest 通过 %d 项" % ok)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("extend")
    p1.add_argument("--plan", required=True)
    p1.add_argument("--teir", default=None)
    p1.add_argument("-o", "--out", required=True)
    p1.add_argument("--force", action="store_true", help="校验失败也写出（仅调试）")

    p2 = sub.add_parser("verify")
    p2.add_argument("--plan", required=True)
    p2.add_argument("--teir", default=None)

    sub.add_parser("selftest")

    a = ap.parse_args()
    if a.cmd == "extend":
        return cmd_extend(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    if a.cmd == "selftest":
        return selftest()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
