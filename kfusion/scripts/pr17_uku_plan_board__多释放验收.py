#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-17B：UKU 多释放板上验收。

编译成功不算覆盖。先 plan_user / plan 语义对照，通过后才允许 mixed_plan 矩阵。
覆盖率在 digest、释放数、会话读回都过之前保持 4/5。
"""
from __future__ import print_function

import argparse
import hashlib
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import pr11_shapes__形状补测 as sh  # noqa: E402

OUT = os.path.join(KF, "docs", "pr17_uku_plan")
SRC = sh.SRC


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


SNAPSHOT_NAMES = ("stkp.json", "candidates.json", "chosen.plan.json")


def snapshot_inputs(gen_dir, out_dir):
    """把 STKP 候选快照拷出 gitignore 的 build/，决策报告才能复现。"""
    rec = {"kind": "pr17_input_snapshot", "files": {}}
    for name in SNAPSHOT_NAMES:
        src = os.path.join(gen_dir, name)
        if not os.path.isfile(src):
            rec["files"][name] = {"missing": True}
            continue
        dst = os.path.join(out_dir, name)
        shutil.copy2(src, dst)
        rec["files"][name] = {
            "path": os.path.relpath(dst, os.path.dirname(KF)).replace(os.sep, "/"),
            "sha256": sha256(dst),
            "bytes": os.path.getsize(dst),
        }
    plan_p = os.path.join(out_dir, "plan.json")
    if os.path.isfile(plan_p):
        rec["files"]["plan.json"] = {
            "path": os.path.relpath(plan_p, os.path.dirname(KF)).replace(os.sep, "/"),
            "sha256": sha256(plan_p),
            "bytes": os.path.getsize(plan_p),
        }
    write_json(os.path.join(out_dir, "input_hashes.json"), rec)
    return rec


def write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def semantic(cell, cycles, out_dir):
    """同一二进制：plan_user 全用户态三释放 vs plan 中段 ioctl。"""
    os.makedirs(out_dir, exist_ok=True)
    drv = cell["driver"]
    ko = cell.get("ko")
    cpu = str(sh.PIN_CPU)
    rec = {"cycles": cycles, "driver": drv, "ko": ko}

    rc_u, txt_u = sh.run(["taskset", "-c", cpu, drv, "plan_user", str(cycles)])
    open(os.path.join(out_dir, "plan_user.log"), "w").write(txt_u)
    st_u = sh.parse_stats(txt_u)
    rec["plan_user"] = {"rc": rc_u, "stats": st_u}

    if not ko:
        rec["ok"] = False
        rec["reason"] = "无 .ko，plan 无法作为 mixed 形状跑"
        return rec
    rc_i, out_i = sh.run(["insmod", ko])
    if rc_i != 0:
        rec["ok"] = False
        rec["reason"] = "insmod 失败：%s" % out_i.strip()[:160]
        return rec
    try:
        rc_p, txt_p = sh.run(["taskset", "-c", cpu, drv, "plan", str(cycles)])
    finally:
        rc_rm, out_rm = sh.run(["rmmod", os.path.basename(ko)[:-3]])
    open(os.path.join(out_dir, "plan.log"), "w").write(txt_p)
    st_p = sh.parse_stats(txt_p)
    rec["plan"] = {"rc": rc_p, "stats": st_p, "rmmod_rc": rc_rm,
                   "rmmod_out": out_rm.strip()[:120]}

    why = []
    if rc_u != 0:
        why.append("plan_user rc=%s" % rc_u)
    if rc_p != 0:
        why.append("plan rc=%s" % rc_p)
    if rc_rm != 0:
        why.append("rmmod rc=%s" % rc_rm)
    if st_u.get("digest") != st_p.get("digest") or not st_p.get("digest"):
        why.append("digest %s vs %s" % (st_u.get("digest"), st_p.get("digest")))
    if st_u.get("n_releases") != 3 or st_p.get("n_releases") != 3:
        why.append("n_releases user=%s plan=%s" % (
            st_u.get("n_releases"), st_p.get("n_releases")))
    if (st_p.get("ioctl_error") or 0) or (st_p.get("seq_mismatch") or 0):
        why.append("ioctl/seq 异常")
    su, sp = st_u.get("session") or {}, st_p.get("session") or {}
    if not sp.get("readback"):
        why.append("内核会话未读回")
    elif su.get("value") != sp.get("value"):
        why.append("session %s vs %s" % (su.get("value"), sp.get("value")))
    ph = st_p.get("plan_phases_us") or []
    if [int(x) for x in ph] != [0, 200, 400]:
        why.append("phases %s" % ph)
    rec["ok"] = not why
    rec["reason"] = None if rec["ok"] else "; ".join(why)
    rec["coverage_claim"] = "5/5" if rec["ok"] else "4/5（语义未过，不得提升覆盖）"
    if rec["ok"]:
        rec["coverage_claim"] = "4/5（语义过了，矩阵未完成仍不得写 5/5）"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=80)
    ap.add_argument("--repeat", type=int, default=10)
    ap.add_argument("--matrix-cycles", type=int, default=5000)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--matrix", action="store_true",
                    help="语义通过后才跑 CFS/FIFO × load0/4")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    spec = [s for s in sh.SHAPES if s["name"] == "mixed_plan"][0]
    wd = os.path.join(a.out, "build")
    print("[build] mixed_plan", file=sys.stderr)
    b = sh.build_shape(spec, wd)
    write_json(os.path.join(a.out, "build.json"), {
        k: (v if k != "plan" else {"groups": (v or {}).get("groups"),
                                    "meta": (v or {}).get("meta")})
        for k, v in b.items() if k != "plan"
    })
    if b.get("plan"):
        write_json(os.path.join(a.out, "plan.json"), b["plan"])
    snap = snapshot_inputs(os.path.join(wd, "gen"), a.out)
    if b.get("status") != "ok":
        print("构建失败 %s" % b.get("status"), file=sys.stderr)
        write_json(os.path.join(a.out, "report.json"), {
            "kind": "pr17_uku_plan", "ok": False, "build": b.get("status"),
            "coverage": "4/5", "note": "编译失败不算覆盖",
        })
        return 4
    hashes = {
        "src": sha256(SRC),
        "driver": sha256(b["driver"]) if os.path.isfile(b["driver"]) else None,
        "ko": sha256(b["ko"]) if b.get("ko") and os.path.isfile(b["ko"]) else None,
    }
    print("[semantic] plan_user vs plan ×%d" % a.cycles, file=sys.stderr)
    sem = semantic(b, a.cycles, os.path.join(a.out, "semantic"))
    sem["hashes"] = hashes
    sem["shape"] = b.get("shape")
    write_json(os.path.join(a.out, "semantic.json"), sem)
    print("  semantic ok=%s %s" % (sem.get("ok"), sem.get("reason") or ""),
          file=sys.stderr)

    report = {
        "kind": "pr17_uku_plan",
        "claim": "empirical; not WCET; compile-success is not coverage",
        "coverage": "4/5",
        "semantic_ok": bool(sem.get("ok")),
        "semantic": sem,
        "matrix": None,
        "inputs": snap,
        "note": "mixed 单释放画像不得当作 mixed_plan",
    }
    if not sem.get("ok"):
        write_json(os.path.join(a.out, "report.json"), report)
        return 5
    if not a.matrix:
        write_json(os.path.join(a.out, "report.json"), report)
        print("语义通过。未跑矩阵，覆盖率仍 4/5。加 --matrix 才采集。",
              file=sys.stderr)
        return 0

    cells = []
    for load in (0, 4):
        for sched in ("cfs", "fifo99"):
            rd = os.path.join(a.out, "runs_load%d_%s" % (load, sched))
            print("[matrix] %s load=%d ×%d×%d" % (
                sched, load, a.repeat, a.matrix_cycles), file=sys.stderr)
            res = sh.measure_shape(b, sched, a.matrix_cycles, a.repeat, rd,
                                   load=load, mode="plan")
            cells.append({"sched": sched, "load": load, **res})
    n_valid = sum(c.get("n_valid") or 0 for c in cells)
    n_need = a.repeat * len(cells)
    matrix_ok = n_valid == n_need and n_need > 0
    report["matrix"] = {"cells": cells, "n_valid": n_valid, "n_need": n_need,
                        "ok": matrix_ok}
    q99_ok = True
    for c in cells:
        for r in c.get("runs") or []:
            rp = r.get("response") or {}
            if not r.get("valid"):
                continue
            if (rp.get("min_us") is not None and rp.get("p99_us") is not None
                    and float(rp["min_us"]) > float(rp["p99_us"]) + 0.5):
                q99_ok = False
    if matrix_ok and q99_ok:
        report["coverage"] = "5/5"
        report["note"] = ("mixed_plan 实测完整；勿与 PR-10E mixed 单释放混表。"
                          "非 WCET。J(d,s) 用 sched_profile_v3.json。")
    elif matrix_ok and not q99_ok:
        report["coverage"] = "4/5"
        report["note"] = "轮次有效但 response 分位数被桶顶夹住，不得当 Q99"
    else:
        report["coverage"] = "4/5"
        report["note"] = "矩阵有无效轮次，覆盖率不得升到 5/5"
    write_json(os.path.join(a.out, "report.json"), report)
    return 0 if (matrix_ok and q99_ok) else 6


if __name__ == "__main__":
    sys.exit(main())
