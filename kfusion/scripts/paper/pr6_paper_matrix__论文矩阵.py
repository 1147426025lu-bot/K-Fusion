#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-6：STKP 选择性内核化论文矩阵（经验值，不是 WCET）。

W1 无状态 UKU：可部署，gold/mixed digest 必须一致。
W2 有状态 PID：U|K|U 形态但积分跨拍 → 拒绝 UKU，不得静默内核化。
W3 执行器窗口：串行 ioctl 完成时刻进不了窗口 → 不得选 mixed / 全融合。
"""
from __future__ import print_function

import json
import os
import re
import shutil
import subprocess
import sys

KF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PIPE = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
OUT = os.environ.get("PR6_OUT", os.path.join(KF, "docs", "pr6_paper_matrix"))
WORK = os.environ.get("PR6_WORK", "/tmp/pr6_matrix")
CYCLES = int(os.environ.get("CYCLES", "80"))
CPU = os.environ.get("MATRIX_CPU", "0")
FIFO = int(os.environ.get("MATRIX_FIFO", "80"))
KDIR = os.environ.get("KERNELDIR", "/root/linux-5.15.86")
PY = os.environ.get("PYTHON", sys.executable)

W1_SRC = os.path.join(KF, "examples/ecos/mixed_uku__无状态混合.c")
W2_SRC = os.path.join(KF, "examples/ecos/mixed_pid_stateful__积分混合.c")
W3_SRC = os.path.join(KF, "examples/ecos/chain_window__四段窗口示例.c")
W3_CON = os.path.join(KF, "contracts/examples/chain_window.contract.json")


def dump(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def sh(cmd, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, env=e)


def rec_stkp(pdir):
    stkp = json.load(open(os.path.join(pdir, "stkp.json")))
    rec = stkp.get("chosen_record") or {}
    plan = rec.get("plan") or {}
    meta = plan.get("meta") or {}
    return {
        "chosen": stkp.get("chosen"),
        "claim": stkp.get("claim"),
        "domain": rec.get("domain"),
        "deployment_ready": rec.get("deployment_ready"),
        "adapter": meta.get("xdom_adapter"),
        "uku_reject": meta.get("uku_reject"),
        "uku_session": meta.get("uku_session"),
        "groups": [g.get("members") for g in plan.get("groups") or []],
        "domains": [g.get("domain") for g in plan.get("groups") or []],
        "predicted_R_us": (rec.get("cost") or {}).get("predicted_R_us"),
        "grade": (rec.get("cost") or {}).get("grade"),
    }


def pipe(src, pdir, env_extra, skip_host=True, skip_kmod=True):
    if os.path.isdir(pdir):
        shutil.rmtree(pdir)
    os.makedirs(pdir)
    env = {
        "OUT_DIR": pdir,
        "SKIP_HOST": "1" if skip_host else "0",
        "SKIP_KMOD": "1" if skip_kmod else "0",
        "C_USER_US": "21",
        "C_KERNEL_US": "10",
        "HANDOFF_US": "1",
        "KERNELDIR": KDIR,
        "CYCLES": str(CYCLES),
    }
    env.update(env_extra or {})
    log = sh(["bash", PIPE, src], env=env).decode("utf-8", "replace")
    return log, rec_stkp(pdir)


def parse_run(text):
    rec = {}
    m = re.search(r"# digest: ([0-9a-f]+)", text)
    if m:
        rec["digest"] = m.group(1)
    m = re.search(r"# last_seq: (\d+) g_in=(-?\d+) g_out=(-?\d+)", text)
    if m:
        rec["last_seq"] = int(m.group(1))
        rec["g_in"] = int(m.group(2))
        rec["g_out"] = int(m.group(3))
    m = re.search(r"# uku_seq_mismatch: (\d+)/", text)
    if m:
        rec["seq_mismatch"] = int(m.group(1))
    m = re.search(r"# uku_ioctl_error: (\d+)/", text)
    if m:
        rec["ioctl_error"] = int(m.group(1))
    m = re.search(
        r"# stat_response_us: min=([0-9.]+) avg=([0-9.]+) p50=(\d+).*max=([0-9.]+)",
        text)
    if m:
        rec["response"] = {
            "min_us": float(m.group(1)), "avg_us": float(m.group(2)),
            "p50_us": int(m.group(3)), "max_us": float(m.group(4)),
        }
    return rec


def w1(out):
    cost = {
        "linux_userspace": {
            "stage_sample": 1.0, "stage_add": 80.0,
            "stage_shift": 80.0, "stage_sink": 1.0,
        },
        "linux_kernel": {"stage_add": 1.0, "stage_shift": 1.0},
    }
    costp = os.path.join(WORK, "w1_cost.json")
    dump(costp, cost)
    pdir = os.path.join(WORK, "w1")
    log, rec = pipe(W1_SRC, pdir, {"STAGE_COST": costp},
                    skip_host=False, skip_kmod=False)
    open(os.path.join(out, "logs", "w1_pipe.log"), "w").write(log)
    board = {}
    binp = os.path.join(pdir, "driver")
    kdir = os.path.join(pdir, "kmod_uku")
    kos = [f for f in os.listdir(kdir) if f.endswith(".ko")] if os.path.isdir(kdir) else []
    if os.path.isfile(binp) and kos:
        gold = sh(["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
                   binp, "gold", str(CYCLES)]).decode("utf-8", "replace")
        open(os.path.join(out, "logs", "w1_gold.log"), "w").write(gold)
        name = kos[0][:-3]
        subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
        sh(["insmod", os.path.join(kdir, kos[0])])
        mixed = sh(["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
                    binp, "mixed", str(CYCLES)]).decode("utf-8", "replace")
        open(os.path.join(out, "logs", "w1_mixed.log"), "w").write(mixed)
        subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
        g, m = parse_run(gold), parse_run(mixed)
        board = {
            "gold": g, "mixed": m,
            "digest_match": g.get("digest") and g.get("digest") == m.get("digest"),
            "seq_ok": m.get("seq_mismatch") == 0 and m.get("ioctl_error") == 0,
        }
    rec["board"] = board
    rec["plan_pass"] = (
        rec.get("adapter") == "uku_ioctl" and rec.get("deployment_ready"))
    rec["board_pass"] = bool(
        board.get("digest_match") and board.get("seq_ok")
        and board.get("gold") and board.get("mixed"))
    rec["end_to_end_pass"] = rec["plan_pass"] and rec["board_pass"]
    rec["ok"] = rec["end_to_end_pass"]
    rec["note"] = "无状态 UKU；digest 一致不是内核更快；非 WCET；无板级日志不得标 end_to_end"
    return rec


def w2(out):
    cost = {
        "linux_userspace": {
            "stage_sample": 1.0, "stage_pid": 80.0, "stage_sink": 1.0,
        },
        "linux_kernel": {"stage_pid": 1.0},
    }
    costp = os.path.join(WORK, "w2_cost.json")
    dump(costp, cost)
    pdir = os.path.join(WORK, "w2")
    log, rec = pipe(W2_SRC, pdir, {"STAGE_COST": costp},
                    skip_host=True, skip_kmod=True)
    open(os.path.join(out, "logs", "w2_pipe.log"), "w").write(log)
    teir = json.load(open(os.path.join(pdir, "task.teir.json")))
    pid = [n for n in teir.get("nodes") or [] if n.get("name") == "stage_pid"]
    rec["pid_persistent"] = (pid[0].get("persistent") if pid else None)
    rec["plan_pass"] = (
        rec.get("adapter") == "uku_ioctl"
        and rec.get("domain") == "mixed"
        and bool(rec.get("uku_session"))
        and (rec.get("uku_session") or {}).get("kind") == "integ_i32"
        and os.path.isfile(os.path.join(pdir, "kmod_uku", "kfusion_uku_mod.c"))
    )
    rec["board_pass"] = False
    rec["end_to_end_pass"] = False
    rec["ok"] = rec["plan_pass"]
    rec["note"] = "单 int32 积分会话：STKP 可生成 UKU；非任意 persistent（规划证据）"
    return rec


def w3(out):
    names = ["s1", "s2", "s3", "s4"]
    cost = {
        "linux_userspace": {n: 0.6 for n in names},
        "linux_kernel": {n: 0.6 for n in names},
    }
    costp = os.path.join(WORK, "w3_cost.json")
    dump(costp, cost)
    pdir = os.path.join(WORK, "w3")
    log, rec = pipe(W3_SRC, pdir, {
        "TIME_CONTRACT": W3_CON, "STAGE_COST": costp,
        "C_KERNEL_US": "21",
    }, skip_host=True, skip_kmod=True)
    open(os.path.join(out, "logs", "w3_pipe.log"), "w").write(log)
    letp = os.path.join(pdir, "let.json")
    let_chosen = None
    let_full_fuse_legal = None
    by = {}
    if os.path.isfile(letp):
        let = json.load(open(letp))
        let_chosen = let.get("chosen")
        by = {c["id"]: c for c in (let.get("candidates") or [])}
        if "full_fuse" in by:
            let_full_fuse_legal = bool(by["full_fuse"].get("legal"))
    rec["let_chosen"] = let_chosen
    rec["let_full_fuse_legal"] = let_full_fuse_legal
    rec["let_full_fuse_let_legal"] = (
        bool(by["full_fuse_let"].get("legal")) if "full_fuse_let" in by else None)
    rec["let_full_fuse_let_deployable"] = (
        bool(by["full_fuse_let"].get("deployable")) if "full_fuse_let" in by else None)
    rec["let_host_emitted"] = os.path.isdir(os.path.join(pdir, "let_host"))
    gatep = os.path.join(pdir, "verify_contract.json")
    rec["contract_gate"] = json.load(open(gatep)) if os.path.isfile(gatep) else None
    rec["plan_pass"] = (
        rec.get("groups") == [["s1", "s2"], ["s3", "s4"]]
        and rec.get("domain") == "linux_userspace"
        and rec.get("adapter") != "uku_ioctl"
        and rec.get("chosen") not in ("B", "D")
        and let_full_fuse_legal is not True
        and rec.get("let_chosen") != "full_fuse"
        and not rec["let_host_emitted"]
        and (rec.get("contract_gate") or {}).get("deploy_ok") is True
    )
    rec["board_pass"] = False
    rec["end_to_end_pass"] = False
    rec["ok"] = rec["plan_pass"]
    rec["note"] = "s3 窗口 [380,520]；STKP 两段用户态；无 LET 全融合产物（规划证据）"
    return rec


def main():
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    report = {
        "kind": "pr6_paper_matrix",
        "claim": "selective kernelization; empirical; not WCET; not kernel-faster",
        "host": open("/proc/version").read().strip(),
        "workloads": {},
    }
    print("== W1 无状态 UKU ==")
    report["workloads"]["W1_stateless_uku"] = w1(OUT)
    print("== W2 有状态 PID ==")
    report["workloads"]["W2_stateful_pid"] = w2(OUT)
    print("== W3 执行器窗口 ==")
    report["workloads"]["W3_actuator_window"] = w3(OUT)
    report["all_ok"] = all(w.get("ok") for w in report["workloads"].values())
    dump(os.path.join(OUT, "report.json"), report)
    md = os.path.join(OUT, "RESULT_PR6__论文矩阵.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# PR-6 选择性内核化矩阵\n\n经验值，不是 WCET。不得从短重叠 R 写内核更快。\n\n")
        f.write("| 负载 | chosen | 域 | plan_pass | board_pass | end_to_end |\n"
                "|---|---|---|---|---|---|\n")
        for k, w in report["workloads"].items():
            f.write("| %s | %s | %s | %s | %s | %s |\n" % (
                k, w.get("chosen"), w.get("domain"),
                w.get("plan_pass"), w.get("board_pass"),
                w.get("end_to_end_pass")))
        f.write("\n")
        for k, w in report["workloads"].items():
            f.write("- **%s**：%s\n" % (k, w.get("note")))
    print(json.dumps({k: {"ok": v.get("ok"), "plan_pass": v.get("plan_pass"),
                           "board_pass": v.get("board_pass"),
                           "end_to_end_pass": v.get("end_to_end_pass"),
                           "chosen": v.get("chosen"),
                           "domain": v.get("domain"), "adapter": v.get("adapter")}
                      for k, v in report["workloads"].items()},
                     indent=2, ensure_ascii=False))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
