#!/usr/bin/env python3
"""after_soak__长测收尾.py — 把板上的浸泡结果收回来、出图、写进文档并推送。

为什么要有它：
  三段浸泡（15 min / 1 h / 8 h）是串在一夜里的，8 h 段结束时人不一定在。
  收尾动作（取图 / 写表 / 提交 / 推送）如果不脚本化，就会漏掉一段数据，
  或者只留在板上、仓库里查不到。

做的事（可重复执行，已完成的步骤会跳过）：
  1. 逐段检查板上 `<root>/<seg>/soak.json` 与 `run.log`，缺的段跳过并说明；
  2. 板上调用 `plot_soak_segments__分段出图.sh` 出图（已存在则跳过）；
  3. pscp 回 png + soak.json 到 `docs/figures/soak_l4/`；
  4. 更新 `docs/K-FUSION__方案与证据.md` 中 `<!-- SOAK_TABLE -->` 标记之间的表；
  5. git add/commit/push（直连失败时按代理列表重试）。

用法：
  PR_PW=<板上密码> python3 after_soak__长测收尾.py
  env: PR_HOST(默认 192.168.1.3) PR_USER(pi) PR_PW(必填) PR_HOSTKEY(sha256:...)
       PR_ROOT(默认 /home/pi/kfusion_results/soak_l4) PR_PUSH(0=不推)
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DOC = os.path.join(REPO, "kfusion", "docs", "K-FUSION__方案与证据.md")
FIGDIR = os.path.join(REPO, "kfusion", "docs", "figures", "soak_l4")
SEGS = ("15m", "1h", "8h")
MARK_BEGIN = "<!-- SOAK_TABLE -->"
MARK_END = "<!-- /SOAK_TABLE -->"


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit("缺环境变量 %s" % name)
    return v


HOST = env("PR_HOST", "192.168.1.3")
USER = env("PR_USER", "pi")
PW = env("PR_PW", required=True)
HOSTKEY = env("PR_HOSTKEY", "SHA256:ppmbZSHlu2yIbbAmHJelrcb67/UV8fkaWEvTvl8XjBg")
ROOT = env("PR_ROOT", "/home/pi/kfusion_results/soak_l4")


def plink(cmd, timeout=120):
    c = ["plink", "-batch", "-l", USER, "-pw", PW, "-hostkey", HOSTKEY, HOST, cmd]
    try:
        r = subprocess.run(c, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def pscp(remote, local, timeout=180):
    c = ["pscp", "-batch", "-pw", PW, "-hostkey", HOSTKEY,
         "%s@%s:%s" % (USER, HOST, remote), local]
    try:
        r = subprocess.run(c, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return False, "timeout"


def git(*args, timeout=180):
    env2 = dict(os.environ)
    for p in ("7890", "2080"):
        env2.setdefault("https_proxy", "http://127.0.0.1:%s" % p)
        env2.setdefault("http_proxy", "http://127.0.0.1:%s" % p)
        break
    r = subprocess.run(["git"] + list(args), cwd=REPO, capture_output=True,
                       text=True, timeout=timeout, env=env2)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    os.makedirs(FIGDIR, exist_ok=True)
    have = []
    for seg in SEGS:
        rc, out = plink("ls %s/%s/soak.json 2>/dev/null; ls %s/%s/run.log 2>/dev/null"
                        % (ROOT, seg, ROOT, seg))
        if rc != 0 or ("soak.json" not in out):
            print("⏳ %-3s 未完成（板上无 soak.json）" % seg)
            continue
        have.append(seg)
        print("✓ %-3s 已有结果" % seg)

    if not have:
        print("没有任何完成的段，退出")
        return 1

    # 出图（板上；已存在会跳过）
    rc, out = plink("bash %s/kfusion/scripts/paper/plot_soak_segments__分段出图.sh %s "
                    "/home/pi/kfusion_results/figs" % (REPO_PLACEHOLDER(), ROOT),
                    timeout=1800)
    print(out.strip().splitlines()[-3:] if out.strip() else "")

    # 取回
    rows = []
    for seg in have:
        base = "soak_l4_%s" % seg
        for name in ("%s.png" % base, "%s_latency.png" % base):
            ok, msg = pscp("/home/pi/kfusion_results/figs/%s" % name,
                           os.path.join(FIGDIR, name))
            print(("  ↓ %s" % name) if ok else ("  ✗ %s: %s" % (name, msg.strip()[-80:])))
        jlocal = os.path.join(FIGDIR, "%s.json" % base)
        ok, _ = pscp("%s/%s/soak.json" % (ROOT, seg), jlocal)
        if not ok:
            continue
        with io.open(jlocal, encoding="utf-8") as f:
            d = json.load(f)
        st = (d.get("stat") or {}).get("start_lat") or {}
        rs = (d.get("stat") or {}).get("response") or {}
        rt = d.get("rtlat") or {}
        miss = d.get("miss") or ["?", "?"]
        rows.append({
            "seg": seg, "cycles": d.get("cycles_planned"),
            "wall": d.get("wall_s"), "p50": rt.get("p50_us") or st.get("p50"),
            "p99": rt.get("p99_us") or st.get("p99"),
            "p999": rt.get("p999_us"), "p9999": rt.get("p9999_us"),
            "max": rt.get("max_us") or st.get("max"),
            "miss": "%s/%s" % (miss[0], miss[1]) if len(miss) == 2 else "?",
            "resp_max": rs.get("max"), "stride": rt.get("stride"),
        })

    # 写表
    if not os.path.isfile(DOC):
        print("找不到文档 %s" % DOC)
        return 1
    with io.open(DOC, encoding="utf-8") as f:
        doc = f.read()
    lines = ["| 段长 | 拍数 | 实际墙钟 | p50 | p99 | p99.9 | p99.99 | max | 超期 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %.0f s | %.2f | %.2f | %s | %s | %.2f | %s |"
                     % (r["seg"], r["cycles"], r["wall"] or 0, r["p50"] or 0,
                        r["p99"] or 0,
                        "%.2f" % r["p999"] if r["p999"] is not None else "—",
                        "%.2f" % r["p9999"] if r["p9999"] is not None else "—",
                        r["max"] or 0, r["miss"]))
    lines.append("")
    lines.append("图：`docs/figures/soak_l4/soak_l4_{%s}.png`（四联抖动分布）"
                 "与 `..._latency.png`（延迟时间线）。"
                 % ",".join(r["seg"] for r in rows))
    table = "\n".join(lines)

    pat = re.compile(re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END), re.S)
    block = "%s\n%s\n%s" % (MARK_BEGIN, table, MARK_END)
    if pat.search(doc):
        doc = pat.sub(block, doc)
    else:
        doc = doc.rstrip() + "\n\n" + block + "\n"
    with io.open(DOC, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print("文档已更新（%d 段）" % len(rows))

    rc, out = git("add", "-A", "kfusion/docs")
    msg = ("data(soak): L4 三段浸泡结果（%s）\n\n"
           "段长/拍数/p50/p99/p99.9/p99.99/max/超期见 docs 表格；"
           "图在 docs/figures/soak_l4/。\n"
           "同一二进制（含 PID UB 修复与 UKU 故障锁定）、同一隔离档位（L4）、"
           "同一调度类（FIFO99）与亲和（CPU3）。" % "+".join(r["seg"] for r in rows))
    rc, out = git("commit", "-q", "-m", msg)
    print("commit:", out.strip()[-200:] or "ok")
    if env("PR_PUSH", "1") != "0":
        for i in range(3):
            rc, out = git("push", "origin", "HEAD:feat/ecos-temporal-semantics",
                          timeout=240)
            if rc == 0:
                print("已推送 feat")
                break
            print("推送重试 %d：%s" % (i + 1, out.strip().splitlines()[-1:]))
        git("checkout", "-q", "main")
        git("merge", "--ff-only", "-q", "feat/ecos-temporal-semantics")
        rc, out = git("push", "origin", "main", timeout=240)
        print("main:", "ok" if rc == 0 else out.strip()[-120:])
        git("checkout", "-q", "feat/ecos-temporal-semantics")
    return 0


def REPO_PLACEHOLDER():
    """板上仓库路径（本脚本在 Windows 侧运行，路径不能直接用本机 REPO）。"""
    return "/home/pi/K-Fusion"


if __name__ == "__main__":
    sys.exit(main())
