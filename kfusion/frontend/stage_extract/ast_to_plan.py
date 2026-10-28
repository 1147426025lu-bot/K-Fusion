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
import importlib.util
import io
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # .../kfusion/frontend/stage_extract -> repo
_PLANNER = os.path.join(_REPO, "kfusion", "scripts", "fusion_plan__融合规划.py")
_CAPS = os.path.join(_REPO, "kfusion", "contracts", "examples", "platform_caps.json")
_TEIR_MOD = os.path.join(_REPO, "kfusion", "ir", "temporal_effect_ir.py")
_VER_MOD = os.path.join(_REPO, "kfusion", "ir", "version_deps.py")


def _load_teir_module():
    """按文件路径加载 TEIR 模块（kfusion/ir/temporal_effect_ir.py），避免依赖包结构。"""
    spec = importlib.util.spec_from_file_location("kfusion_ir_teir", _TEIR_MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_ver_module():
    """按文件路径加载 version_deps 模块（Gate 2 后期 + Gate 3 准备）。"""
    spec = importlib.util.spec_from_file_location("kfusion_ir_version_deps", _VER_MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
              task_name=None, default_domain="linux_kernel", domain_period_us=None):
    """从提取结果构造 dag dict（调用序 -> 相位/拓扑序 + 数据版本依赖）。

    default_domain / domain_period_us 支持**多域多速率**输入：
      · 每阶段可带 stage.domain（或 extract.meta.domains 批量声明）；缺省回退
        default_domain（即 --domain 平台名）。
      · 每域周期来自 extract.meta.domain_period_us 或 --domain-periods；
        某阶段还可直接带 stage.period_us 覆盖本阶段周期。
      · 数据边据此标注 version（same_period / prev_period）+ lag_periods + xdom，
        公式由 version_deps.compute_lag 提供（与独立复核共用同一来源）。
    """
    if not extract.get("ok") or not extract.get("entry"):
        raise SystemExit("ast_to_plan: 提取结果没有周期入口（ok/entry 不成立），拒绝构造")
    # K-C0 fail-closed：提取阶段已标出不可内核化的外部调用，这里直接拒绝往下走
    kc = extract.get("k_c0") or {}
    if kc and not kc.get("ok", True):
        rej = kc.get("rejections") or []
        lines = ["ast_to_plan: K-C0 拒绝——以下构造不支持内核化（fail-closed）："]
        for r in rej:
            lines.append("  · %s() 调用 %s()：%s"
                         % (r.get("function", "?"), r.get("call", "?"),
                            r.get("reason", "")))
        lines.append("  → 先改写成受支持子集（固定布局状态/无动态内存/无阻塞/I/O 走声明适配器），"
                     "或改到用户态域。不要把不支持的东西硬塞进内核模块。")
        raise SystemExit("\n".join(lines))
    stages = extract.get("stages") or []
    if not stages:
        raise SystemExit("ast_to_plan: 入口 %r 没有可识别的阶段函数（stages 为空）"
                         % extract.get("entry"))

    # 域 / 多速率：提取器声明的域映射与域周期，CLI 的 --domain-periods 覆盖
    emeta = extract.get("meta") or {}
    domains_map = dict(emeta.get("domains") or {})
    periods_map = dict(emeta.get("domain_period_us") or {})
    if domain_period_us:
        periods_map.update(domain_period_us)

    order = [s["name"] for s in stages]
    # IR use-def（Gate 2）：每个阶段的读/写全局，来自 stage_extract 的 IR 级分析
    stage_rw = {}
    stage_dom = {}
    stage_period = {}
    for s in stages:
        name = s["name"]
        stage_rw[name] = {
            "reads": s.get("reads") or [],
            "writes": s.get("writes") or [],
        }
        dom = s.get("domain") or domains_map.get(name) or default_domain
        stage_dom[name] = dom
        # 阶段周期：stage.period_us > 域周期 > 任务周期
        p = s.get("period_us") or periods_map.get(dom) or period_us
        stage_period[name] = float(p)

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

    # 数据版本依赖边（Gate 2 IR use-def + Gate 2 后期跨域/多速率）：
    # 对 B 的每个读 g，找调用序中 B 之前最后一个写 g 的阶段 A -> A 在周期 k 写
    # w_g(k)，B 读 r_g(k)（同一周期版本）或 r_g(k-lag)（上一/更早周期，跨域/多速率）。
    # 这是「功能等价 ≠ 时间正确」要钉死的那条约束——版本必须和 (域/周期/相位) 一致，
    # 跨域/多速率边不能默认同拍可见（PlanVerifier 诚实边界）。
    ver_mod = _load_ver_module()
    data_edges = []
    cross_inputs = []
    seen = set()
    for idx_b, b in enumerate(order):
        for g in stage_rw[b]["reads"]:
            producer = None
            for idx_a in range(idx_b - 1, -1, -1):
                a = order[idx_a]
                if g in stage_rw[a]["writes"]:
                    producer = a
                    break
            if producer is not None:
                key = (producer, b, g)
                if key not in seen:
                    Pa = stage_period[producer]
                    Pb = stage_period[b]
                    xdom = stage_dom[producer] != stage_dom[b]
                    la = offsets[producer]
                    lb = offsets[b]
                    # 自动提取的程序无法证明跨域同拍可达 ⇒ proof=False ⇒
                    # 跨域边一律降级为 prev_period（fail-closed，杜绝 over-claim）。
                    req, _kind = ver_mod.compute_lag(Pa, Pb, xdom, la, lb, False)
                    if req == 0:
                        version = "same_period"
                        lag = 0
                    else:
                        version = "prev_period"
                        lag = req
                    data_edges.append({"from": producer, "to": b,
                                       "kind": "data", "proven": g,
                                       "var": g, "version": version,
                                       "lag_periods": lag, "xdom": xdom})
                    seen.add(key)
            else:
                # 读但本周期无人写：外部输入 / 上一周期状态（跨周期，不算内部边，
                # 本就是 prev_period 读取）
                cross_inputs.append({"stage": b, "var": g})

    # 每个节点的真实数据入边（用于 declared_deps / 下游验证）
    in_edges = {}
    for e in data_edges:
        in_edges.setdefault(e["to"], []).append(e["from"])

    nodes = []
    for i, n in enumerate(order):
        nodes.append({
            "name": n,
            "fns": [n],
            "context": "thread",
            "domain": stage_dom[n],
            "period_us": stage_period[n],
            "declared_deps": in_edges.get(n, []),
            # 保守：控制阶段按读写本域状态处理（Gate 2 的 IR use-def 已给出真实读写）
            "effects": ["state_read", "state_write"],
            "contexts": ["thread", "softirq"],
            "phase_us": offsets[n],
        })

    all_periods = dict(periods_map)
    all_periods.setdefault(default_domain, period_us)
    domains_used = sorted(set(stage_dom.values()))
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
            "domains": domains_used,
            "domain_period_us": all_periods,
            "note": ("由 ast_to_plan 从 stage_extract 输出自动构造；"
                     "edges 来自 IR use-def 数据版本依赖（%d 条，其中跨域 %d 条），"
                     "另有 %d 个跨周期输入；PhaseSpan=%.1f µs；域=%s"
                     % (len(data_edges),
                        sum(1 for e in data_edges if e.get("xdom")),
                        len(cross_inputs), span, ",".join(domains_used))),
            "auto": {
                "source": extract.get("source"),
                "globals": [g.get("name") for g in (extract.get("globals") or [])],
                "placeholder_exec": placeholders,
                "data_edges": len(data_edges),
                "cross_period_inputs": cross_inputs,
            },
        },
        "nodes": nodes,
        "edges": data_edges,
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
    return dag, ex, placeholders, data_edges, cross_inputs, stage_rw


def main():
    ap = argparse.ArgumentParser(description="stage_extract JSON -> dag/contract/plan（经真实验证器）")
    ap.add_argument("extract")
    ap.add_argument("--period-us", type=float, required=True)
    ap.add_argument("--deadline-us", type=float, default=None)
    ap.add_argument("--domain", default="linux_kernel", help="平台名（platform_caps 里的 name）")
    ap.add_argument("--default-domain", default="linux_kernel",
                    help="阶段未声明 domain 时回退的默认域（多域时通常是平台名）")
    ap.add_argument("--domain-periods", default=None,
                    help="JSON 或 @file：{域名: 周期µs}，支持多速率/跨域周期")
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

    domain_period_us = None
    if a.domain_periods:
        if a.domain_periods.startswith("@"):
            domain_period_us = _load(a.domain_periods[1:])
        elif a.domain_periods.lstrip().startswith("{"):
            domain_period_us = json.loads(a.domain_periods)
        else:
            domain_period_us = _load(a.domain_periods)

    extract = _load(a.extract)
    (dag, ex, placeholders, data_edges, cross_inputs, stage_rw) = build_dag(
        extract, a.period_us, deadline, exec_us, a.default_exec_us,
        a.task_name, a.default_domain, domain_period_us)

    os.makedirs(a.out_dir, exist_ok=True)
    dag_p = os.path.join(a.out_dir, "dag.json")
    contract = {"task": {"period_us": a.period_us, "deadline_us": deadline}}
    contract_p = os.path.join(a.out_dir, "contract.json")
    plan_p = os.path.join(a.out_dir, "plan.json")
    _write(dag_p, dag)
    _write(contract_p, contract)

    # Gate 2：从同一份提取结果产出 TEIR（时序效应中间表示）
    order = [s["name"] for s in (extract.get("stages") or [])]
    kc_ok = bool((extract.get("k_c0") or {}).get("ok", True))
    teir_mod = _load_teir_module()
    teir = teir_mod.build_teir(extract, dag, ex, order, stage_rw,
                              data_edges, cross_inputs, kc_ok)
    teir_p = os.path.join(a.out_dir, "teir.json")
    teir_mod.write_teir(teir, teir_p)
    print("teir=%s (数据边 %d，跨周期输入 %d)"
          % (teir_p, len(data_edges), len(cross_inputs)), file=sys.stderr)

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

    # Gate 3 准备：与规划器**分离**的版本依赖复核（version_deps）。
    # 规划器只看融合/时序可行性；版本依赖（跨域/多速率边是否 over-claim）由独立模块
    # 从 (域/周期/相位) 重新推导必要延迟并比对声明——over-claim 即 fail-closed。
    ver_mod = _load_ver_module()
    vrep = ver_mod.verify(dag)
    if not vrep["ok"]:
        ok = False
        print("ast_to_plan: 版本依赖复核失败（fail-closed）：", file=sys.stderr)
        for e in vrep["errors"]:
            print("  · " + e, file=sys.stderr)
    for w in vrep.get("warnings") or []:
        print("  ⚠ " + w, file=sys.stderr)

    print("dag=%s\ncontract=%s\nplan=%s\nverification.ok=%s groups=%d\n"
          "version_deps.ok=%s (edges=%d, xdom=%d)"
          % (dag_p, contract_p, plan_p, ok, len(plan.get("groups") or []),
             vrep["ok"], len(vrep["edges"]),
             sum(1 for e in vrep["edges"] if e.get("xdom"))),
          file=sys.stderr)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
