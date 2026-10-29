#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr11_env_evidence__环境证据.py — PR-12 最小集：冻结「这份画像是哪台机器测的」。

审查提出的 P0：画像里 `measure_env.kernel` 是 null，等于没有平台身份。一份
不知道该跑在哪个内核/哪块板/哪种抢占模式上的 Q99，不能拿去和别的数据比，
更不能当论文证据。

本脚本只做取证，不做判断：
  · 内核身份：release + version 行 + **PREEMPT 类别**（PREEMPT / PREEMPT_RT / NONE）
  · 板级身份：device-tree model + compatible + nproc
  · 调度条件：CPU 亲和、sched_rt_runtime_us、governor 是否存在、cmdline 里有没有 isolcpus
  · 代码身份：git rev + 被测源码 sha256
  · 采集时刻与主机名

`complete=False` 时必须**拒绝进入决策**——缺关键字段的画像不能当证据用。
哪些字段算关键写在 REQUIRED 里，改动要同步改文档，不许靠记忆。

用法（在被测机器上跑）：
  python3 kfusion/scripts/pr11_env_evidence__环境证据.py \
      --source kfusion/examples/ecos/mixed_pid_stateful__积分混合.c \
      -o kfusion/docs/pr11_deploy_objective/env_evidence.json
"""
from __future__ import print_function

import argparse
import hashlib
import io
import json
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)

# 缺任一项就不算「有平台身份」。不在这里的字段缺失只记 missing，不阻断。
REQUIRED = ("kernel.release", "kernel.preempt_class", "kernel.config_source",
            "host.model", "host.nproc", "sched.pin_cpu",
            "sched.rt_runtime_us", "code.git_rev", "code.source_sha256")


def readf(path, default=None):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return default


def read_dt(path):
    try:
        with open(path, "rb") as f:
            return f.read().replace(b"\x00", b"").decode("utf-8", "replace")
    except Exception:
        return None


def run(cmd):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        out = p.communicate()[0]
        return p.returncode, (out.decode("utf-8", "replace")
                             if isinstance(out, bytes) else out).strip()
    except Exception as exc:
        return 127, str(exc)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def kernel_cfg(release):
    """从 /boot/config-<rel> 取抢占类别。拿不到就返回 None，**不猜**。"""
    path = "/boot/config-%s" % release
    txt = readf(path)
    if txt is None:
        return None, None
    kv = {}
    for line in txt.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            k, v = line.split("=", 1)
            kv[k] = v
    return kv, path


def preempt_class(kv, version_line):
    """PREEMPT_RT / PREEMPT / PREEMPT_VOLUNTARY / NONE 四类，来自配置不是版本串。"""
    if not kv:
        return None
    if kv.get("CONFIG_PREEMPT_RT") == "y":
        return "PREEMPT_RT"
    if kv.get("CONFIG_PREEMPT") == "y":
        return "PREEMPT"
    if kv.get("CONFIG_PREEMPT_VOLUNTARY") == "y":
        return "PREEMPT_VOLUNTARY"
    if kv.get("CONFIG_PREEMPT_NONE") == "y":
        return "NONE"
    # 配置里没写 = 不可判定；用版本串只在能交叉验证时作旁证
    if "PREEMPT_RT" in (version_line or ""):
        return "PREEMPT_RT(版本串推断,未交叉验证)"
    return None


def collect(pin_cpu, source, extra=None):
    release = os.uname()[2]
    version_line = readf("/proc/version") or ""
    kv, cfg_path = kernel_cfg(release)
    cmdline = readf("/proc/cmdline") or ""
    doc = {
        "kind": "env_evidence",
        "version": 1,
        "host": {
            "hostname": socket.gethostname(),
            "model": read_dt("/proc/device-tree/model"),
            "compatible": read_dt("/proc/device-tree/compatible"),
            "nproc": os.cpu_count(),
            "machine": os.uname()[4],
        },
        "kernel": {
            "release": release,
            "version_line": version_line,
            "preempt_class": preempt_class(kv, version_line),
            "config_source": cfg_path,
            "config": {k: (kv or {}).get(k) for k in
                       ("CONFIG_PREEMPT", "CONFIG_PREEMPT_RT",
                        "CONFIG_PREEMPT_VOLUNTARY", "CONFIG_PREEMPT_NONE",
                        "CONFIG_HZ", "CONFIG_HZ_250", "CONFIG_NO_HZ_IDLE",
                        "CONFIG_NO_HZ_FULL", "CONFIG_FTRACE")},
        },
        "boot": {
            "cmdline": cmdline,
            "isolcpus": ("isolcpus=" in cmdline),
            "nohz_full": ("nohz_full=" in cmdline),
            "console": next((t.split("=", 1)[1] for t in cmdline.split()
                             if t.startswith("console=")), None),
        },
        "sched": {
            "pin_cpu": pin_cpu,
            "rt_runtime_us": readf("/proc/sys/kernel/sched_rt_runtime_us"),
            "rt_period_us": readf("/proc/sys/kernel/sched_rt_period_us"),
            "governor": readf("/sys/devices/system/cpu/cpu%d/cpufreq/"
                              "scaling_governor" % pin_cpu),
            "freq_control_present": os.path.isdir(
                "/sys/devices/system/cpu/cpu%d/cpufreq" % pin_cpu),
        },
        "code": {
            "git_rev": (run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"])[1]
                        if os.path.isdir(os.path.join(REPO, ".git")) else None),
            "git_dirty": bool(run(["git", "-C", REPO, "status", "--porcelain"])[1])
                          if os.path.isdir(os.path.join(REPO, ".git")) else None,
            "source": os.path.relpath(source, REPO) if source else None,
            "source_sha256": (sha256_file(source)
                              if source and os.path.isfile(source) else None),
        },
        "uptime_s": readf("/proc/uptime"),
        "note": "配置取自 /boot/config-<release>；抢占类别以配置为准，"
                "版本串只在无配置时作旁证并标注。缺失即 complete=False。",
    }
    if extra:
        doc.update(extra)

    # 「树脏」必须区分源码脏与产物脏：采集本身在写 docs/ 下的 json，
    # 若不分，每次取证都 dirty=True，dirty 这个字段就废了。
    rc, porcelain = run(["git", "-C", REPO, "status", "--porcelain"])
    files = [ln[3:].strip().strip('"') for ln in porcelain.splitlines()
             if ln.strip()] if rc == 0 else []
    OUT_PREFIX = ("kfusion/docs/", "analysis/build/", "build/", "results/")
    src_dirty = [f for f in files if not f.startswith(OUT_PREFIX)]
    doc["code"]["dirty_files"] = files[:20]
    doc["code"]["dirty_source"] = src_dirty[:20]
    doc["code"]["dirty_means"] = (
        "dirty_source 非空 = 被测代码与 git_rev 不一致，该次结果不可复现；"
        "只有产物/文档脏不算")

    def get(path):
        cur = doc
        for k in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    missing = [k for k in REQUIRED if get(k) in (None, "")]
    doc["complete"] = not missing
    doc["missing"] = missing
    return doc


def main():
    ap = argparse.ArgumentParser(description="冻结实验环境证据")
    ap.add_argument("--pin-cpu", type=int, default=3)
    ap.add_argument("--source", default=None, help="被测源码，记 sha256")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    doc = collect(a.pin_cpu, a.source)
    d = os.path.dirname(os.path.abspath(a.out))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    k = doc["kernel"]
    print("host   %s / %s / nproc=%s" % (doc["host"]["model"],
                                         doc["host"]["compatible"],
                                         doc["host"]["nproc"]))
    print("kernel %s %s  preempt=%s  HZ=%s  ftrace=%s" % (
        k["release"], k["config"].get("CONFIG_HZ"),
        k["preempt_class"], k["config"].get("CONFIG_HZ"),
        k["config"].get("CONFIG_FTRACE")))
    print("sched  pin_cpu=%s rt_runtime=%s governor=%s cpufreq=%s" % (
        doc["sched"]["pin_cpu"], doc["sched"]["rt_runtime_us"],
        doc["sched"]["governor"], doc["sched"]["freq_control_present"]))
    print("code   %s dirty=%s src_dirty=%d src=%s" % (
        doc["code"]["git_rev"], doc["code"]["git_dirty"],
        len(doc["code"]["dirty_source"] or []),
        (doc["code"]["source_sha256"] or "")[:16]))
    print("complete=%s missing=%s" % (doc["complete"], doc["missing"]))
    print("wrote %s" % a.out)
    return 0 if doc["complete"] else 5


if __name__ == "__main__":
    sys.exit(main())
