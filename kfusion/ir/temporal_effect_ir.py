# -*- coding: utf-8 -*-
"""temporal_effect_ir — Gate 2 的时序效应中间表示（TEIR）。

设计目标（来自项目规划）：用一个编译器从「既有 C 周期程序」自动提取出
**计算事实 / 时间事实 / 执行事实**，让下游（规划器、验证器、选择性内核化
成本模型）能独立检查，而不是靠手写 DAG 或靠运行时"看起来对"。

TEIR 表达三类事实：
  · 计算事实（computation）：阶段、其读全局、写全局、执行时间（占位需实测）。
  · 时间事实（temporal）：周期/相位；数据版本依赖——消费者 B 读的是生产者 A
    在**同一周期 k** 内写的版本（r_i(k) 依赖 w_i(k)），这是「功能等价 ≠ 时间正确」
    要钉死的那条约束。
  · 执行事实（execution）：该方案/阶段能否进内核（来自提取器的 K-C0 结论）。

诚实边界（v1，写进 meta）：
  · exec_us 未实测时按 --default-exec-us 占位并标记，不得进论文。
  · 同周期数据边只覆盖「调用序中前置阶段写、本阶段读」的全局；读但无同周期
    生产者的是 cross_period（外部输入 / 上一周期状态），单独列出，不算内部边。
  · 多速率 / 跨域版本依赖（r_i(k) 与 r_j(k-1)）属于 Gate 2 后期 + Gate 3，这里只
    给出单域同周期骨架。
"""

import io
import os


def _write(p, obj):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        json_dump(obj, f)


def json_dump(obj, f):
    # 延迟 import，避免与调用方 json 实现耦合
    import json
    json.dump(obj, f, ensure_ascii=False, indent=2)
    f.write("\n")


def build_teir(extract, dag, ex, order, stage_rw, data_edges,
               cross_inputs, kernelizable):
    """从提取结果 + 由 ast_to_plan 算出的依赖，构造 TEIR dict。

    stage_rw: {stage: {"reads":[g...], "writes":[g...]}}
    data_edges: [{from,to,var,version}]  同周期生产者->消费者
    cross_inputs: [{stage, var}]          读但无同周期生产者
    kernelizable: bool（K-C0.ok）
    """
    nodes = []
    for i, n in enumerate(order):
        nodes.append({
            "name": n,
            "fns": [n],
            "reads": stage_rw[n]["reads"],
            "writes": stage_rw[n]["writes"],
            "exec_us": ex.get(n),
            "phase_us": (dag.get("phase") or {}).get("offsets", {}).get(n),
            "kernelizable": kernelizable,
        })

    # 状态版本账本：每个全局被哪些阶段写/读（按顺序），用于版本推理
    state_versions = {}
    for g in [gl.get("name") for gl in (extract.get("globals") or [])]:
        state_versions[g] = {"written_by": [], "read_by": []}
    for n in order:
        for g in stage_rw[n]["writes"]:
            if g in state_versions:
                state_versions[g]["written_by"].append(n)
        for g in stage_rw[n]["reads"]:
            if g in state_versions:
                state_versions[g]["read_by"].append(n)

    task = (dag.get("meta") or {}).get("task") or {}
    teir = {
        "version": 1,
        "ir": "TEIR",
        "source": extract.get("source"),
        "task": {
            "name": task.get("name"),
            "entry": task.get("entry"),
            "period_us": task.get("period_us"),
            "deadline_us": task.get("deadline_us"),
        },
        "globals": [{"name": gl.get("name"), "type": gl.get("type")}
                    for gl in (extract.get("globals") or [])],
        "nodes": nodes,
        # 时间事实：数据版本依赖（same_period = 消费者读同周期生产者写的版本）
        "data_edges": data_edges,
        "state_versions": state_versions,
        # 跨周期输入：读但本周期无人写的全局（外部/前周期状态）
        "cross_period_inputs": cross_inputs,
        # 执行事实：能否内核化
        "kernelizable": kernelizable,
        "meta": {
            "note": ("Gate 2 TEIR v1：单域同周期数据依赖骨架；exec_us 占位需实测；"
                     "跨域/多速率版本依赖见 Gate 2 后期 + Gate 3。"),
            "placeholder_exec": (dag.get("meta") or {}).get("auto", {}).get("placeholder_exec", []),
        },
    }
    return teir


def write_teir(teir, path):
    _write(path, teir)
    return path
