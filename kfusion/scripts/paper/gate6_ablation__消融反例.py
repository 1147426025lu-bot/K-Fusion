#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate6_ablation__消融反例.py — Gate 6：论文消融 + 反例矩阵（经验结论，不是 WCET）

把方案里的反例和三类程序收到一份可复核 JSON。不编造板级抖动数字。
"""
from __future__ import print_function

import importlib.util
import io
import json
import os
import subprocess
import sys

KF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def loadj(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def rec(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def main():
    teir_mod = load_mod("teir", os.path.join(KF, "ir", "temporal_effect_ir.py"))
    ver_mod = load_mod("pv", os.path.join(KF, "verify", "plan_verify.py"))
    abcd_mod = load_mod("abcd", os.path.join(KF, "scripts", "ecos",
                                            "candidate_generate.py"))
    let_mod = load_mod("let", os.path.join(KF, "scripts", "ecos", "let_decide.py"))
    caps = loadj(os.path.join(KF, "contracts", "examples", "platform_caps.json"))
    rows = []

    # --- 反例矩阵 ---
    pid_teir = teir_mod.build_teir(
        loadj(os.path.join(KF, "ir", "test", "pid_unread_output.dag.json")),
        loadj(os.path.join(KF, "ir", "test", "pid_unread_output.summary.json")),
        {}, caps)
    n = teir_mod.node(pid_teir, "stage_pid")
    rows.append(rec(
        "PID 未读输出 ≠ 可删",
        n["must_preserve"] and "@g_integ" in n["persistent"]
        and "@g_act" in n["unread_this_job"],
        "persistent=%s unread=%s" % (n["persistent"], n["unread_this_job"])))

    alias_teir = teir_mod.build_teir(
        loadj(os.path.join(KF, "ir", "test", "alias_uncertain.dag.json")),
        {}, {}, caps)
    rows.append(rec(
        "别名未证不得当无依赖重排",
        alias_teir["transform"]["policy"] == "keep_deps",
        alias_teir["transform"]["policy"]))

    mmio_teir = teir_mod.build_teir(
        loadj(os.path.join(KF, "ir", "test", "mmio_sensor.dag.json")),
        loadj(os.path.join(KF, "ir", "test", "mmio_sensor.summary.json")),
        {}, caps)
    mmio_n = teir_mod.node(mmio_teir, "sensor_prepare")
    rows.append(rec(
        "MMIO 不得进 linux_kernel",
        "linux_kernel" not in mmio_n["allowed_domains"]
        and "nuttx" in mmio_n["allowed_domains"],
        "allowed=%s" % mmio_n["allowed_domains"]))

    five = os.path.join(KF, "analysis", "build", "gate1_from_c", "task.teir.json")
    planp = os.path.join(KF, "analysis", "build", "gate1_from_c", "plan.json")
    if os.path.isfile(five) and os.path.isfile(planp):
        teir5 = loadj(five)
        plan5 = loadj(planp)
        ok, _ = ver_mod.verify(teir5, plan5)
        drop = ver_mod.mutate_drop(plan5, "stage_pid")
        ok_d, d_d = ver_mod.verify(teir5, drop)
        age = ver_mod.mutate_age(plan5, "stage_plant", "stage_sample", 2)
        ok_a, d_a = ver_mod.verify(teir5, age)
        swap = ver_mod.mutate_swap(plan5, "stage_sample", "stage_plant")
        ok_s, d_s = ver_mod.verify(teir5, swap)
        rows.append(rec("合法方案通过独立验证器", ok, "PASS" if ok else "FAIL"))
        rows.append(rec("变异漏 PID", (not ok_d) and any(x["code"]=="E_DROP_STATE" for x in d_d),
                        ",".join(x["code"] for x in d_d[:4])))
        rows.append(rec("变异年龄 k-2", (not ok_a) and any(x["code"]=="E_AGE" for x in d_a),
                        ",".join(x["code"] for x in d_a[:4])))
        rows.append(rec("变异本拍先写后读", (not ok_s) and any(
            x["code"]=="E_PREV_JOB_BECAME_SAME" for x in d_s),
                        ",".join(x["code"] for x in d_s[:4])))

        doc = abcd_mod.generate(teir5, 21, 21, 80, 0, ver_mod)
        rows.append(rec(
            "内核化无收益 → 选用户态",
            doc["chosen"] == "B",
            "chosen=%s extra=80" % doc["chosen"]))
        always_d = next(c for c in doc["candidates"] if c["id"] == "D")
        chosen = next(c for c in doc["candidates"] if c["id"] == doc["chosen"])
        rows.append(rec(
            "消融 always-kernel 更差",
            always_d["cost"]["total_us"] > chosen["cost"]["total_us"],
            "D=%s chosen %s=%s" % (always_d["cost"]["total_us"],
                                   doc["chosen"], chosen["cost"]["total_us"])))

        tpath = os.path.join(KF, "contracts", "examples", "five_stage_let.time.json")
        tcon = loadj(tpath)
        let_hi = let_mod.decide(teir5, tcon, ver_mod, 21, 200, 0)
        let_lo = let_mod.decide(teir5, tcon, ver_mod, 21, 21, 0)
        by_hi = {c["id"]: c for c in let_hi["candidates"]}
        rows.append(rec(
            "全融合无 LET 非法",
            not by_hi["full_fuse"]["legal"],
            by_hi["full_fuse"]["why"]))
        rows.append(rec(
            "LET 更贵则不用",
            let_hi["chosen"] == "selective_pins"
            and not let_hi["chosen_record"]["cost"]["uses_let"],
            "chosen=%s" % let_hi["chosen"]))
        rows.append(rec(
            "LET 便宜可以采用",
            let_lo["chosen"] == "full_fuse_let",
            "chosen=%s" % let_lo["chosen"]))
        rows.append(rec(
            "严格周期拒绝 1.5T 重同步",
            not by_hi["stretch_period"]["legal"],
            by_hi["stretch_period"]["why"]))
    else:
        rows.append(rec("五阶段 TEIR/plan 产物", False, "先跑 ecos_from_c"))

    ew = let_mod.decide(
        loadj(os.path.join(KF, "ir", "test", "empty_window.teir.json")),
        loadj(os.path.join(KF, "ir", "test", "empty_window.time.json")),
        ver_mod, 21, 21, 0)
    rows.append(rec(
        "相位窗交集为空 → 整份拒绝",
        ew["n_legal"] == 0 and any(
            any(d["code"]=="E_WINDOW_EMPTY" for d in c["diagnostics"])
            for c in ew["candidates"]),
        "n_legal=%d" % ew["n_legal"]))

    # K-C0 malloc
    extract = os.path.join(KF, "frontend", "stage_extract", "stage_extract.py")
    badc = os.path.join(KF, "frontend", "stage_extract", "test", "kc0_malloc_reject.c")
    proc = subprocess.Popen(
        [sys.executable, extract, badc, "-o", "/tmp/gate6_bad_stages.json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, err = proc.communicate()
    err_s = err.decode("utf-8", "replace")
    rows.append(rec(
        "K-C0 malloc fail-closed",
        proc.returncode != 0 and "E_KC0_FORBIDDEN_CALL" in err_s,
        "exit=%s" % proc.returncode))

    # 三类程序（能跑的记覆盖）
    classes = [
        {"id": "stateless", "path": os.path.join(KF, "examples", "ecos",
            "teir_stateless_pipe__无状态流水线.c"), "role": "无状态流水线"},
        {"id": "closed_loop", "path": os.path.join(KF, "examples", "ecos",
            "five_stage_loop__闭环五阶段示例.c"), "role": "带状态闭环"},
        {"id": "multi_constraint", "path": os.path.join(KF, "ir", "test",
            "empty_window.teir.json"), "role": "多约束窗口（非法交集）"},
    ]

    passed = sum(1 for r in rows if r["ok"])
    report = {
        "version": 1,
        "kind": "gate6_ablation",
        "claim_limit": "经验性结论；本报告不含 WCET / 最坏情况证明",
        "rows": rows,
        "pass": passed,
        "fail": len(rows) - passed,
        "program_classes": classes,
        "stop_loss": {
            "A_automation": "新 C 走 ecos_from_c，不手写 DAG",
            "B_alias": "别名未证 keep_deps，不扩大语言",
            "C_joint": "always-kernel / always-LET 在反例上劣于联合选择",
        },
    }
    out_dir = os.path.join(KF, "analysis", "build", "gate6")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    out = os.path.join(out_dir, "report.json")
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("Gate 6  %d pass / %d fail" % (report["pass"], report["fail"]))
    for r in rows:
        print("  %s %s" % ("✅" if r["ok"] else "❌", r["name"]))
        if not r["ok"]:
            print("      %s" % r["detail"])
    print("wrote %s" % out)
    return 0 if report["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
