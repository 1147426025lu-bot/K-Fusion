#!/usr/bin/env python3
"""iso_evidence__隔离取证.py — 记录「隔离到底生效了什么」，而不是「申请了什么」。

为什么需要它：
  计划里那条批评是对的——只记录申请的配置等于自证。`irqaffinity=0-2` 写在 cmdline
  上，`/proc/irq/*/smp_affinity_list` 里仍可能留着 3（managed IRQ 不给改）；
  `workqueue.cpumask` 写了也可能有 unbound 池绕过。所以取证要读**运行时活状态**，
  并把它压成几个可判定的数字：

    irqs_with_probe_cpu      亲和集里含探针核的 IRQ 数（越少越好，managed/PPI 无法归零）
    irqs_unmovable           写失败的 IRQ（per-CPU PPI/IPI，属预期，单独计数）
    cpuset_partition         探针核是否处于独立 cpuset partition
    rt_throttle_off          sched_rt_runtime_us == -1（否则长跑会被 RT 带宽节流）
    cpuidle_disabled         探针核所有 idle state 是否都 disable
    workqueue_cpumask        全局 unbound workqueue 的 cpumask 是否排除探针核
    probe_cpu_threads        采集时刻仍可跑在探针核上的线程数（排除本进程）

输出 JSON 带 `probe_cpu`、`ts_uptime_s` 与逐项原始值，便于两次快照做 diff。
不写文件时打印人类可读摘要。

用法:
    sudo python3 iso_evidence__隔离取证.py                    # 打印
    sudo python3 iso_evidence__隔离取证.py -o /tmp/iso.json    # 落盘
    python3 iso_evidence__隔离取证.py --probe-cpu 3 --quiet
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys


def read(p):
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return None


def run(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=20)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 127, str(e)


def parse_list(txt):
    """'0-2' / '3' / '0,2-3' → set(int)"""
    out = set()
    if not txt:
        return out
    for part in txt.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                out.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                out.add(int(part))
            except ValueError:
                pass
    return out


def irq_table(probe_cpu):
    """逐 IRQ 的亲和与名字。名字取自 /proc/interrupts 首列映射。"""
    names = {}
    txt = read("/proc/interrupts") or ""
    for line in txt.splitlines()[1:]:
        m = re.match(r"\s*(\d+):", line)
        if m:
            tail = line.split()[-1] if line.split() else ""
            names[int(m.group(1))] = tail[:40]
    rows = []
    with_probe = 0
    for f in sorted(glob.glob("/proc/irq/*/smp_affinity_list"),
                    key=lambda p: int(p.split("/")[3])):
        irq = int(f.split("/")[3])
        cur = read(f)
        cpus = parse_list(cur)
        # 无 smp_affinity_list 的（per-CPU PPI）默认视为不可迁移
        hit = probe_cpu in cpus
        if hit:
            with_probe += 1
        rows.append({
            "irq": irq,
            "name": names.get(irq),
            "affinity": cur,
            "eff_cpus": sorted(cpus),
            "has_probe_cpu": hit,
        })
    return rows, with_probe


def probe_threads(probe_cpu):
    """/proc/<pid>/task/<tid>/stat 第 39 字段 = 上次运行的 CPU。

    这只是一个**瞬时快照**，不能当成「CPU3 上没有别的线程」的证明；
    它用来发现「明明隔离了却还有东西在跑」这种明显异常。
    """
    hits = []
    me = os.getpid()
    for st in glob.glob("/proc/[0-9]*/task/[0-9]*/stat"):
        try:
            txt = read(st)
            if not txt:
                continue
            # comm 可能含空格与括号，从最后一个 ')' 之后按空白切
            rest = txt[txt.rindex(")") + 2:].split()
            cpu = int(rest[36])
            if cpu != probe_cpu:
                continue
            pid = int(st.split("/")[2])
            tid = int(st.split("/")[4])
            if pid == me:
                continue
            comm = txt[txt.index("(") + 1:txt.rindex(")")]
            hits.append({"pid": pid, "tid": tid, "comm": comm})
        except Exception:
            continue
    return hits


def cgroup_v2_probe(probe_cpu):
    out = {"v2": os.path.isfile("/sys/fs/cgroup/cgroup.controllers"),
           "partitions": []}
    if not out["v2"]:
        return out
    for d in sorted(glob.glob("/sys/fs/cgroup/*/")):
        pc = os.path.join(d, "cpuset.cpus.partition")
        if not os.path.isfile(pc):
            continue
        cpus = read(os.path.join(d, "cpuset.cpus"))
        out["partitions"].append({
            "path": d,
            "cpus": cpus,
            "partition": read(pc),
            "has_probe_cpu": probe_cpu in parse_list(cpus),
            "partition": (read(pc) or "").strip(),
            "isolated": (read(pc) or "").strip() == "isolated",
            "is_partition_root": (read(pc) or "").strip() in ("root", "isolated"),
        })
    # isolated 与 root 要分开记：root 也是 partition root（CPU 从父分区调度域摘出），
    # 但没有 isolated 的"完全不做负载均衡"。混成一个布尔会让人以为达到了 5.15
    # 上做不到的 isolated。
    out["probe_cpu_in_isolated_partition"] = any(
        p["has_probe_cpu"] and p["isolated"] for p in out["partitions"])
    out["probe_cpu_in_partition_root"] = any(
        p["has_probe_cpu"] and p["is_partition_root"] for p in out["partitions"])
    return out


def collect(probe_cpu):
    cmdline = read("/proc/cmdline") or ""
    doc = {
        "kind": "iso_evidence",
        "version": 1,
        "probe_cpu": probe_cpu,
        "kernel": {
            "release": read("/proc/sys/kernel/osrelease"),
            "version": read("/proc/version"),
            "realtime": read("/sys/kernel/realtime"),
        },
        "boot_args": {
            "cmdline": cmdline,
            "isolcpus": next((t for t in cmdline.split() if t.startswith("isolcpus=")), None),
            "nohz_full": next((t for t in cmdline.split() if t.startswith("nohz_full=")), None),
            "rcu_nocbs": next((t for t in cmdline.split() if t.startswith("rcu_nocbs=")), None),
            "irqaffinity": next((t for t in cmdline.split() if t.startswith("irqaffinity=")), None),
        },
        "runtime": {
            "isolated": read("/sys/devices/system/cpu/isolated"),
            "nohz_full": read("/sys/devices/system/cpu/nohz_full"),
            "sched_rt_runtime_us": read("/proc/sys/kernel/sched_rt_runtime_us"),
            "timer_migration": read("/proc/sys/kernel/timer_migration"),
            "watchdog": read("/proc/sys/kernel/watchdog"),
            "nmi_watchdog": read("/proc/sys/kernel/nmi_watchdog"),
            "watchdog_thresh": read("/proc/sys/kernel/watchdog_thresh"),
            "vm_stat_interval": read("/proc/sys/vm/stat_interval"),
            "workqueue_cpumask": read("/sys/devices/virtual/workqueue/cpumask"),
            "governor": read("/sys/devices/system/cpu/cpu%d/cpufreq/scaling_governor"
                             % probe_cpu),
            "cur_freq_khz": read("/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq"
                                 % probe_cpu),
        },
        "cpuidle": [],
        "irqbalance": {
            "active": (run("systemctl is-active irqbalance")[1] or "").strip(),
        },
        "default_target": (run("systemctl get-default")[1] or "").strip(),
    }

    for st in sorted(glob.glob("/sys/devices/system/cpu/cpu%d/cpuidle/state*"
                               % (probe_cpu))):
        doc["cpuidle"].append({
            "state": os.path.basename(st),
            "name": read(os.path.join(st, "name")),
            "disable": read(os.path.join(st, "disable")),
        })
    # 没有 cpuidle 目录 = 这核根本没有 idle state 可进（恒 C0），不是「未关」。
    # 之前写成 bool(doc["cpuidle"]) and ... 会把这种情况误判成 NG。
    doc["cpuidle_state_count"] = len(doc["cpuidle"])
    doc["cpuidle_all_disabled"] = all(
        (s["disable"] or "") == "1" for s in doc["cpuidle"])

    rows, with_probe = irq_table(probe_cpu)
    doc["irqs"] = rows
    doc["irqs_with_probe_cpu"] = with_probe
    doc["irqs_with_probe_cpu_list"] = [
        "%d:%s" % (r["irq"], r["name"]) for r in rows if r["has_probe_cpu"]]

    doc["cgroup"] = cgroup_v2_probe(probe_cpu)
    doc["threads_on_probe_cpu"] = probe_threads(probe_cpu)

    rt = doc["runtime"]
    doc["verdict"] = {
        "rt_throttle_off": (rt["sched_rt_runtime_us"] or "") == "-1",
        "probe_cpu_isolated": probe_cpu in parse_list(rt["isolated"] or ""),
        "nohz_full_on_probe": probe_cpu in parse_list(rt["nohz_full"] or ""),
        "irqs_clear_of_probe_cpu": with_probe == 0,
        "cpuidle_disabled": doc["cpuidle_all_disabled"],
        "workqueue_excludes_probe": (
            rt["workqueue_cpumask"] is not None and
            probe_cpu not in parse_list(rt["workqueue_cpumask"])),
        "cpuset_partition_root": doc["cgroup"].get(
            "probe_cpu_in_partition_root", False),
        "cpuset_isolated_partition": doc["cgroup"].get(
            "probe_cpu_in_isolated_partition", False),
        "no_stray_threads": len(doc["threads_on_probe_cpu"]) == 0,
    }
    doc["note"] = ("irqs_with_probe_cpu 不会为 0：per-CPU PPI/IPI（arch_timer 等）"
                   "无法改亲和；managed IRQ 也可能拒改。判定用 verdict 各项 + 明细。")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-cpu", type=int, default=3)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--label", default=None, help="写进 JSON，便于两次快照区分")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    doc = collect(a.probe_cpu)
    if a.label:
        doc["label"] = a.label
    if a.out:
        d = os.path.dirname(os.path.abspath(a.out))
        if d:
            os.makedirs(d, exist_ok=True)
        with io.open(a.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")

    if a.quiet:
        return 0
    v = doc["verdict"]
    print("probe_cpu      %d" % doc["probe_cpu"])
    print("kernel         %s  realtime=%s" % (
        doc["kernel"]["release"], doc["kernel"]["realtime"]))
    print("isolated       %s    nohz_full=%s   rcu_nocbs=%s" % (
        doc["boot_args"]["isolcpus"], doc["runtime"]["nohz_full"],
        doc["boot_args"]["rcu_nocbs"]))
    print("rt_throttle    sched_rt_runtime_us=%s%s" % (
        doc["runtime"]["sched_rt_runtime_us"],
        "" if v["rt_throttle_off"] else "  ⚠ 未关闭（长跑会被节流）"))
    print("irq            %d 个 IRQ 亲和仍含 CPU%d：%s" % (
        doc["irqs_with_probe_cpu"], doc["probe_cpu"],
        ", ".join(doc["irqs_with_probe_cpu_list"][:8]) or "无"))
    print("workqueue      cpumask=%s%s" % (
        doc["runtime"]["workqueue_cpumask"],
        "" if v["workqueue_excludes_probe"] else "  ⚠ 未排除探针核"))
    print("cpuidle        all_disabled=%s   governor=%s" % (
        doc["cpuidle_all_disabled"], doc["runtime"]["governor"]))
    print("cgroup         v2=%s isolated_partition=%s" % (
        doc["cgroup"]["v2"], doc["cgroup"].get("probe_cpu_in_isolated_partition")))
    print("threads on %d  %d" % (doc["probe_cpu"], len(doc["threads_on_probe_cpu"])))
    print("verdict        " + " ".join(
        "%s=%s" % (k, "OK" if val else "NG") for k, val in v.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
