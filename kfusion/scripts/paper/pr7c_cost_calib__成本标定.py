#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-7C：实测分域成本标定，再用独立 holdout 对照预测。

流程：
  1. 仪器构建：用合法 UKU 形状生成 gold/mixed 二进制（人工 STAGE_COST 只为发出
     mixed 探针，不得当作结题选型）。
  2. 标定跑：gold 逐阶段 + start_lat；mixed 的 kernel_compute 与 T_ioctl。
  3. 用带 source_log 的成本再跑 STKP。
  4. 独立 holdout 再跑 gold/mixed，对照 predicted_R 与实测 R。

无 holdout、标定与测试同一日志、或成本无追溯，都不得标 end_to_end_pass。
经验值，不是 WCET。不得从短测宣称内核更快。
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys

KF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))
sys.path.insert(0, os.path.join(KF, "scripts"))
import stage_cost as sc  # noqa: E402
import ecos_plan_joint as stkp  # noqa: E402

SRC = os.path.join(KF, "examples/ecos/mixed_uku__无状态混合.c")
PIPE = os.path.join(KF, "scripts/ecos_from_c__从C编译.sh")
CAPS = os.path.join(KF, "contracts/examples/platform_caps.json")
OUT = os.environ.get("PR7C_OUT", os.path.join(KF, "docs/pr7c_cost_calib"))
WORK = os.environ.get("PR7C_WORK", "/tmp/pr7c_calib")
CYCLES = int(os.environ.get("CYCLES", "80"))
CPU = os.environ.get("MATRIX_CPU", "0")
FIFO = int(os.environ.get("MATRIX_FIFO", "80"))
KDIR = os.environ.get("KERNELDIR", "/root/linux-5.15.86")
PY = os.environ.get("PYTHON", sys.executable)
SKIP_BOARD = os.environ.get("SKIP_BOARD", "0") == "1"


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


def abspath_same(a, b):
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def kernel_members_from_plan(plan):
    out = []
    for g in (plan or {}).get("groups") or []:
        if g.get("domain") == "linux_kernel":
            out.extend(g.get("members") or [])
    return out


def slot_final(slot):
    val, ev = sc.parse_cost_entry(slot, from_override=True)
    return (val is not None and ev in sc.EV_FINAL
            and sc.has_provenance(slot if isinstance(slot, dict) else {}))


def calib_inputs_ok(override, ioctl, names, kernel_members):
    """标定输入：用户态每阶段、内核组成员、T_ioctl 均须 measured+日志。"""
    us = (override or {}).get("linux_userspace") or {}
    ks = (override or {}).get("linux_kernel") or {}
    why = []
    for n in names:
        if not slot_final(us.get(n)):
            why.append("user:%s" % n)
    for n in kernel_members:
        if not slot_final(ks.get(n)):
            why.append("kernel:%s" % n)
    if not slot_final(ioctl):
        why.append("T_ioctl")
    return (not why), why


def holdout_independent(calib_paths, holdout_paths):
    cg, cm = calib_paths
    hg, hm = holdout_paths
    if not all([cg, cm, hg, hm]):
        return False, "missing_log"
    if abspath_same(cg, hg) or abspath_same(cm, hm):
        return False, "same_log_as_calib"
    if abspath_same(cg, cm) or abspath_same(hg, hm):
        return False, "gold_mixed_same_file"
    return True, "ok"


def parse_run_ok(gold, mixed):
    if not gold or not mixed:
        return False
    if gold.get("digest") and gold.get("digest") != mixed.get("digest"):
        return False
    if (mixed.get("stages") is None) and mixed.get("ioctl_roundtrip") is None:
        # allow either parsed stage_cost blob or thin digest dict
        pass
    sm = mixed.get("seq_mismatch")
    ie = mixed.get("ioctl_error")
    if sm not in (None, 0) or ie not in (None, 0):
        return False
    return True


def rel_err(pred, meas):
    if pred is None or meas is None:
        return None
    meas = float(meas)
    if abs(meas) < 1e-9:
        return None
    return abs(float(pred) - meas) / meas


def instrument_cost(names):
    """只为生成 UKU 二进制。证据是 cli_override，禁止当结题成本。"""
    return {
        "linux_userspace": {n: 1.0 for n in names},
        "linux_kernel": {n: 0.1 for n in names if n not in (
            "stage_sample", "stage_sink", "sample", "sink")},
    }


def pipe_instrument(pdir):
    if os.path.isdir(pdir):
        shutil.rmtree(pdir)
    os.makedirs(pdir)
    names = ["stage_sample", "stage_add", "stage_shift", "stage_sink"]
    costp = os.path.join(pdir, "instrument_cost.json")
    dump(costp, instrument_cost(names))
    env = {
        "OUT_DIR": pdir, "SKIP_HOST": "0", "SKIP_KMOD": "0",
        "C_USER_US": "21", "C_KERNEL_US": "10", "HANDOFF_US": "1",
        "STAGE_COST": costp, "KERNELDIR": KDIR, "CYCLES": str(CYCLES),
    }
    log = sh(["bash", PIPE, SRC], env=env).decode("utf-8", "replace")
    return log


def run_pair(binp, kdir, cycles, tag, logdir):
    gold = sh(["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
               binp, "gold", str(cycles)]).decode("utf-8", "replace")
    gp = os.path.join(logdir, "%s_gold.log" % tag)
    open(gp, "w").write(gold)
    kos = [f for f in os.listdir(kdir) if f.endswith(".ko")] if os.path.isdir(kdir) else []
    if not kos:
        return gp, None, gold, None
    name = kos[0][:-3]
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    sh(["insmod", os.path.join(kdir, kos[0])])
    mixed = sh(["taskset", "-c", CPU, "chrt", "-f", str(FIFO),
                binp, "mixed", str(cycles)]).decode("utf-8", "replace")
    mp = os.path.join(logdir, "%s_mixed.log" % tag)
    open(mp, "w").write(mixed)
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    return gp, mp, gold, mixed


def thin_run(parsed, text):
    rec = dict(parsed)
    rec["seq_mismatch"] = None
    rec["ioctl_error"] = None
    rec["digest"] = None
    import re
    m = re.search(r"# digest: ([0-9a-f]+)", text or "")
    if m:
        rec["digest"] = m.group(1)
    m = re.search(r"# uku_seq_mismatch: (\d+)/", text or "")
    if m:
        rec["seq_mismatch"] = int(m.group(1))
    m = re.search(r"# uku_ioctl_error: (\d+)/", text or "")
    if m:
        rec["ioctl_error"] = int(m.group(1))
    return rec


def solve_from_override(teir, dag, platforms, override, ioctl, c_user):
    v = stkp.load_verifier()
    return stkp.solve(
        teir, v, c_user=float(c_user), c_kernel=float(c_user),
        extra_kernel=0.0, handoff_us=0.0, override=override,
        dag=dag, platforms=platforms, ioctl_cost=ioctl)


def mixed_cost_record(doc):
    for c in doc.get("candidates") or []:
        if c.get("domain") == "mixed" and (
                (c.get("plan") or {}).get("meta") or {}).get("xdom_adapter") == "uku_ioctl":
            return c
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    host = open("/proc/version").read().strip()
    report = {
        "kind": "pr7c_cost_calib",
        "claim": "empirical; not WCET; not kernel-faster from short R",
        "host": host,
        "calib_pass": False,
        "holdout_pass": False,
        "end_to_end_pass": False,
        "note": "",
    }

    env_cg = os.environ.get("PR7C_CALIB_GOLD")
    env_cm = os.environ.get("PR7C_CALIB_MIXED")
    env_hg = os.environ.get("PR7C_HOLDOUT_GOLD")
    env_hm = os.environ.get("PR7C_HOLDOUT_MIXED")
    teir_p = os.environ.get("PR7C_TEIR")
    dag_p = os.environ.get("PR7C_DAG")
    plan_p = os.environ.get("PR7C_PLAN")

    pdir = os.path.join(WORK, "pipe")
    if not SKIP_BOARD:
        print("== 仪器构建（人工成本只为发出 UKU，不是结题选型）==")
        try:
            plog = pipe_instrument(pdir)
            open(os.path.join(OUT, "logs", "instrument_pipe.log"), "w").write(plog)
        except subprocess.CalledProcessError as e:
            report["note"] = "instrument_build_failed"
            report["error"] = (e.output or b"").decode("utf-8", "replace")[-800:]
            dump(os.path.join(OUT, "report.json"), report)
            print(json.dumps(report, indent=2, ensure_ascii=False)[:2000])
            return 1
        teir_p = os.path.join(pdir, "task.teir.json")
        dag_p = os.path.join(pdir, "task.dag.json")
        plan_p = os.path.join(pdir, "plan.json")
        binp = os.path.join(pdir, "driver")
        kdir = os.path.join(pdir, "kmod_uku")
        print("== 标定跑 ==")
        env_cg, env_cm, _, _ = run_pair(
            binp, kdir, CYCLES, "calib", os.path.join(OUT, "logs"))
        print("== holdout 跑 ==")
        env_hg, env_hm, _, _ = run_pair(
            binp, kdir, CYCLES, "holdout", os.path.join(OUT, "logs"))

    if not teir_p or not os.path.isfile(teir_p):
        report["note"] = "no_teir"
        dump(os.path.join(OUT, "report.json"), report)
        return 1

    teir = json.load(open(teir_p))
    dag = json.load(open(dag_p)) if dag_p and os.path.isfile(dag_p) else None
    platforms = json.load(open(CAPS))
    plan = json.load(open(plan_p)) if plan_p and os.path.isfile(plan_p) else {}
    names = [n["name"] for n in teir.get("nodes") or []]
    kmem = kernel_members_from_plan(plan) or [
        n for n in names if n not in ("stage_sample", "stage_sink")]

    calib_paths = (env_cg, env_cm)
    hold_paths = (env_hg, env_hm)
    if not (env_cg and env_cm and os.path.isfile(env_cg) and os.path.isfile(env_cm)):
        report["note"] = "no_calib_logs"
        dump(os.path.join(OUT, "report.json"), report)
        return 1

    gold = sc.load_user_stage_log(env_cg)
    mixed = sc.load_user_stage_log(env_cm)
    gold_t = open(env_cg, encoding="utf-8", errors="replace").read()
    mixed_t = open(env_cm, encoding="utf-8", errors="replace").read()
    gold_run = thin_run(gold, gold_t)
    mixed_run = thin_run(mixed, mixed_t)
    ov, ioctl = sc.override_from_gold_mixed(gold, mixed, names, kmem)
    dump(os.path.join(OUT, "stage_cost.json"), ov)
    dump(os.path.join(OUT, "ioctl_cost.json"), ioctl)
    ok_in, why_in = calib_inputs_ok(ov, ioctl, names, kmem)
    lat = (gold.get("start_lat") or {}).get("avg_us") or 21.0
    doc = solve_from_override(teir, dag, platforms, ov, ioctl, lat)
    rec = doc.get("chosen_record") or {}
    cost = rec.get("cost") or {}
    mixed_rec = mixed_cost_record(doc)
    report["calib"] = {
        "logs": {"gold": os.path.abspath(env_cg), "mixed": os.path.abspath(env_cm)},
        "inputs_ok": ok_in,
        "inputs_why": why_in,
        "start_lat_avg_us": lat,
        "T_ioctl": ioctl,
        "chosen": doc.get("chosen"),
        "claim": doc.get("claim"),
        "domain": rec.get("domain"),
        "grade": cost.get("grade"),
        "final_rankable": cost.get("final_rankable"),
        "predicted_R_us": cost.get("predicted_R_us"),
        "mixed_predicted_R_us": ((mixed_rec or {}).get("cost") or {}).get(
            "predicted_R_us"),
        "instrument_cost_not_used": True,
    }
    report["calib_pass"] = bool(ok_in and rec)

    ind, ind_why = holdout_independent(calib_paths, hold_paths)
    hold = {"independent": ind, "why": ind_why}
    if ind and env_hg and env_hm and os.path.isfile(env_hg) and os.path.isfile(env_hm):
        hg = sc.load_user_stage_log(env_hg)
        hm = sc.load_user_stage_log(env_hm)
        hgt = open(env_hg, encoding="utf-8", errors="replace").read()
        hmt = open(env_hm, encoding="utf-8", errors="replace").read()
        hg_run, hm_run = thin_run(hg, hgt), thin_run(hm, hmt)
        hold["logs"] = {
            "gold": os.path.abspath(env_hg), "mixed": os.path.abspath(env_hm)}
        hold["digest_match"] = bool(
            hg_run.get("digest") and hg_run.get("digest") == hm_run.get("digest"))
        hold["seq_ok"] = (hm_run.get("seq_mismatch") == 0
                          and hm_run.get("ioctl_error") == 0)
        hold["gold_R_avg_us"] = (hg.get("response") or {}).get("avg_us")
        hold["mixed_R_avg_us"] = (hm.get("response") or {}).get("avg_us")
        hold["gold_R_p50_us"] = (hg.get("response") or {}).get("p50_us")
        hold["mixed_R_p50_us"] = (hm.get("response") or {}).get("p50_us")
        chosen_meas = hold["gold_R_avg_us"]
        if rec.get("domain") == "mixed":
            chosen_meas = hold["mixed_R_avg_us"]
        hold["chosen_measured_R_avg_us"] = chosen_meas
        hold["pred_rel_err"] = rel_err(cost.get("predicted_R_us"), chosen_meas)
        hold["mixed_pred_rel_err"] = rel_err(
            ((mixed_rec or {}).get("cost") or {}).get("predicted_R_us"),
            hold["mixed_R_avg_us"])
        hold["mixed_faster_on_holdout"] = (
            hold["mixed_R_avg_us"] is not None
            and hold["gold_R_avg_us"] is not None
            and hold["mixed_R_avg_us"] < hold["gold_R_avg_us"])
        report["holdout_pass"] = bool(
            hold["digest_match"] and hold["seq_ok"]
            and hold["gold_R_avg_us"] is not None
            and hold["mixed_R_avg_us"] is not None
            and hg.get("stages") and hm.get("T_ioctl"))
    else:
        report["holdout_pass"] = False
    report["holdout"] = hold
    report["end_to_end_pass"] = report["calib_pass"] and report["holdout_pass"]
    notes = []
    if not report["holdout_pass"]:
        notes.append("无独立 holdout 不得标板级/预测验证通过")
    if hold.get("gold_R_avg_us") is not None and hold.get("mixed_faster_on_holdout") is False:
        notes.append("holdout 未显示 mixed 更快")
    report["note"] = "；".join(notes)
    dump(os.path.join(OUT, "stkp_measured.json"), {
        "chosen": doc.get("chosen"), "claim": doc.get("claim"),
        "domain": rec.get("domain"), "cost": cost,
    })
    dump(os.path.join(OUT, "report.json"), report)
    md = os.path.join(OUT, "RESULT_PR7C__成本标定.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# PR-7C 实测成本标定（经验值，不是 WCET）\n\n")
        f.write("人工 STAGE_COST 只用于仪器构建。结题选型必须带来源日志。\n\n")
        f.write("| 项 | 值 |\n|---|---|\n")
        f.write("| calib_pass | %s |\n" % report["calib_pass"])
        f.write("| holdout_pass | %s |\n" % report["holdout_pass"])
        f.write("| end_to_end_pass | %s |\n" % report["end_to_end_pass"])
        f.write("| STKP chosen | %s |\n" % report["calib"].get("chosen"))
        f.write("| domain | %s |\n" % report["calib"].get("domain"))
        f.write("| predicted_R_us | %s |\n" % report["calib"].get("predicted_R_us"))
        f.write("| holdout gold R avg | %s |\n" % hold.get("gold_R_avg_us"))
        f.write("| holdout mixed R avg | %s |\n" % hold.get("mixed_R_avg_us"))
        f.write("| pred rel err | %s |\n" % hold.get("pred_rel_err"))
        f.write("| mixed faster on holdout | %s |\n" % hold.get("mixed_faster_on_holdout"))
        f.write("\n%s\n" % (report.get("note") or ""))
    print(json.dumps({
        "calib_pass": report["calib_pass"],
        "holdout_pass": report["holdout_pass"],
        "end_to_end_pass": report["end_to_end_pass"],
        "chosen": report["calib"].get("chosen"),
        "domain": report["calib"].get("domain"),
        "final_rankable": report["calib"].get("final_rankable"),
        "pred": report["calib"].get("predicted_R_us"),
        "holdout": {k: hold.get(k) for k in (
            "why", "digest_match", "pred_rel_err",
            "gold_R_avg_us", "mixed_R_avg_us", "mixed_faster_on_holdout")},
        "wrote": os.path.join(OUT, "report.json"),
    }, indent=2, ensure_ascii=False))
    return 0 if report["end_to_end_pass"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
