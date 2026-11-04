#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""let_events__LET事件规范化.py — 统一 LET 读写事件形状（开题方案第 1–2 步）

既有 let_decide 产出：
  {kind, stage, at_us, compute_at_us}

本模块在此基础上规范化/校验扩展字段，供 plan_extend 与宿主生成共用：
  vars       共享对象（逻辑读写涉及的全局量）
  path       "thread" | "irq" —— 实际生效路径
             默认：声明了 vars/publish/snap_vars 时用 irq（可生成中断安全桩），否则 thread
  version    版本变量名（可选）
  on_timeout 超时策略：skip_commit | hold_last | fault
  publish    output_commit 的赋值表 [{dst, src}|{dst, op:inc}]
  snap_vars  input_snapshot 的目标快照变量（与 vars 一一对应）

不猜测：缺字段就保留缺省或让 verify 报错。
"""
from __future__ import annotations

import copy

KINDS = ("input_snapshot", "output_commit")
PATHS = ("thread", "irq")
TIMEOUTS = ("skip_commit", "hold_last", "fault")


def _default_path_for(e):
    """有显式读写映射 → 默认可走 irq；否则只能调阶段函数 → thread。"""
    if e.get("path"):
        return e.get("path")
    if e.get("publish") or e.get("snap_vars") or e.get("vars"):
        return "irq"
    return "thread"


def normalize_event(e, default_path=None):
    """返回规范化后的事件副本；无法识别的 kind 原样返回并留给校验报错。"""
    out = copy.deepcopy(e) if isinstance(e, dict) else {}
    kind = out.get("kind")
    if kind in ("snapshot", "read"):
        kind = "input_snapshot"
    if kind in ("commit", "write", "publish"):
        kind = "output_commit"
    out["kind"] = kind
    if "at_us" in out and out["at_us"] is not None:
        out["at_us"] = float(out["at_us"])
    if "compute_at_us" in out and out["compute_at_us"] is not None:
        out["compute_at_us"] = float(out["compute_at_us"])
    vars_ = out.get("vars")
    if vars_ is None:
        out["vars"] = []
    elif isinstance(vars_, str):
        out["vars"] = [vars_]
    else:
        out["vars"] = [str(v) for v in vars_]
    if out.get("snap_vars") is not None and isinstance(out["snap_vars"], str):
        out["snap_vars"] = [out["snap_vars"]]
    path = default_path if default_path is not None else _default_path_for(out)
    path = out.get("path") or path
    if path in ("hardirq", "hard_irq", "hrtimer"):
        path = "irq"
    if path in ("kthread", "worker", "fifo"):
        path = "thread"
    out["path"] = path
    if not out.get("on_timeout"):
        out["on_timeout"] = "skip_commit"
    return out


def normalize_list(events, default_path=None):
    return [normalize_event(e, default_path=default_path)
            for e in (events or [])]


def validate_events(events, period_us=None, stages=None):
    """返回错误字符串列表（空 = 通过）。"""
    errs = []
    stages = set(stages or [])
    period = float(period_us) if period_us is not None else None
    seen = set()
    for i, raw in enumerate(events or []):
        e = normalize_event(raw)
        tag = "let_events[%d]" % i
        if e.get("kind") not in KINDS:
            errs.append("%s kind 非法：%r（要 %s）" % (tag, e.get("kind"), KINDS))
        st = e.get("stage")
        if not st:
            errs.append("%s 缺 stage" % tag)
        elif stages and st not in stages:
            errs.append("%s stage %r 不在计划阶段集合" % (tag, st))
        at = e.get("at_us")
        if at is None:
            errs.append("%s 缺 at_us" % tag)
        elif period is not None and (float(at) < 0 or float(at) >= period):
            errs.append("%s at_us=%.1f 越出周期 [0, %.1f)" % (tag, float(at), period))
        if e.get("path") not in PATHS:
            errs.append("%s path 非法：%r" % (tag, e.get("path")))
        if e.get("on_timeout") not in TIMEOUTS:
            errs.append("%s on_timeout 非法：%r" % (tag, e.get("on_timeout")))
        key = (e.get("kind"), st, e.get("at_us"))
        if key in seen:
            errs.append("%s 与先前事件重复：%r" % (tag, key))
        seen.add(key)
        # irq 路径必须能生成赋值桩：publish 或 (vars+snap_vars)
        if e.get("path") == "irq":
            if e.get("kind") == "output_commit" and not e.get("publish") and not e.get("vars"):
                errs.append("%s path=irq 的 output_commit 需 publish 或 vars" % tag)
            if e.get("kind") == "input_snapshot":
                snaps = e.get("snap_vars") or []
                srcs = e.get("vars") or []
                if not snaps or len(snaps) != len(srcs):
                    errs.append("%s path=irq 的 input_snapshot 需要等长的 vars 与 snap_vars"
                                % tag)
    return errs


def from_time_contract(contract):
    """从 let_pc_shared.time.json 一类 pins 抽出 let_events 初稿。"""
    pins = (contract or {}).get("pins") or {}
    timeout = ((contract or {}).get("timeout") or {}).get("on_compute_miss") or "skip_commit"
    out = []
    for stage, pin in pins.items():
        if not isinstance(pin, dict):
            continue
        kind = pin.get("kind")
        if kind not in KINDS:
            continue
        ev = {
            "kind": kind,
            "stage": stage,
            "at_us": float(pin.get("phase_us") or 0),
            "vars": list(pin.get("vars") or []),
            "on_timeout": timeout,
        }
        if pin.get("path"):
            ev["path"] = pin["path"]
        if pin.get("version"):
            ev["version"] = pin["version"]
        if pin.get("snap_vars"):
            ev["snap_vars"] = list(pin["snap_vars"])
        if pin.get("publish"):
            ev["publish"] = list(pin["publish"])
        out.append(normalize_event(ev))
    out.sort(key=lambda e: (float(e.get("at_us") or 0), e.get("kind") or ""))
    return out


def _phase_map(teir, contract):
    ph = {}
    for n in (teir or {}).get("nodes") or []:
        if n.get("name") is not None:
            ph[n["name"]] = float(n.get("phase_us") or 0.0)
    for stage, pin in ((contract or {}).get("pins") or {}).items():
        if isinstance(pin, dict) and pin.get("phase_us") is not None:
            # 契约钉扎相位覆盖 TEIR（compute / LET 共用）
            ph[stage] = float(pin["phase_us"])
    return ph


def reshape_groups_for_let(plan, teir=None, contract=None):
    """全融合 + LET 后按相位重切组：钉扎读写仍留在前序组（LET_DEFER），
    其后计算阶段升到自身相位释放（如 act@700 在 fetch@500 之后）。

    返回 (plan, changed:bool)。无 let_events 时原样返回。
    """
    events = list(plan.get("let_events") or [])
    if not events:
        return plan, False
    defer = {e.get("stage") for e in events if e.get("stage")}
    if not defer:
        return plan, False

    order = list(plan.get("order") or [])
    if not order:
        for g in plan.get("groups") or []:
            order.extend(g.get("members") or [])
    if not order:
        return plan, False

    ph = _phase_map(teir, contract)
    # 计算阶段不得早于其前序 LET 事件时刻（同拍时组槽优先于 LET，会读到旧快照）
    last_let_at = None
    at_by_stage = {e.get("stage"): float(e.get("at_us") or 0)
                   for e in events if e.get("stage")}
    release_ph = {}
    for s in order:
        if s in defer:
            last_let_at = at_by_stage.get(s, last_let_at)
            release_ph[s] = at_by_stage.get(s, ph.get(s, 0.0))
            continue
        p = ph.get(s, 0.0)
        if last_let_at is not None:
            # 严格晚于最近一次逻辑读写，避免与 LET 同相位时组先跑
            need = float(last_let_at) + 1.0
            if p < need:
                p = need
        # 契约显式相位优先（已在 _phase_map）；再与下界取 max
        if s in ((contract or {}).get("pins") or {}) and \
           isinstance(contract["pins"][s], dict) and \
           contract["pins"][s].get("phase_us") is not None:
            p = max(p, float(contract["pins"][s]["phase_us"]))
        release_ph[s] = p

    # 按 order 扫：LET 阶段并入当前组；计算阶段相位变化则新开组
    buckets = []  # [phase, members]
    cur_ph, cur_mem = None, []
    for s in order:
        if s in defer:
            if cur_ph is None:
                cur_ph = 0.0
            cur_mem.append(s)
            continue
        p = release_ph[s]
        if cur_ph is None:
            cur_ph, cur_mem = p, [s]
        elif abs(cur_ph - p) < 1e-9:
            cur_mem.append(s)
        else:
            buckets.append((cur_ph, cur_mem))
            cur_ph, cur_mem = p, [s]
    if cur_mem:
        buckets.append((cur_ph if cur_ph is not None else 0.0, cur_mem))

    old = plan.get("groups") or []
    dom = "linux_kernel"
    if old:
        dom = old[0].get("domain") or dom
    new_groups = []
    for i, (p, mem) in enumerate(buckets):
        g = {
            "id": "g%d" % i,
            "members": list(mem),
            "domain": dom,
            "phase_us": float(p),
            "release_phase_us": float(p),
        }
        # 显式钉住的计算组 → required，避免后续 enrich 再拉回 0
        only_compute = [m for m in mem if m not in defer]
        if only_compute and any(
            isinstance(((contract or {}).get("pins") or {}).get(m), dict)
            and ((contract or {}).get("pins") or {}).get(m, {}).get("pull_forbidden")
            for m in only_compute
        ):
            g["phase_binding"] = "required"
        new_groups.append(g)

    # 是否真有变化
    old_sig = [(g.get("id"), tuple(g.get("members") or []),
                g.get("phase_us", g.get("release_phase_us")))
               for g in old]
    new_sig = [(g["id"], tuple(g["members"]), g["phase_us"]) for g in new_groups]
    if old_sig == new_sig:
        return plan, False

    out = copy.deepcopy(plan)
    out["groups"] = new_groups
    out["order"] = list(order)
    meta = dict(out.get("meta") or {})
    meta["let_reshaped"] = True
    out["meta"] = meta
    return out, True
