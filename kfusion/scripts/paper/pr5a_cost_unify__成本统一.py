#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-5A：用可追溯日志回填分阶段成本，Ĉ 与 DAG 窗口共用 CandidateSchedule。

内核 rsf 无逐阶段探针 → missing，不得均分、不得标 measured。
裸 CLI 覆盖不得结题。经验值，不是 WCET。
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

SRC = os.path.join(KF, "examples/ecos/five_stage_loop__闭环五阶段示例.c")
PIPE = os.path.join(KF, "scripts/ecos_from_c__从C编译.sh")
CAPS = os.path.join(KF, "contracts/examples/platform_caps.json")
OUT = os.environ.get("PR5A_OUT", os.path.join(KF, "docs/pr5a_cost_unify"))
WORK = os.environ.get("PR5A_WORK", "/tmp/pr5a_cost")
LOG = os.environ.get(
    "PR5A_USER_LOG",
    os.path.join(KF, "docs/pr4c_bd_paired/logs/B_0.log"))
PY = os.environ.get("PYTHON", sys.executable)


def dump(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def pipe(outdir):
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir)
    env = os.environ.copy()
    env.update({
        "OUT_DIR": outdir, "SKIP_HOST": "1", "SKIP_KMOD": "1",
        "C_USER_US": "21", "C_KERNEL_US": "21", "CYCLES": "40",
    })
    subprocess.check_output(["bash", PIPE, SRC], stderr=subprocess.STDOUT, env=env)


def main():
    os.makedirs(OUT, exist_ok=True)
    parsed = sc.load_user_stage_log(LOG)
    if not parsed["stages"]:
        print("日志无 stat_exec_stage_*：%s" % LOG, file=sys.stderr)
        return 2

    print("== 1) 生成 TEIR/DAG ==")
    pdir = os.path.join(WORK, "pipe")
    pipe(pdir)
    teir = json.load(open(os.path.join(pdir, "task.teir.json")))
    dag = json.load(open(os.path.join(pdir, "task.dag.json")))
    names = [n["name"] for n in teir.get("nodes") or []]
    ov = sc.build_override(parsed["stages"], teir_names=names)
    dump(os.path.join(OUT, "stage_cost.json"), ov)
    platforms = json.load(open(CAPS))
    v = stkp.load_verifier()
    lat = (parsed.get("start_lat") or {}).get("avg_us") or 21.0

    print("== 2) 可追溯 measured 回填（内核 missing） ==")
    traced = stkp.solve(
        teir, v, c_user=lat, c_kernel=lat, override=ov,
        dag=dag, platforms=platforms)
    dump(os.path.join(OUT, "stkp_traced.json"), {
        "chosen": traced.get("chosen"),
        "claim": traced.get("claim"),
        "domain": (traced.get("chosen_record") or {}).get("domain"),
        "final_rankable": ((traced.get("chosen_record") or {}).get("cost")
                           or {}).get("final_rankable"),
        "evidence": ((traced.get("chosen_record") or {}).get("cost")
                     or {}).get("evidence"),
        "schedule": ((traced.get("chosen_record") or {}).get("plan")
                     or {}).get("schedule"),
        "incomparable": traced.get("pruned", {}).get("incomparable"),
    })

    print("== 3) 伪造 measured（无 source_log）不得结题 ==")
    fake_slots = {n: {"value": 0.1, "evidence": "measured"} for n in names}
    fake = stkp.solve(
        teir, v, c_user=lat, c_kernel=10.0,
        override={"linux_userspace": fake_slots, "linux_kernel": fake_slots},
        dag=dag, platforms=platforms)

    print("== 4) Ĉ 与窗口共用覆盖 exec ==")
    # 放大用户态 FIR：Ĉ 的 compute 与 plan.schedule.exec_us 必须同值
    ov2 = json.loads(json.dumps(ov))
    fir = ov2["linux_userspace"].get("stage_fir") or {}
    fir = dict(fir)
    fir["value"] = 400.0
    fir["avg_us"] = 400.0
    ov2["linux_userspace"]["stage_fir"] = fir
    blown = stkp.solve(
        teir, v, c_user=lat, c_kernel=lat, override=ov2,
        dag=dag, platforms=platforms)
    sched = ((blown.get("chosen_record") or {}).get("plan") or {}).get("schedule") or {}
    cost = (blown.get("chosen_record") or {}).get("cost") or {}

    report = {
        "kind": "pr5a_cost_unify",
        "claim": "empirical; not WCET; cli is not measured",
        "source_log": os.path.abspath(LOG),
        "user_stages_avg_us": {
            n: (ov["linux_userspace"].get(n) or {}).get("avg_us")
            for n in names
        },
        "start_lat_avg_us": lat,
        "kernel": "missing_per_stage",
        "traced": {
            "chosen": traced.get("chosen"),
            "claim": traced.get("claim"),
            "domain": (traced.get("chosen_record") or {}).get("domain"),
            "final_rankable": ((traced.get("chosen_record") or {}).get("cost")
                               or {}).get("final_rankable"),
        },
        "forged_measured": {
            "claim": fake.get("claim"),
            "chosen": fake.get("chosen"),
            "final_rankable": ((fake.get("chosen_record") or {}).get("cost")
                               or {}).get("final_rankable"),
        },
        "blown_fir_400us": {
            "chosen": blown.get("chosen"),
            "claim": blown.get("claim"),
            "schedule_exec_us": sched.get("exec_us"),
            "compute_us": cost.get("compute_us"),
            "same_fir": (sched.get("exec_us") or {}).get("stage_fir") == 400.0,
        },
        "checks": {
            "traced_is_measured_userspace": (
                traced.get("claim") == "empirical_model_not_wcet"
                and (traced.get("chosen_record") or {}).get("domain")
                == "linux_userspace"),
            "forged_not_final": fake.get("claim") != "empirical_model_not_wcet",
            "schedule_matches_cost": (
                (sched.get("exec_us") or {}).get("stage_fir") == 400.0
                and cost.get("compute_us") is not None),
        },
    }
    dump(os.path.join(OUT, "report.json"), report)
    md = os.path.join(OUT, "RESULT_PR5A__成本统一.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# PR-5A 成本与时间验证统一（经验值，不是 WCET）\n\n")
        f.write("日志 `%s`。Ĉ 用 avg；内核逐阶段 missing。\n\n" % LOG)
        f.write("| 阶段 | 用户态 avg (µs) | 内核 |\n|---|---:|---|\n")
        for n in names:
            slot = ov["linux_userspace"].get(n) or {}
            f.write("| %s | %s | missing |\n" % (n, slot.get("avg_us")))
        f.write("\n可追溯回填：chosen=%s claim=%s final=%s\n\n"
                % (report["traced"]["chosen"], report["traced"]["claim"],
                   report["traced"]["final_rankable"]))
        f.write("伪造 measured：claim=%s（不得 empirical_model_not_wcet）\n\n"
                % report["forged_measured"]["claim"])
        f.write("FIR=400µs 覆盖后 schedule.exec_us.stage_fir=%s，与 Ĉ 同源：%s\n"
                % ((sched.get("exec_us") or {}).get("stage_fir"),
                   report["checks"]["schedule_matches_cost"]))
    print(json.dumps(report["checks"], indent=2, ensure_ascii=False))
    ok = all(report["checks"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    sys.exit(main())
