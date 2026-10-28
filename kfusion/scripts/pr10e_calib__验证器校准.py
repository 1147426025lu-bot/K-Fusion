#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-10E 附：验证器校准——「预测可行」与「实测不超期」的混淆矩阵

评审要求（原话）：400 µs 被拒绝属于很好的功能测试，不过还需要验证模型误差、
误拒绝率，以及是否有预测通过但实际超期的情况。

本脚本能回答的：
  · 假阳（最危险）：验证器判 pass，实测却超期  → 算得出
  · 真阴：验证器判 pass，实测确实不超期        → 算得出
  · 真阳：验证器判 reject，同周期下该部署本就该超期 → 只能间接论证
  · **假阴（误拒率）：验证器判 reject，但实测本来能行 → 当前架构下测不出**

为什么误拒率测不出：被 reject 的方案不会生成产物（fail-closed 的代价），
没有 driver 就没有实测。要测必须给流水线加「强制生成」开关，
那会破坏 fail-closed 语义，故本脚本不擅自加，只如实标注该项未测。

用法：
  python3 pr10e_calib__验证器校准.py --periods 400 500 600 800 1000 --cycles 5000
"""
from __future__ import print_function

import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MATRIX = os.path.join(HERE, "pr10b_matrix__矩阵.py")


def load_matrix():
    """按路径加载矩阵脚本，复用它的变体源/构建/测量逻辑（避免第二套实现）。"""
    spec = importlib.util.spec_from_file_location("pr10b_matrix", MATRIX)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def predict_from_log(text):
    """从流水线输出里抽验证器的判定。

    返回 {verdict: pass|reject, predicted_us: float|None, reason: str}
    —— 不猜字段名，只认代码里实际打印的 [DEADLINE]/[WINDOW] 等标记。
    """
    verdict = "pass"
    predicted = None
    reason = ""

    # 形如 "[DEADLINE] 426.3 > 400" 或 "426 > 400"
    m = re.search(r"\[DEADLINE\]\s*([0-9.]+)\s*>\s*([0-9.]+)", text)
    if m:
        verdict = "reject"
        predicted = float(m.group(1))
        reason = "DEADLINE %s > %s" % (m.group(1), m.group(2))

    for tag in ("E_WINDOW", "E_PERIOD_STRETCH", "E_PIN_EARLY_COMMIT", "E_DOMAIN",
                "E_UKU", "E_XDOM", "E_WINDOW_NO_COST", "E_WINDOW_GROUP_BOUND"):
        if tag in text and verdict == "pass":
            verdict = "reject"
            mm = re.search(r"%s[^\n]*" % tag, text)
            reason = (mm.group(0).strip() if mm else tag)[:160]

    # 兜底：明确的不合法标记
    if verdict == "pass" and re.search(r"not ok|verification\.ok=false|不可行", text):
        verdict = "reject"
        reason = reason or "verification 未通过"

    return {"verdict": verdict, "predicted_us": predicted, "reason": reason}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--periods", type=int, nargs="+",
                    default=[400, 500, 600, 800, 1000])
    ap.add_argument("--cycles", type=int, default=5000)
    ap.add_argument("--out", default=os.path.join(REPO, "kfusion", "docs",
                                                 "pr10e_calib"))
    ap.add_argument("--reuse", action="store_true",
                    help="复用已有 gen_*（不重建），用于补测")
    a = ap.parse_args()

    mx = load_matrix()
    os.makedirs(a.out, exist_ok=True)

    rows = []
    print("=" * 70)
    print(" 验证器校准：预测可行 × 实测超期")
    print("=" * 70, file=sys.stderr)

    for period in a.periods:
        src_dir = os.path.join(a.out, "src_p%d" % period)
        os.makedirs(src_dir, exist_ok=True)
        src, src_sha = mx.variant_source(src_dir, period)
        print("\n[周期 %d µs] 变体源 = %s (sha256=%s)"
              % (period, src, src_sha[:12]), file=sys.stderr)

        for scn_name, spec in mx.SCENARIOS.items():
            tag = "%s_p%d" % (scn_name, period)
            print("  [build] %s ..." % tag, end="", file=sys.stderr)
            b = mx.build_cell(scn_name, spec, src, period, a.out)

            # build_cell 只回 6 行 log_tail，验证器判定要从完整 pipeline.log 读
            logp = os.path.join(a.out, tag, "pipeline.log")
            text = ""
            if os.path.isfile(logp):
                text = io.open(logp, encoding="utf-8", errors="replace").read()
            if not text:
                text = "\n".join(b.get("log_tail") or [])
            pred = predict_from_log(text)
            print(" pipeline=%s / 预测=%s" % (b.get("status"), pred["verdict"]),
                  file=sys.stderr)

            row = {"period_us": period, "scenario": scn_name,
                   "src_sha256": src_sha,
                   "pipeline": b.get("status"),
                   "predicted": pred["verdict"],
                   "predicted_us": pred["predicted_us"],
                   "predicted_reason": pred["reason"],
                   "measured": None}

            if b.get("status") == "ok":
                d = os.path.join(a.out, "runs_%s" % tag)
                os.makedirs(d, exist_ok=True)
                res = mx.measure(b, a.cycles, 1, 0, d)
                r0 = (res.get("runs") or [{}])[0]
                row["measured"] = {
                    "n": r0.get("cycles"),
                    "miss": r0.get("miss"),
                    "resp_max_us": (r0.get("response") or {}).get("max"),
                    "resp_p99_us": (r0.get("response") or {}).get("p99"),
                    "resp_p50_us": (r0.get("response") or {}).get("p50"),
                }
            rows.append(row)

    # ---------- 混淆矩阵 ----------
    cells = {"真阴（放行且确实不超期）": [], "假阳（放行但实测超期）": [],
             "真阳（拒绝，同周期另一部署可过）": [], "拒绝且无对照": []}
    for r in rows:
        if r["measured"] and r["measured"].get("n"):
            if (r["measured"].get("miss") or 0) == 0:
                cells["真阴（放行且确实不超期）"].append(r)
            else:
                cells["假阳（放行但实测超期）"].append(r)
        elif r["predicted"] == "reject":
            # 同周期是否有另一部署被放行 —— 有则说明该周期本身可行，拒绝更可能是正确的
            sib = [x for x in rows
                   if x["period_us"] == r["period_us"] and x is not r
                   and x["measured"] and x.get("measured", {}).get("n")]
            if sib:
                cells["真阳（拒绝，同周期另一部署可过）"].append(r)
            else:
                cells["拒绝且无对照"].append(r)

    out = {"kind": "pr10e_calib", "cycles": a.cycles, "rows": rows,
           "confusion": {k: [{"period_us": x["period_us"], "scenario": x["scenario"],
                              "predicted_us": x["predicted_us"],
                              "reason": x["predicted_reason"],
                              "measured": x["measured"]} for x in v]
                         for k, v in cells.items()},
           "not_measured": {
               "误拒率（假阴）": ("架构限制：被 reject 的方案不生成产物，无 driver 可跑。"
                              "要测需给流水线加 --force 强制生成，会破坏 fail-closed 语义，"
                              "故不在本脚本内擅自开启。")}}
    with io.open(os.path.join(a.out, "calib.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))

    print("\n" + "=" * 70)
    for k, v in cells.items():
        print("%-32s %d 项" % (k, len(v)))
        for x in v:
            m = x.get("measured") or {}
            print("    T=%-5d %-16s 预测=%-6s 预测值=%-8s 实测 miss=%s max=%s"
                  % (x["period_us"], x["scenario"], x["predicted"],
                     x["predicted_us"], m.get("miss"), m.get("resp_max_us")))
    print("=" * 70)
    print("误拒率：未测（见 not_measured）")
    print("结果：%s" % os.path.join(a.out, "calib.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
