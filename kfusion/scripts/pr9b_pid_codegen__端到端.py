#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr9b_pid_codegen__端到端.py — PR-10A：PR-9B 的一键上板复现。

审查（3aac154）P1 指出的两个问题，本脚本各解一半：

  1. 「没有一键重现脚本」→ 本脚本固定源码/编译选项/周期/轮数，跑完
     生成 → 编译 → 加载 → 逐拍验证 → 出报告，一条命令。
  2. 「digest 排除了内核持有的 g_integ，状态延续性没验」→ 借助 PR-10A
     新增的 KFUSION_UKU_STATE_IOC 读回内核那份会话状态，与 gold（用户态）
     同名字段比对；读不回来 = 失败，不许默认通过。

诚实边界（写进报告，不藏在脚注里）：
  · 4 这条只证明「这 40 拍在这台机器上一致」，不是 WCET，也不是形式化等价。
  · digest 仍按 PR-9B 口径（不含 session）；session 单独一行比对。
  · 树不干净时报告标 dirty=true —— 那次结果不能当可复现证据。

用法：
  python3 kfusion/scripts/pr9b_pid_codegen__端到端.py              # 默认 40 拍
  python3 kfusion/scripts/pr9b_pid_codegen__端到端.py --cycles 200
  python3 kfusion/scripts/pr9b_pid_codegen__端到端.py --no-board   # 只跑生成

退出码：0 通过 / 2 环境缺失 / 3 生成物不符 / 4 板级不一致
"""
from __future__ import print_function

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)
SRC = os.path.join(KF, "examples", "ecos", "mixed_pid_stateful__积分混合.c")
ECOS_FROM_C = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
DEFAULT_OUT = os.path.join(KF, "docs", "pr9b_pid_codegen")


def run(cmd, **kw):
    """跑命令并返回 (rc, out+err)。不抛异常：失败要能写进报告。"""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         **kw)
    out = p.communicate()[0]
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return p.returncode, out


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git_fingerprint():
    rc, head = run(["git", "-C", REPO, "rev-parse", "HEAD"])
    rc2, st = run(["git", "-C", REPO, "status", "--porcelain"])
    rc3, br = run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"])
    dirty = bool(st.strip())
    return {
        "commit": head.strip() if rc == 0 else None,
        "branch": br.strip() if rc3 == 0 else None,
        "dirty": dirty,
        "dirty_files": len([l for l in st.splitlines() if l.strip()]),
    }


def tool_version(cmd):
    exe = shutil.which(cmd)
    if not exe:
        return None
    rc, out = run([exe, "--version"])
    first = (out.splitlines() or [""])[0].strip()
    return first or None


def fingerprint(cycles):
    """把「这次结果由什么产生」钉死：提交、机器、编译器、源码摘要。"""
    return {
        "git": git_fingerprint(),
        "source": {
            "path": os.path.relpath(SRC, REPO),
            "sha256": sha256_file(SRC) if os.path.isfile(SRC) else None,
        },
        "host": {
            "uname": platform.platform(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "toolchain": {
            "clang": tool_version("clang-16") or tool_version("clang"),
            "opt": tool_version("opt-16") or tool_version("opt"),
            "gcc": tool_version("gcc"),
        },
        "params": {"cycles": cycles},
    }


_NUM = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(-?\d+)")


def parse_log(text):
    """只认 `# key: value` 行，避免被探针的表格干扰。"""
    d = {"digest": None, "g_out": None, "g_in": None, "session": None,
         "session_name": None, "readback": None, "miss": None, "seq_bad": None}
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            continue
        if s.startswith("# digest:"):
            d["digest"] = s.split(":", 1)[1].strip()
        elif s.startswith("# last_seq:"):
            for k, v in _NUM.findall(s):
                if k in ("g_out", "g_in"):
                    d[k] = int(v)
        elif s.startswith("# session:"):
            body = s.split(":", 1)[1].strip()
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(-?\d+)\s+readback=(\d+)", body)
            if m:
                d["session_name"] = m.group(1)
                d["session"] = int(m.group(2))
                d["readback"] = int(m.group(3))
        elif s.startswith("# deadline_miss:"):
            body = s.split(":", 1)[1].strip()
            d["miss"] = int(body.split("/")[0])
        elif s.startswith("# uku_seq_mismatch:"):
            body = s.split(":", 1)[1].strip()
            d["seq_bad"] = int(body.split("/")[0])
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=40)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-board", action="store_true",
                    help="只做生成与编译，不 insmod（无内核头/无板时用）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(SRC):
        print("找不到源：%s" % SRC, file=sys.stderr)
        return 2
    if not os.path.isfile(ECOS_FROM_C):
        print("找不到流水线：%s" % ECOS_FROM_C, file=sys.stderr)
        return 2

    fp = fingerprint(a.cycles)
    gen = os.path.join(a.out, "gen")
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(os.path.join(a.out, "logs"), exist_ok=True)

    env = dict(os.environ)
    env.update({
        "OUT_DIR": gen,
        "CYCLES": str(a.cycles),
    })

    # ---- 1) 生成 + 编译（一条命令，复用既有流水线；不复制第二份计算体）----
    if not a.quiet:
        print("[1/4] 生成 → 编译（OUT_DIR=%s）" % os.path.relpath(gen, REPO),
              file=sys.stderr)
    rc, out = run(["bash", ECOS_FROM_C, SRC], env=env)
    with io.open(os.path.join(a.out, "logs", "pipeline.log"), "w",
                 encoding="utf-8") as f:
        f.write(out)
    if rc != 0:
        print("流水线失败（rc=%d），见 logs/pipeline.log" % rc, file=sys.stderr)
        return 3

    plan_p = os.path.join(gen, "plan.json")
    driver_p = os.path.join(gen, "driver")
    ko_dir = os.path.join(gen, "kmod_uku")
    ko = None
    if os.path.isdir(ko_dir):
        for fn in sorted(os.listdir(ko_dir)):
            if fn.endswith(".ko"):
                ko = os.path.join(ko_dir, fn)
                break
    if not os.path.isfile(plan_p):
        print("流水线没有产出 plan.json", file=sys.stderr)
        return 3

    plan = json.load(io.open(plan_p, encoding="utf-8"))
    sess_meta = (plan.get("meta") or {}).get("uku_session") or {}
    report = {
        "kind": "pr9b_pid_codegen",
        "claim": ("一键复现：同一份 C → STKP → UKU → 板级逐拍；"
                  "会话状态由专用 ioctl 读回比对。不是 WCET，不是形式化等价。"),
        "source": os.path.relpath(SRC, REPO),
        "chosen": None,
        "domain": None,
        "adapter": (plan.get("meta") or {}).get("xdom_adapter"),
        "uku_session": sess_meta or None,
        "fingerprint": fp,
        "artifacts": {
            "plan": os.path.relpath(plan_p, REPO),
            "driver": os.path.relpath(driver_p, REPO) if os.path.isfile(driver_p) else None,
            "kmod": os.path.relpath(ko, REPO) if ko else None,
        },
    }

    rc2, ch = run([sys.executable, "-c",
                   "import json,sys;d=json.load(open(sys.argv[1]));"
                   "print(d.get('chosen') or '');"
                   "print(((d.get('chosen_record') or {}).get('domain')) or '')",
                   os.path.join(gen, "stkp.json")])
    if rc2 == 0:
        lines = [x for x in ch.splitlines() if x.strip()]
        if lines:
            report["chosen"] = lines[0]
        if len(lines) > 1:
            report["domain"] = lines[1]

    if a.no_board:
        report["board"] = {"skipped": True, "why": "--no-board"}
        report["verdict"] = "generated_only"
        _write_report(a.out, report)
        print("生成完成（未上板）。报告：%s" % os.path.join(a.out, "report.json"))
        return 0

    if not ko or not os.path.isfile(driver_p):
        report["board"] = {"error": "缺 .ko 或 driver（内核头缺失？）"}
        report["verdict"] = "blocked"
        _write_report(a.out, report)
        print("缺内核模块或驱动，无法上板（见 report.json）", file=sys.stderr)
        return 3

    # ---- 2) gold（全用户态）----
    if not a.quiet:
        print("[2/4] gold：全用户态 %d 拍" % a.cycles, file=sys.stderr)
    rc_g, gold_out = run([driver_p, "gold", str(a.cycles)])
    with io.open(os.path.join(a.out, "logs", "gold.log"), "w",
                 encoding="utf-8") as f:
        f.write(gold_out)
    gold = parse_log(gold_out)

    # ---- 3) mixed（中间组进内核）+ 状态读回 ----
    if not a.quiet:
        print("[3/4] mixed：insmod + %d 拍" % a.cycles, file=sys.stderr)
    rc_i, ins = run(["insmod", ko])
    if rc_i != 0:
        report["board"] = {"error": "insmod 失败", "detail": ins.strip()[:400]}
        report["verdict"] = "blocked"
        _write_report(a.out, report)
        print("insmod 失败：%s" % ins.strip()[:300], file=sys.stderr)
        return 4
    try:
        rc_m, mixed_out = run([driver_p, "mixed", str(a.cycles)])
        with io.open(os.path.join(a.out, "logs", "mixed.log"), "w",
                     encoding="utf-8") as f:
            f.write(mixed_out)
    finally:
        run(["rmmod", os.path.basename(ko)[:-3]])
    mixed = parse_log(mixed_out)

    # ---- 4) 判定（fail-closed：任一条不成立就是失败，不许"看起来对"）----
    checks = []

    def chk(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    chk("gold 跑通", rc_g == 0, "rc=%d" % rc_g)
    chk("mixed 跑通", rc_m == 0, "rc=%d" % rc_m)
    chk("digest 一致", gold["digest"] is not None and
        gold["digest"] == mixed["digest"],
        "gold=%s mixed=%s" % (gold["digest"], mixed["digest"]))
    chk("末拍 g_out 一致", gold["g_out"] is not None and
        gold["g_out"] == mixed["g_out"],
        "gold=%s mixed=%s" % (gold["g_out"], mixed["g_out"]))
    # PR-10A 核心：状态延续性。readback=0 说明根本没读到内核那份 → 不算通过。
    chk("会话状态可读回", mixed["readback"] == 1,
        "readback=%s（0 = 未提供/未读到）" % mixed["readback"])
    chk("会话状态与 gold 一致", gold["session"] is not None and
        gold["session"] == mixed["session"],
        "%s gold=%s mixed=%s" % (mixed["session_name"] or sess_meta.get("region"),
                                 gold["session"], mixed["session"]))
    chk("无期限超期", (mixed["miss"] or 0) == 0, "miss=%s" % mixed["miss"])
    chk("无序号错配", (mixed["seq_bad"] or 0) == 0, "seq_bad=%s" % mixed["seq_bad"])

    report["board"] = {
        "cycles": a.cycles,
        "digest": mixed["digest"],
        "digest_match": gold["digest"] == mixed["digest"],
        "last_g_out": mixed["g_out"],
        "session": {
            "name": mixed["session_name"] or sess_meta.get("region"),
            "gold": gold["session"],
            "mixed": mixed["session"],
            "readback": mixed["readback"],
        },
        "deadline_miss": mixed["miss"],
        "seq_mismatch": mixed["seq_bad"],
        "checks": checks,
    }
    report["verdict"] = "pass" if all(c["ok"] for c in checks) else "fail"
    _write_report(a.out, report)

    if not a.quiet:
        print("[4/4] 报告：%s" % os.path.join(a.out, "report.json"))
        for c in checks:
            print("  %s %s（%s）" % ("✅" if c["ok"] else "❌",
                                     c["check"], c["detail"]))
    if report["verdict"] != "pass":
        return 4
    return 0


def _write_report(out_dir, report):
    p = os.path.join(out_dir, "report.json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    sys.exit(main())
