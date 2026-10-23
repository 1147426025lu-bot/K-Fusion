#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""effect_to_contract__摘要转契约.py — 把 EffectSummaryAnalysis 的 JSON 填进任务契约

为什么要有这一步（这是 ECoS `feat(analysis)` 的另一半）：
  上一轮 `feat(contract)` 里，`stages[].effects` 是**人写的**。人能写错，也能忘写。
  本脚本让编译器（LLVM Pass `plc-effect-summary`）来回答"这段代码到底会不会睡／碰 MMIO"，
  人只负责声明**拓扑与上下文**（哪些函数组成一个阶段、谁依赖谁）。

用法：
  python effect_to_contract__摘要转契约.py \
      --summary effect_summary.json \
      --skeleton stages.json \
      --out task_contract.json

输入 1：effect_summary.json（Pass 输出）
  { "version":1, "functions": { "fir_filter": {"effects":["state_read","state_write"],
    "allowed_contexts":["thread","softirq"], "recursive":false, "calls":[...],
    "unknown_calls":[...]} } }

输入 2：stages.json（人写，只写拓扑）
  { "task": {"name":"plc_chain","entry":"chain_main","period_us":1000,"deadline_us":800},
    "stages": [ {"name":"sensor_prepare","fn":"sensor_prepare","depends_on":[],
                 "context":"thread"} ] }

输出：完整的 task_contract.json（effects 由摘要填），可直接喂 contract_check。

判定规则（保守）：
  - 一个阶段可以由多个函数组成（fn 可以是列表）→ 效果取并集
  - 函数不在摘要里 → 记 `external` 并告警（宁拒绝不放过）
  - 摘要说 recursive → 保留并告警（不动点结果可能偏保守）
  - 上下文与 allowed_contexts 冲突 → 不在这里改，交给 contract_check 报 E_SLEEP_IN_IRQ
"""
import argparse
import io
import json
import sys

EFFECT_ORDER = ["pure", "state_read", "state_write", "mmio", "sleep",
                "blocking_io", "external"]


def load_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def merge_effects(summary_funcs, fnames, warns, stage_name):
    """把一个阶段里所有函数的效果取并集。"""
    eff = set()
    for fn in fnames:
        info = summary_funcs.get(fn)
        if info is None:
            warns.append(f"[W_FN_MISSING] 阶段 '{stage_name}' 的函数 '{fn}' 不在摘要里 → 记 external")
            eff.add("external")
            continue
        eff |= set(info.get("effects") or [])
        if info.get("recursive"):
            warns.append(f"[W_RECURSION] 阶段 '{stage_name}' 的函数 '{fn}' 在递归环里，"
                         f"效果是保守并集")
        if info.get("unknown_calls"):
            unk = ", ".join(sorted(info["unknown_calls"]))
            warns.append(f"[W_UNKNOWN_CALL] 阶段 '{stage_name}' 的函数 '{fn}' 调用了未识别外部：{unk}")
    if not eff:
        eff = {"pure"}
    # 去掉被 sleep/blocking_io 蕴含的多余项？不——保留全部，让读者看到完整证据。
    return [e for e in EFFECT_ORDER if e in eff]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--skeleton", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    summ = load_json(args.summary)
    skel = load_json(args.skeleton)
    funcs = summ.get("functions") or {}
    warns = []

    stages_out = []
    for st in skel.get("stages", []):
        fn = st.get("fn")
        fnames = fn if isinstance(fn, list) else [fn]
        fnames = [f for f in fnames if f]
        if not fnames:
            raise SystemExit(f"阶段 '{st.get('name')}' 缺少 fn")

        eff = merge_effects(funcs, fnames, warns, st.get("name", "?"))
        item = {
            "name": st.get("name"),
            "fn": fn,
            "depends_on": st.get("depends_on", []),
            "context": st.get("context", "thread"),
            "effects": eff,
        }
        for extra in ("notes",):
            if extra in st:
                item[extra] = st[extra]
        # 把摘要里的 allowed_contexts 一并带出，便于人工核对（contract_check 不读这个字段）
        ctx = set()
        for f in fnames:
            info = funcs.get(f) or {}
            ctx |= set(info.get("allowed_contexts") or [])
        if ctx:
            item["derived_allowed_contexts"] = [c for c in ("thread", "softirq") if c in ctx]
        stages_out.append(item)

    contract = {
        "version": 1,
        "task": skel.get("task", {}),
        "stages": stages_out,
        "generated": {
            "by": "EffectSummaryAnalysis + effect_to_contract",
            "summary_module": summ.get("module"),
            "note": "effects 由 LLVM 分析推导，非人工声明；拓扑与 context 来自 skeleton",
        },
    }
    if skel.get("observations"):
        contract["observations"] = skel["observations"]

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(contract, f, ensure_ascii=False, indent=2)
        f.write("\n")

    for w in warns:
        print(w, file=sys.stderr)
    print(f"写出 {args.out}（{len(stages_out)} 个阶段）", file=sys.stderr)
    for s in stages_out:
        print(f"  {s['name']:24s} {'/'.join(s['effects'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
