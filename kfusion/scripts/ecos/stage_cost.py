# -*- coding: utf-8 -*-
"""分域阶段成本：数值与证据来源绑定。

CLI 裸数字 ≠ 实测。measured 必须能追溯到日志；缺测不得当 0。
1µs 分桶的 p50 常为 0，拟合用 avg，p50/p99.9/max 分栏保存。
"""
from __future__ import print_function

import os
import re

EV_FINAL = frozenset(("measured", "test"))
EV_EXPLORE = frozenset((
    "estimated", "dag_exec", "shared_exec", "scalar",
    "cli_override", "empirical",
))
EV_WEAK = frozenset(("placeholder",))
EV_MISSING = frozenset(("missing",))

_STAGE_RE = re.compile(
    r"# stat_exec_stage_([A-Za-z0-9_]+)_us: "
    r"min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*?"
    r"p99=(\d+).*?p99\.9=(\d+).*?p99\.99=(\d+) "
    r"max=([0-9.]+) over=(\d+).*? n=(\d+)"
)
_LAT_RE = re.compile(
    r"# stat_start_lat_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*?"
    r"max=([0-9.]+)"
)
_EXEC_RE = re.compile(
    r"# stat_exec_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*?max=([0-9.]+)"
)
_RESP_RE = re.compile(
    r"# stat_response_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*?max=([0-9.]+)"
)
_IOCTL_RE = re.compile(
    r"# stat_ioctl_roundtrip_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*?"
    r"max=([0-9.]+).*? n=(\d+)"
)
_KCOMP_RE = re.compile(
    r"# stat_kernel_compute_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*?"
    r"max=([0-9.]+).*? n=(\d+)"
)


def has_provenance(slot):
    if not isinstance(slot, dict):
        return False
    for k in ("source_log", "source", "log_path"):
        v = slot.get(k)
        if v:
            return True
    return False


def parse_cost_entry(v, from_override=False):
    """返回 (value_or_None, evidence_tag)。伪造 measured 降为 cli_override。"""
    if v is None:
        return None, "missing"
    if not isinstance(v, dict):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None, "missing"
        return x, ("cli_override" if from_override else "scalar")
    val = v.get("value")
    if val is None:
        val = v.get("exec_us")
    ev = v.get("evidence")
    if val is None:
        return None, ev if ev in EV_MISSING else "missing"
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None, "missing"
    if not ev:
        ev = "cli_override" if from_override else "empirical"
    if ev == "measured" and not has_provenance(v):
        ev = "cli_override"
    return x, ev


def parse_user_stage_log(text, source_log=None):
    """从用户态 driver 日志抽逐阶段执行时间。value=avg，不是 p50。"""
    stages = {}
    for m in _STAGE_RE.finditer(text):
        name = m.group(1)
        stages[name] = {
            "value": float(m.group(3)),
            "exec_us": float(m.group(3)),
            "evidence": "measured",
            "statistic": "avg",
            "min_us": float(m.group(2)),
            "avg_us": float(m.group(3)),
            "p50_us": int(m.group(4)),
            "p99_us": int(m.group(5)),
            "p99_9_us": int(m.group(6)),
            "p99_99_us": int(m.group(7)),
            "max_us": float(m.group(8)),
            "over": int(m.group(9)),
            "samples": int(m.group(10)),
            "source_log": source_log,
            "note": "p50 受 1µs 分桶影响，Ĉ 使用 avg",
        }
    lat = None
    m = _LAT_RE.search(text)
    if m:
        lat = {
            "min_us": float(m.group(1)),
            "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)),
            "max_us": float(m.group(4)),
        }
    tot = None
    m = _EXEC_RE.search(text)
    if m:
        tot = {
            "min_us": float(m.group(1)),
            "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)),
            "max_us": float(m.group(4)),
        }
    resp = None
    m = _RESP_RE.search(text)
    if m:
        resp = {
            "min_us": float(m.group(1)),
            "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)),
            "max_us": float(m.group(4)),
        }
    ioctl = None
    m = _IOCTL_RE.search(text)
    if m:
        ioctl = {
            "value": float(m.group(2)),
            "evidence": "measured",
            "statistic": "avg",
            "min_us": float(m.group(1)),
            "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)),
            "max_us": float(m.group(4)),
            "samples": int(m.group(5)),
            "source_log": source_log,
            "kind": "ioctl_roundtrip",
            "note": "用户态包住 ioctl 的往返；含 syscall+copy，不是单阶段 C_K",
        }
    kcomp = None
    m = _KCOMP_RE.search(text)
    if m:
        kcomp = {
            "value": float(m.group(2)),
            "evidence": "measured",
            "statistic": "avg",
            "avg_us": float(m.group(2)),
            "max_us": float(m.group(4)),
            "samples": int(m.group(5)),
            "source_log": source_log,
            "kind": "kernel_compute",
        }
    tax = None
    if ioctl and kcomp and ioctl["value"] >= kcomp["value"]:
        tax = {
            "value": round(ioctl["value"] - kcomp["value"], 6),
            "evidence": "measured",
            "statistic": "avg_roundtrip_minus_kcompute",
            "source_log": source_log,
            "ioctl_roundtrip_us": ioctl["value"],
            "kernel_compute_us": kcomp["value"],
            "samples": ioctl.get("samples"),
            "kind": "T_ioctl",
            "note": "T_ioctl=ioctl_roundtrip−kernel_compute；非 WCET",
        }
    elif ioctl:
        tax = dict(ioctl)
        tax["kind"] = "T_ioctl"
        tax["note"] = "无 kernel_compute 对照时 T_ioctl 用整段 roundtrip"
    return {"stages": stages, "start_lat": lat, "exec": tot,
            "response": resp,
            "ioctl_roundtrip": ioctl, "kernel_compute": kcomp,
            "T_ioctl": tax, "source_log": source_log}


def load_user_stage_log(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    return parse_user_stage_log(text, source_log=os.path.abspath(path))


def bind_to_teir(stages, teir_names):
    """日志里可能是 sample，TEIR 里是 stage_sample。"""
    out = {}
    for tn in teir_names:
        if tn in stages:
            out[tn] = stages[tn]
            continue
        short = tn[6:] if tn.startswith("stage_") else tn
        if short in stages:
            out[tn] = stages[short]
            continue
        for k, slot in stages.items():
            if tn.endswith(k) or k.endswith(tn):
                out[tn] = slot
                break
        else:
            out[tn] = {"value": None, "evidence": "missing",
                       "note": "日志无阶段 %s" % tn}
    return out


def fold_group_cost(total_slot, members):
    """组级一次测量记在首成员，其余 0。禁止把总额再均分。"""
    out = {}
    if not members:
        return out
    base = dict(total_slot or {})
    total = base.get("value")
    try:
        total = float(total)
    except (TypeError, ValueError):
        total = None
    for i, m in enumerate(members):
        slot = dict(base)
        slot["value"] = (total if (i == 0 and total is not None) else
                         (0.0 if total is not None else None))
        slot["covers_group"] = list(members)
        slot["folded"] = bool(i)
        slot["note"] = ("组级测量记在 %s，其余成员 0；不得均分" % members[0])
        out[m] = slot
    return out


def override_from_gold_mixed(gold, mixed, teir_names, kernel_members):
    """gold 提供用户态逐阶段；mixed 提供内核组 kernel_compute 与 T_ioctl。"""
    gold = gold or {}
    mixed = mixed or {}
    names = list(teir_names or [])
    user = bind_to_teir(gold.get("stages") or {}, names)
    kmem = list(kernel_members or [])
    kcomp = mixed.get("kernel_compute")
    if kcomp and kmem:
        folded = fold_group_cost(kcomp, kmem)
        kernel = {}
        for n in names:
            if n in folded:
                kernel[n] = folded[n]
            else:
                kernel[n] = {
                    "value": None, "evidence": "missing",
                    "note": "该阶段不在内核组，不以 gold 用户态值冒充 C_K",
                }
    else:
        kernel = kernel_missing(names)
    ioctl = mixed.get("T_ioctl")
    return {"linux_userspace": user, "linux_kernel": kernel}, ioctl


def kernel_missing(names):
    return {n: {"value": None, "evidence": "missing",
                "note": "内核 rsf_probe 无逐阶段剖析，不得均分整组 exec"}
            for n in names}


def build_override(user_stages, kernel_stages=None, teir_names=None):
    """STKP --stage-cost 对象。内核缺测写 missing，不填 0。"""
    names = list(teir_names) if teir_names else list(user_stages.keys())
    user = bind_to_teir(user_stages, names) if teir_names else dict(user_stages)
    out = {"linux_userspace": user}
    if kernel_stages:
        out["linux_kernel"] = bind_to_teir(kernel_stages, names) if teir_names else dict(kernel_stages)
    else:
        out["linux_kernel"] = kernel_missing(names)
    return out
