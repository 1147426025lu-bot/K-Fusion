# -*- coding: utf-8 -*-
"""ECoS 合成入口：方案 → 双后端源码。

    python3 kfusion/backend/synthesis/emit.py \
        --plan build/ecos/plan.json --dag dag.json \
        --source examples/ecos/five_stage_loop__闭环五阶段示例.c \
        --out-dir build/ecos/linux --target linux-kernel

三条硬门（顺序固定，任一失败即停）：

  1. 方案必须带验证凭据（`plan.verification.ok=true`，由 PlanVerifier 出具）；
  2. 时间前提必须够（周期已知、相位已知、组相位在周期内）；
  3. ring-0 目标还要过"不能睡"检查（sleep / blocking_io / external / unknown）。

**不做的事**：不在这里调用编译器。本机没有内核头/交叉工具链，构建在目标侧完成
（生成的 README.build.txt 写了步骤）。"生成了源码"与"构建出了 .ko"是两件事，
不混为一谈。
"""

import argparse
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import body_emit          # noqa: E402
import fusion_ir          # noqa: E402
import linux_kernel_emitter as linux_em  # noqa: E402
import nuttx_emitter as nuttx_em          # noqa: E402
import stage_manifest                      # noqa: E402


def _write(out_dir, name, text):
    p = os.path.join(out_dir, name)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p


def main():
    ap = argparse.ArgumentParser(description="ECoS 双后端代码生成（PR9-C）")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--dag", required=True)
    ap.add_argument("--source", required=True, help="阶段实现所在的 .c")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--target", default="linux-kernel",
                    choices=["linux-kernel", "nuttx", "both"])
    ap.add_argument("--name", default=None, help="模块/应用名（默认取 DAG 任务名）")
    ap.add_argument("--contract", default=None)
    ap.add_argument("--exec-us", default=None, help="JSON 或文件：阶段执行时间")
    ap.add_argument("--call", action="append", default=[],
                    help="阶段名=调用表达式，如 stage_pid=stage_pid(1000)")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="允许用未验证方案生成（产物标记为对照用，不得进论文）")
    a = ap.parse_args()

    plan = fusion_ir.load(a.plan)
    dag = fusion_ir.load(a.dag)
    contract = fusion_ir.load(a.contract) if a.contract else None
    # 成本模型：以方案里的 cost_model 为准；命令行只允许"核对一致"
    cm = plan.get("cost_model") or {}
    exec_us = None
    if cm.get("exec_us") is not None:
        exec_us = {k: float(v) for k, v in cm["exec_us"].items()}
    if a.exec_us:
        cli = (json.loads(a.exec_us) if a.exec_us.lstrip().startswith("{")
               else fusion_ir.load(a.exec_us))
        cli = {k: float(v) for k, v in cli.items()}
        if exec_us is not None:
            norm = lambda d: sorted((k, round(v, 6)) for k, v in d.items())
            if norm(cli) != norm(exec_us):
                raise SystemExit("--exec-us 与 plan.cost_model 不一致：\n"
                                 "  方案   %s\n  命令行 %s\n"
                                 "  规划/复检/生成必须用同一份成本"
                                 % (exec_us, cli))
        exec_us = cli
    calls = {}
    for spec in a.call:
        if "=" not in spec:
            raise SystemExit("--call 格式应为 名字=表达式：%r" % spec)
        k, v = spec.split("=", 1)
        calls[k.strip()] = v.strip()

    # ---- 门 1：方案验证凭据 ----
    if not (plan.get("verification") or {}).get("ok"):
        if not a.allow_unverified:
            print("❌ 方案没有 PlanVerifier 的验证凭据，拒绝生成（PR9-A 门禁）\n"
                  "   → 先用 fusion_plan 规划（它会自动复检），或 --allow-unverified "
                  "生成仅供对照的产物", file=sys.stderr)
            return 3
        print("⚠ 未验证方案：产物标记为对照用，不得用于论文主张", file=sys.stderr)

    name = a.name or ((dag.get("meta") or {}).get("task") or {}).get("name") or "ecos_fused"
    name = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in name)

    ir = fusion_ir.FusionIR.from_plan(plan, dag, contract=contract, exec_us=exec_us,
                                      calls=calls, source_name=name)

    # ---- 门 2：时间前提 ----
    tv = ir.timing_violations()
    if tv:
        for who, why in tv:
            print("❌ %s%s" % (("%s: " % who) if who else "", why), file=sys.stderr)
        return 3

    # ---- 门 3：ring-0 禁止项 ----
    if a.target in ("linux-kernel", "both"):
        rv = ir.ring0_violations()
        if rv:
            for who, why in rv:
                print("❌ ring-0 禁止：%s —— %s" % (who, why), file=sys.stderr)
            print("   → 这些阶段只能跑在 NuttX/用户态；不要靠「改小睡眠」绕过",
                  file=sys.stderr)
            return 3

    # ---- 阶段函数必须真实存在 ----
    try:
        protos = stage_manifest.prototypes_for(ir, a.source)
    except stage_manifest.StageMissing as e:
        print("❌ %s" % e, file=sys.stderr)
        return 3

    os.makedirs(a.out_dir, exist_ok=True)
    written = []
    written.append(_write(a.out_dir, "ecos_fused_body.h", body_emit.emit_body_h(ir)))
    written.append(_write(a.out_dir, "ecos_fused_body.c",
                          body_emit.emit_body_c(ir, protos)))
    written.append(_write(a.out_dir, "fusion_ir.json",
                          json.dumps(ir.to_json(), ensure_ascii=False, indent=2) + "\n"))

    if a.target in ("linux-kernel", "both"):
        d = a.out_dir if a.target == "linux-kernel" else os.path.join(a.out_dir, "linux")
        os.makedirs(d, exist_ok=True)
        for fn, text in linux_em.emit(ir, name).items():
            written.append(_write(d, fn, text))

    if a.target in ("nuttx", "both"):
        d = a.out_dir if a.target == "nuttx" else os.path.join(a.out_dir, "nuttx")
        os.makedirs(d, exist_ok=True)
        for fn, text in nuttx_em.emit(ir, name).items():
            written.append(_write(d, fn, text))

    print("=== ECoS 合成：%s（target=%s）===" % (name, a.target))
    print("  组 %d / 阶段 %d，周期 %.0f µs，证据 %s，验证 %s"
          % (len(ir.groups), len(ir.order), ir.period_us or 0, ir.evidence,
             "通过" if ir.verified else "未验证"))
    for g in ir.groups:
        print("  %-4s @%-6s µs  %s"
              % (g.gid, ("%.0f" % g.phase_us) if g.phase_us is not None else "?",
                 " + ".join(s.name for s in g.stages)))
    print("  写出 %d 个文件：" % len(written))
    for p in written:
        print("    %s" % os.path.relpath(p, a.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
