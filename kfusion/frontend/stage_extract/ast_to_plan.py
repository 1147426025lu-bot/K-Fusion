# -*- coding: utf-8 -*-
"""ast_to_plan — 把 stage_extract 的 JSON 变成可验证的 plan/dag/contract。

Gate 1 的「自动生成」环节：用户不再手写 DAG，而是由编译器从既有 C 程序的提取结果
自动构造执行图与计划。

输入：stage_extract 输出的 JSON（entry/stages[]/globals[]）。
输出：
  · dag.json      —— 阶段节点 + 相位 + 执行序（供规划器与 emit 使用）
  · contract.json —— 最小时间契约（task.period_us/deadline_us）
  · plan.json     —— **由现有规划器 fusion_plan__融合规划.py 生成并签署**，
                     其 verification.ok 来自独立验证器 plan_verify（不是本脚本伪造）

诚实边界（v1）：
  · 执行时间若未给 --exec-us，按 --default-exec-us 占位（默认 10 µs），并标记为
    "empirical placeholder"，不得用于论文主张——需实测后替换。
  · 数据依赖边未恢复（stage_extract 目前只给调用序，不给全局 use-def）；本脚本按
    **调用序**建 topo_order_delayed，edges 留空，并在 dag.meta 里注明。真正的数据版本
    依赖由 Gate 2（TEIR，IR use-def）补。
  · 效果按 state_read/state_write 保守假设（保守=多依赖，不会漏依赖）。

用法：
  python3 ast_to_plan.py <extract.json> --period-us 1000 \
      --out-dir build/gate1 [--exec-us '{"stage_pid":0.6}'] [--default-exec-us 10] \
      [--domain linux_kernel] [--task-name demo] [--deadline-us 1000]
"""

import argparse
import io
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # .../kfusion/frontend/stage_extract -> repo
_PLANNER = os.path.join(_REPO, "kfusion", "scripts", "fusion_plan__融合规划.py")
_CAPS = os.path.join(_REPO, "kfusion", "contracts", "examples", "platform_caps.json")


def _load(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(p, obj):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_dag(extract, period_us, deadline_us, exec_us, default_exec_us,
              task_name=None):
    """从提取结果构造 dag dict（调用序 -> 相位/拓扑序）。"""
    if not extract.get("ok") or not extract.get("entry"):
        raise SystemExit("ast_to_plan: 提取结果没有周期入口（ok/entry 不成立），拒绝构造")
    stages = extract.get("stages") or []
    if not stages:
        raise SystemExit("ast_to_plan: 入口 %r 没有可识别的阶段函数（stages 为空）"
                         % extract.get("entry"))

    order = [s["name"] for s in stages]
    # 执行时间：显式优先，缺省用占位并标记
    ex = dict(exec_us or {})
    placeholders = []
    for n in order:
        if n not in ex:
            ex[n] = float(default_exec_us)
            placeholders.append(n)

    # 相位：线性链，第 i 阶段在 0..i-1 全部完成后释放
    offsets = {}
    acc = 0.0
    for n in order:
        offsets[n] = round(acc, 3)
        acc += ex[n]
    span = round(acc, 3)

    nodes = []
    for i, n in enumerate(order):
        nodes.append({
            "name": n,
            "fns": [n],
            "context": "thread",
            "declared_deps": ([order[i - 1]] if i > 0 else []),
            # 保守：控制阶段按读写本域状态处理（Gate 2 的 IR use-def 再精化）
            "effects": ["state_read", "state_write"],
            "contexts": ["thread", "softirq"],
            "phase_us": offsets[n],
        })

    dag = {
        "version": 2,
        "meta": {
            "mode": "stages",
            "task": {
                "name": task_name or extract.get("entry"),
                "entry": extract.get("entry"),
                "period_us": period_us,
                "deadline_us": deadline_us,
            },
            "note": ("由 ast_to_plan 从 stage_extract 输出自动构造；"
                     "edges 未恢复数据依赖（v1 仅按调用序），PhaseSpan=%.1f µs" % span),
            "auto": {
                "source": extract.get("source"),
                "globals": [g.get("name") for g in (extract.get("globals") or [])],
                "placeholder_exec": placeholders,
            },
        },
        "nodes": nodes,
        "edges": [],
        "candidates": [],
        "phase": {
            "known": True,
            "unit": "us",
            "period_us": period_us,
            "offsets": offsets,
        },
        "topo_order": [],
        "topo_order_delayed": order,
        "recurrences": [],
        "algebraic_loops": [],
        "cycles": [],
    }
    return dag, ex, placeholders


def main():
    ap = argparse.ArgumentParser(description="stage_extract JSON -> dag/contract/plan（经真实验证器）")
    ap.add_argument("extract")
    ap.add_argument("--period-us", type=float, required=True)
    ap.add_argument("--deadline-us", type=float, default=None)
    ap.add_argument("--domain", default="linux_kernel", help="平台名（platform_caps 里的 name）")
    ap.add_argument("--exec-us", default=None, help="JSON 或 @file：{阶段名: µs}")
    ap.add_argument("--default-exec-us", type=float, default=10.0)
    ap.add_argument("--task-name", default=None)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--planner", default=_PLANNER)
    ap.add_argument("--platforms", default=_CAPS)
    ap.add_argument("--release-cost-us", type=float, default=20.0)
    a = ap.parse_args()

    if a.period_us is None or a.period_us <= 0:
        raise SystemExit("ast_to_plan: --period-us 必须 > 0")
    deadline = a.deadline_us if a.deadline_us is not None else a.period_us

    exec_us = None
    if a.exec_us:
        if a.exec_us.startswith("@"):
            exec_us = _load(a.exec_us[1:])
        elif a.exec_us.lstrip().startswith("{"):
            exec_us = json.loads(a.exec_us)
        else:
            exec_us = _load(a.exec_us)

    extract = _load(a.extract)
    dag, ex, placeholders = build_dag(
        extract, a.period_us, deadline, exec_us, a.default_exec_us, a.task_name)

    os.makedirs(a.out_dir, exist_ok=True)
    dag_p = os.path.join(a.out_dir, "dag.json")
    contract = {"task": {"period_us": a.period_us, "deadline_us": deadline}}
    contract_p = os.path.join(a.out_dir, "contract.json")
    plan_p = os.path.join(a.out_dir, "plan.json")
    _write(dag_p, dag)
    _write(contract_p, contract)

    if placeholders:
        print("⚠ 这些阶段没有显式执行时间，用占位 %.1f µs（需实测替换）：%s"
              % (a.default_exec_us, ", ".join(placeholders)), file=sys.stderr)

    if not os.path.isfile(a.planner):
        raise SystemExit("ast_to_plan: 找不到规划器 %s" % a.planner)
    if not os.path.isfile(a.platforms):
        raise SystemExit("ast_to_plan: 找不到平台能力表 %s" % a.platforms)

    cmd = [sys.executable, a.planner, dag_p,
           "--platforms", a.platforms, "--platform", a.domain,
           "--exec-us", json.dumps(ex, ensure_ascii=False),
           "--release-cost-us", str(a.release_cost_us),
           "--contract", contract_p,
           "-o", plan_p, "--quiet"]
    print("== 调用规划器 ==", file=sys.stderr)
    rc = subprocess.call(cmd)
    if rc != 0:
        print("ast_to_plan: 规划器返回 %d（多半是该部署不可行/被验证器拒绝，见上）" % rc,
              file=sys.stderr)
        return rc
    if not os.path.isfile(plan_p):
        raise SystemExit("ast_to_plan: 规划器没有产出 plan.json")

    plan = _load(plan_p)
    ok = bool((plan.get("verification") or {}).get("ok"))
    print("dag=%s\ncontract=%s\nplan=%s\nverification.ok=%s groups=%d"
          % (dag_p, contract_p, plan_p, ok, len(plan.get("groups") or [])),
          file=sys.stderr)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
