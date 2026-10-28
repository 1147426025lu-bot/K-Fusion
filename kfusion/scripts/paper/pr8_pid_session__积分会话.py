#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-8：会话内 PID 积分（显式协议，不是 STKP 自动迁移持久状态）。

内核在一次 open 中持有 integ；每拍收 g_in、更新、返回 g_out。
验收：与用户态金轨迹逐拍一致；重复序号 EILSEQ 且不改状态；关闭重开后积分回零。
UKU v1 / STKP 仍拒绝把任意 persistent 静默放进无状态邮箱。
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys

KF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("PR8_OUT", os.path.join(KF, "docs/pr8_pid_session"))
WORK = os.environ.get("PR8_WORK", "/tmp/pr8_pid")
KDIR = os.environ.get("KERNELDIR", "/root/linux-5.15.86")
PY = os.environ.get("PYTHON", sys.executable)

INTEG_MAX = 10000
INTEG_MIN = -10000


def dump(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def pid_step(integ, gin):
    err = 0 - gin
    i = integ + err
    if i > INTEG_MAX:
        i = INTEG_MAX
    elif i < INTEG_MIN:
        i = INTEG_MIN
    gout = (err << 1) + (i >> 3)
    return i, gout


def sample(seq):
    return int((seq * 13) & 0x7ff) - 256


def gold_traj(n):
    integ = 0
    out = []
    for seq in range(1, n + 1):
        integ, gout = pid_step(integ, sample(seq))
        out.append({"seq": seq, "g_in": sample(seq), "g_out": gout, "integ": integ})
    return out


def sh(cmd, env=None, cwd=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, env=e, cwd=cwd)


def build(kdir_out):
    if os.path.isdir(kdir_out):
        shutil.rmtree(kdir_out)
    os.makedirs(kdir_out)
    for name in ("pr8_pid_session.h", "pr8_pid_session_mod.c"):
        shutil.copy(os.path.join(HERE, name), os.path.join(kdir_out, name))
    with open(os.path.join(kdir_out, "Kbuild"), "w") as f:
        f.write("obj-m := kfusion_uku_pid.o\n")
        f.write("kfusion_uku_pid-y := pr8_pid_session_mod.o\n")
        f.write("ccflags-y := -I$(src)\n")
    sh(["make", "-C", KDIR, "M=" + kdir_out, "modules"])
    user = os.path.join(kdir_out, "pr8_pid_session_user")
    sh(["gcc", "-O2", "-std=gnu11", "-Wall", "-Werror",
        "-I", HERE, os.path.join(HERE, "pr8_pid_session_user.c"),
        "-o", user])
    return user


def main():
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    traj = gold_traj(40)
    dump(os.path.join(OUT, "gold_traj.json"), traj)
    report = {
        "kind": "pr8_pid_session",
        "claim": "session PID state; not generic persistent UKU; not WCET",
        "stkp_still_rejects_silent_persist": True,
        "gold_last": traj[-1],
        "board_pass": False,
        "end_to_end_pass": False,
    }
    kdir_out = os.path.join(WORK, "kmod")
    print("== 构建会话 PID 模块 ==")
    try:
        user = build(kdir_out)
    except subprocess.CalledProcessError as e:
        report["error"] = (e.output or b"").decode("utf-8", "replace")[-1200:]
        dump(os.path.join(OUT, "report.json"), report)
        print(json.dumps({"ok": False, "error": report["error"][-400:]},
                         ensure_ascii=False, indent=2))
        return 1
    kos = [f for f in os.listdir(kdir_out) if f.endswith(".ko")]
    if not kos:
        report["error"] = "no .ko"
        dump(os.path.join(OUT, "report.json"), report)
        return 1
    name = kos[0][:-3]
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    print("== insmod + 轨迹/序号/重开会话 ==")
    try:
        sh(["insmod", os.path.join(kdir_out, kos[0])])
        out = sh([user]).decode("utf-8", "replace")
    except subprocess.CalledProcessError as e:
        out = (e.output or b"").decode("utf-8", "replace")
        open(os.path.join(OUT, "logs", "user.log"), "w").write(out)
        report["error"] = out[-800:]
        subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
        dump(os.path.join(OUT, "report.json"), report)
        print(out)
        return 1
    subprocess.call(["rmmod", name], stderr=open(os.devnull, "w"))
    open(os.path.join(OUT, "logs", "user.log"), "w").write(out)
    report["log_tail"] = "\n".join(out.strip().splitlines()[-8:])
    report["board_pass"] = "all_ok" in out
    report["end_to_end_pass"] = report["board_pass"]
    dump(os.path.join(OUT, "report.json"), report)
    md = os.path.join(OUT, "RESULT_PR8__PID会话.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# PR-8 会话 PID 积分（显式协议）\n\n")
        f.write("不是 UKU v1 静默迁移。open 持有 integ，close 丢弃。\n\n")
        f.write("金轨迹末拍 `g_out=%s integ=%s`。\n\n" % (
            traj[-1]["g_out"], traj[-1]["integ"]))
        f.write("```\n%s\n```\n" % report["log_tail"])
        f.write("\nboard_pass=%s\n" % report["board_pass"])
    print(json.dumps({
        "board_pass": report["board_pass"],
        "last_gold": traj[-1],
        "log": report["log_tail"],
        "wrote": os.path.join(OUT, "report.json"),
    }, indent=2, ensure_ascii=False))
    return 0 if report["end_to_end_pass"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
