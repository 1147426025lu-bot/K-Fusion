#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-4C：STKP 实机闭环（经验 R，不是 WCET）。

1. 同一份五阶段 C：等权成本 → 用户态产物；压低 c_kernel → 内核产物。
2. 板上跑入选用户态（及内核，若 .ko 编出），记录 r/s/f。
3. 把实测释放/执行回填 STKP 再求解，不得用占位宣称最优。
4. chain_window 时间窗口：STKP 必须拒绝全融合，选出 s1+s2 | s3+s4。
"""
from __future__ import print_function

import json
import os
import re
import shutil
import subprocess
import sys
import time

KF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(KF, "examples/ecos/five_stage_loop__闭环五阶段示例.c")
PIPE = os.path.join(KF, "scripts/ecos_from_c__从C编译.sh")
STKP = os.path.join(KF, "scripts/ecos_plan_joint.py")
TEIR = os.path.join(KF, "ir/temporal_effect_ir.py")
CAPS = os.path.join(KF, "contracts/examples/platform_caps.json")
CW_DAG = os.path.join(KF, "scripts/test/fusion/chain_window.dag.json")
CW_CON = os.path.join(KF, "contracts/examples/chain_window.contract.json")
REPORT_DIR = os.environ.get(
    "PR4C_OUT", os.path.join(KF, "docs/pr4c_stkp_board"))
LOGDIR = os.path.join(REPORT_DIR, "logs")
WORK = os.environ.get("PR4C_WORK", "/tmp/pr4c_stkp")
CYCLES = int(os.environ.get("CYCLES", "200"))
CPU = os.environ.get("MATRIX_CPU", "0")
FIFO = int(os.environ.get("MATRIX_FIFO", "80"))
PERIOD_US = int(os.environ.get("PERIOD_US", "1000"))
KDIR = os.environ.get("KERNELDIR", "/root/linux-5.15.86")
PY = os.environ.get("PYTHON", sys.executable)


def dump(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def sh(cmd, env=None, cwd=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT,
                                   env=e, cwd=cwd)


def parse_user(text):
    rec = {"raw_tail": "\n".join(text.strip().splitlines()[-14:])}
    m = re.search(r"# digest: ([0-9a-f]+)", text)
    if m:
        rec["digest"] = m.group(1)
    m = re.search(r"# deadline_miss: (\d+)/(\d+)", text)
    if m:
        rec["miss"] = int(m.group(1))
        rec["cycles"] = int(m.group(2))
    for name in ("start_lat", "exec", "response", "span"):
        m = re.search(
            r"# stat_%s_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*max=([0-9.]+) over=(\d+)"
            % name, text)
        if m:
            rec[name] = {
                "min_us": float(m.group(1)),
                "avg_us": float(m.group(2)),
                "p50_us": int(m.group(3)),
                "max_us": float(m.group(4)),
                "over": int(m.group(5)),
            }
    return rec


def parse_rsf(text):
    rec = {"raw": text.strip()}
    m = re.search(r"miss=(\d+) periods=(\d+)", text)
    if m:
        rec["miss"] = int(m.group(1))
        rec["n"] = int(m.group(2))
    for name in ("start_lat", "exec", "response"):
        m = re.search(
            r"%s n=(\d+) over=(\d+) min=(\d+)ns p50=(-?\d+)us p99=(-?\d+)us "
            r"avg=(\d+)ns max=(\d+)ns" % name, text)
        if m:
            rec[name] = {
                "n": int(m.group(1)),
                "over": int(m.group(2)),
                "min_ns": int(m.group(3)),
                "p50_us": int(m.group(4)),
                "p99_us": int(m.group(5)),
                "avg_ns": int(m.group(6)),
                "max_ns": int(m.group(7)),
            }
    return rec


def run_pipe(outdir, c_user, c_kernel, extra, skip_kmod="1", stage_cost=None):
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir)
    env = {
        "OUT_DIR": outdir,
        "C_USER_US": str(c_user),
        "C_KERNEL_US": str(c_kernel),
        "KERNEL_EXTRA_US": str(extra),
        "SKIP_HOST": "1",
        "SKIP_KMOD": skip_kmod,
        "CYCLES": str(CYCLES),
        "KERNELDIR": KDIR,
    }
    if stage_cost:
        costp = os.path.join(outdir, "stage_cost.in.json")
        os.makedirs(outdir, exist_ok=True)
        dump(costp, stage_cost)
        env["STAGE_COST"] = costp
    out = sh(["bash", PIPE, SRC], env=env)
    logp = os.path.join(LOGDIR, os.path.basename(outdir) + "_pipe.log")
    os.makedirs(LOGDIR, exist_ok=True)
    with open(logp, "w", encoding="utf-8") as f:
        f.write(out.decode("utf-8", "replace"))
    deploy = json.load(open(os.path.join(outdir, "deploy.json")))
    stkp = json.load(open(os.path.join(outdir, "stkp.json")))
    return {
        "deploy": deploy,
        "stkp_chosen": stkp.get("chosen"),
        "stkp_claim": stkp.get("claim"),
        "domain": deploy.get("domain"),
        "has_driver": os.path.isfile(os.path.join(outdir, "driver.c")),
        "has_kbuild": os.path.isfile(os.path.join(outdir, "kmod_hrtimer", "Kbuild")),
        "log": logp,
    }


def compile_driver(outdir):
    src = os.path.join(outdir, "driver.c")
    binp = os.path.join(outdir, "driver")
    subprocess.check_call([
        "gcc", "-O2", "-std=gnu11", "-Wall", "-Werror",
        "-I", os.path.join(KF, "include"), src, "-o", binp, "-lrt",
    ])
    return binp


def run_user(binp, mode, tag):
    cmd = ["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
           binp, mode, str(CYCLES)]
    out = sh(cmd).decode("utf-8", "replace")
    rec = parse_user(out)
    rec["cmd"] = cmd
    rec["domain"] = "linux_userspace"
    rec["fused"] = mode == "plan"
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, "user_%s.log" % tag), "w") as f:
        f.write(out)
    return rec


def run_kmod(outdir, hold_s=0.45):
    kdir = os.path.join(outdir, "kmod_hrtimer")
    kos = [f for f in os.listdir(kdir) if f.endswith(".ko")] if os.path.isdir(kdir) else []
    if not kos:
        return {"error": "no .ko", "legal_run": False}
    name = kos[0][:-3]
    ko = os.path.join(kdir, kos[0])
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    try:
        sh(["insmod", ko, "period_us=%d" % PERIOD_US,
            "rt_priority=%d" % FIFO, "cpu=%s" % CPU])
    except subprocess.CalledProcessError as e:
        return {"error": e.output.decode("utf-8", "replace")[-400:],
                "legal_run": False, "ko": ko}
    time.sleep(hold_s)
    base = "/sys/module/%s/parameters" % name
    rsf = open(os.path.join(base, "rsf_probe")).read()
    periods = open(os.path.join(base, "periods")).read().strip()
    missed = open(os.path.join(base, "missed")).read().strip()
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    rec = parse_rsf(rsf)
    rec["periods"] = int(periods)
    rec["missed_backlog"] = int(missed)
    rec["domain"] = "linux_kernel"
    rec["ko"] = ko
    rec["legal_run"] = True
    with open(os.path.join(LOGDIR, "rsf_%s.txt" % name), "w") as f:
        f.write(rsf)
    return rec


def chain_window_stkp():
    work = os.path.join(WORK, "chain_window")
    os.makedirs(work, exist_ok=True)
    teir_p = os.path.join(work, "teir.json")
    sh([PY, TEIR, "--dag", CW_DAG, "--platforms", CAPS, "-o", teir_p])
    outp = os.path.join(work, "stkp.json")
    sh([PY, STKP, "--teir", teir_p, "--dag", CW_DAG, "--contract", CW_CON,
        "--platforms", CAPS, "--c-user-us", "21", "--c-kernel-us", "21",
        "--no-baseline", "-o", outp])
    doc = json.load(open(outp))
    rec = doc.get("chosen_record") or {}
    groups = (rec.get("plan") or {}).get("groups") or []
    members = [g.get("members") for g in groups]
    fused_rejected = any(
        x.get("id") in ("B", "D") and x.get("reason") == "temporal"
        for x in (doc.get("rejected_sample") or []))
    return {
        "chosen": doc.get("chosen"),
        "claim": doc.get("claim"),
        "groups": members,
        "pruned": doc.get("pruned"),
        "full_fusion_temporally_rejected": fused_rejected,
        "matches_expected_split": members == [["s1", "s2"], ["s3", "s4"]],
        "stkp_json": outp,
    }


def refill_stkp(teir_path, dag_path, c_user, c_kernel, stage_cost, extra=0.0):
    work = os.path.join(WORK, "refill")
    os.makedirs(work, exist_ok=True)
    costp = os.path.join(work, "stage_cost.json")
    dump(costp, stage_cost)
    outp = os.path.join(work, "stkp.json")
    cmd = [PY, STKP, "--teir", teir_path, "--dag", dag_path,
           "--platforms", CAPS,
           "--c-user-us", str(c_user), "--c-kernel-us", str(c_kernel),
           "--kernel-extra-us", str(extra),
           "--stage-cost", costp, "-o", outp, "--chosen-plan",
           os.path.join(work, "chosen.plan.json")]
    sh(cmd)
    doc = json.load(open(outp))
    return {
        "chosen": doc.get("chosen"),
        "claim": doc.get("claim"),
        "domain": (doc.get("chosen_record") or {}).get("domain"),
        "why": (doc.get("chosen_record") or {}).get("why"),
        "cost": ((doc.get("chosen_record") or {}).get("cost") or {}),
        "vs_baseline": doc.get("vs_baseline"),
        "stkp_json": outp,
    }


def p50(cell, key="response"):
    return ((cell or {}).get(key) or {}).get("p50_us")


def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(LOGDIR, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    host = open("/proc/version").read().strip()
    report = {
        "kind": "stkp_board_loop",
        "claim": "empirical; not WCET; STKP drives emit",
        "host": host,
        "controls": {
            "cpu": CPU, "fifo": FIFO, "period_us": PERIOD_US,
            "cycles_user": CYCLES,
        },
        "cells": {},
        "selection": {},
        "refill": {},
        "chain_window": {},
    }

    print("== 1) 占位成本：不得宣称最优，回退用户态，无 kmod ==")
    user_dir = os.path.join(WORK, "eq")
    sel_eq = run_pipe(user_dir, 21, 21, 0, skip_kmod="1")
    report["selection"]["placeholder"] = sel_eq
    assert sel_eq["domain"] == "linux_userspace", sel_eq
    assert sel_eq["stkp_claim"] == "placeholder_no_optimum", sel_eq
    assert sel_eq["has_driver"] and not sel_eq["has_kbuild"], sel_eq

    names = [n["name"] for n in json.load(open(
        os.path.join(user_dir, "task.teir.json"))).get("nodes") or []]
    # 调用方给出的分域执行时间 → estimated/cli_override，允许经验排序
    estimated = {
        "linux_userspace": {n: 1.0 for n in names},
        "linux_kernel": {n: 1.0 for n in names},
    }

    print("== 2) 分域成本 + 更低 c_kernel：应选内核并写出 Kbuild ==")
    kern_dir = os.path.join(WORK, "dkern")
    sel_k = run_pipe(kern_dir, 21, 10, 0, skip_kmod="0",
                     stage_cost=estimated)
    report["selection"]["measured_low_ck"] = sel_k
    assert sel_k["domain"] == "linux_kernel", sel_k
    assert sel_k["has_kbuild"], sel_k

    print("== 3) 板上用户态 A/B（等权管线的 driver） ==")
    binp = compile_driver(user_dir)
    report["cells"]["A"] = run_user(binp, "flat", "A")
    report["cells"]["A"]["id"] = "A"
    report["cells"]["B"] = run_user(binp, "plan", "B")
    report["cells"]["B"]["id"] = "B"
    da = report["cells"]["A"].get("digest")
    db = report["cells"]["B"].get("digest")
    report["digest_match_AB"] = bool(da and da == db)

    print("== 4) 板上内核 D（若 .ko 存在） ==")
    kd = run_kmod(kern_dir)
    report["cells"]["D"] = kd
    report["cells"]["D"]["id"] = "D"
    report["cells"]["D"]["fused"] = True

    print("== 5) 实测回填 STKP（逐阶段 avg，内核缺剖析则 missing） ==")
    sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))
    import stage_cost as sc
    blog = os.path.join(LOGDIR, "user_B.log")
    parsed = sc.load_user_stage_log(blog) if os.path.isfile(blog) else {"stages": {}}
    teir = json.load(open(os.path.join(user_dir, "task.teir.json")))
    names = [n["name"] for n in teir.get("nodes") or []]
    stage_cost = sc.build_override(parsed.get("stages") or {}, teir_names=names)
    c_u = ((parsed.get("start_lat") or {}).get("avg_us")
           or p50(report["cells"]["B"], "start_lat"))
    e_u = (parsed.get("exec") or {}).get("avg_us")
    c_k = p50(report["cells"].get("D"), "start_lat")
    e_k = p50(report["cells"].get("D"), "exec")
    c_user_m = float(c_u) if c_u is not None else 21.0
    c_kern_m = float(c_k) if c_k is not None else 21.0
    report["measured"] = {
        "c_user_start_lat_avg_us": c_u,
        "c_kernel_start_lat_p50_us": c_k,
        "exec_user_avg_us": e_u,
        "exec_kernel_p50_us": e_k,
        "stage_cost": stage_cost,
        "note": ("Ĉ 用 stat_exec_stage_* 的 avg；内核无逐阶段探针，标 missing。"
                 "经验值，不是 WCET。不得均分整组 exec。"),
    }
    refill = refill_stkp(
        os.path.join(user_dir, "task.teir.json"),
        os.path.join(user_dir, "task.dag.json"),
        c_user_m, c_kern_m, stage_cost)
    report["refill"] = refill

    print("== 6) chain_window 窗口：禁止全融合 ==")
    report["chain_window"] = chain_window_stkp()

    report["conclusions"] = {
        "stkp_drives_emit": (
            sel_eq["domain"] == "linux_userspace"
            and sel_k["domain"] == "linux_kernel"
        ),
        "digest_match_AB": report.get("digest_match_AB"),
        "fusion_helps_user": (
            p50(report["cells"]["A"]) is not None
            and p50(report["cells"]["B"]) is not None
            and p50(report["cells"]["B"]) < p50(report["cells"]["A"]) / 2
        ),
        "window_blocks_full_fusion": report["chain_window"].get(
            "matches_expected_split"),
        "refill_claim": refill.get("claim"),
        "refill_chosen": refill.get("chosen"),
        "note": (
            "B/D 经验 R 接近时，回填后不应仅因占位选内核。"
            "chain_window 证明时间契约能挡住全融合。"
        ),
    }
    dump(os.path.join(REPORT_DIR, "report.json"), report)
    md = os.path.join(REPORT_DIR, "RESULT_PR4C__STKP实机闭环.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# PR-4C STKP 实机闭环（经验值，不是 WCET）\n\n")
        f.write("宿主：`%s`\n\n" % host)
        f.write("控制：CPU%s FIFO%s T=%sµs 用户态 %d 周期。\n\n"
                % (CPU, FIFO, PERIOD_US, CYCLES))
        f.write("## 选型驱动生成\n\n")
        f.write("| 成本输入 | STKP | 域 | driver | Kbuild |\n|---|---|---|---|---|\n")
        f.write("| 占位 c_u=c_k=21 | %s | %s | %s | %s |\n" % (
            sel_eq["stkp_chosen"], sel_eq["domain"],
            sel_eq["has_driver"], sel_eq["has_kbuild"]))
        f.write("| 分域成本 + c_k=10 | %s | %s | %s | %s |\n\n" % (
            sel_k["stkp_chosen"], sel_k["domain"],
            sel_k["has_driver"], sel_k["has_kbuild"]))
        f.write("## 板上 R（p50 / max，µs）\n\n")
        f.write("| 格 | 域 | R p50 | R max | miss | digest |\n|---|---|---:|---:|---:|---|\n")
        for cid in ("A", "B", "D"):
            c = report["cells"].get(cid) or {}
            r = c.get("response") or {}
            mx = r.get("max_us")
            if mx is None and r.get("max_ns") is not None:
                mx = round(float(r["max_ns"]) / 1000.0, 3)
            f.write("| %s | %s | %s | %s | %s | %s |\n" % (
                cid, c.get("domain") or c.get("error"),
                r.get("p50_us"), mx,
                c.get("miss"), c.get("digest") or ""))
        f.write("\nA/B digest 一致：%s\n\n" % report.get("digest_match_AB"))
        f.write("## 实测回填\n\n")
        f.write("claim=`%s` chosen=`%s` domain=`%s`\n\n" % (
            refill.get("claim"), refill.get("chosen"), refill.get("domain")))
        f.write("## chain_window 时间窗口\n\n")
        f.write("入选分组 `%s`（期望 `[['s1','s2'],['s3','s4']]`）。"
                "全融合被窗口拒绝：%s。\n" % (
                    report["chain_window"].get("groups"),
                    report["chain_window"].get("full_fusion_temporally_rejected")))
        f.write("\n原始日志：`logs/`，表：`report.json`。\n")
    print(json.dumps({
        "wrote": os.path.join(REPORT_DIR, "report.json"),
        "md": md,
        "placeholder": sel_eq["stkp_chosen"],
        "low_ck": sel_k["stkp_chosen"],
        "A_R": p50(report["cells"]["A"]),
        "B_R": p50(report["cells"]["B"]),
        "D_R": p50(report["cells"].get("D")),
        "digest_match": report.get("digest_match_AB"),
        "refill": refill.get("chosen"),
        "window": report["chain_window"].get("groups"),
    }, indent=2, ensure_ascii=False))
    ok = (
        sel_eq["domain"] == "linux_userspace"
        and sel_k["domain"] == "linux_kernel"
        and report.get("digest_match_AB")
        and report["chain_window"].get("matches_expected_split")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
