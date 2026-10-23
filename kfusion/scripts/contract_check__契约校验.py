#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contract_check__契约校验.py — ECoS 的 fail-closed 契约校验器

做什么：
  读「任务契约」(task_contract) + 「平台能力」(platform_caps)，
  判定该任务能否部署到某个运行域；**无法证明合法就拒绝**（非零退出）。

判定规则（每条一个稳定诊断码）：
  E_STRUCT            结构缺失 / 类型错
  E_RANGE             period_us / deadline_us < 1
  E_DEADLINE_GT_PERIOD  deadline_us > period_us
  E_EFFECT_UNKNOWN    effects 里出现白名单以外的值（等价 UNKNOWN）
  E_CONTEXT_UNKNOWN   context 不在全局白名单
  E_CONTEXT           该平台不支持这个 context
  E_SLEEP_IN_IRQ      在 hardirq/softirq 上下文里声明 sleep / blocking_io
  E_MMIO_FORBIDDEN    平台 allow_mmio=false 但阶段声明了 mmio
  E_DEP_UNKNOWN       depends_on 指向不存在的阶段
  E_DEP_CYCLE         阶段依赖成环

用法：
  python contract_check__契约校验.py <contract.json> --platforms <caps.json> [--platform NAME|all]
  python contract_check__契约校验.py ... --json      # 机器可读
退出码：0 全部通过 / 1 有违例 / 2 用法或读文件失败

⚠️ 设计取舍：本脚本**不依赖** jsonschema（板子上没有），规则用代码实现；
   若环境里有 jsonschema，会额外对 .schema.json 做一次结构校验（双重保险）。
"""
import json
import os
import sys

# 已知的结构性效果（这些本身不构成拒绝理由，是否合法由上下文/平台规则决定）
KNOWN_EFFECTS = {"pure", "state_read", "state_write", "mmio", "sleep", "blocking_io"}
# 两个"可疑"标记，语义不同：
#   unknown  —— 分析器没能证明任何东西（含解析不出的间接调用）→ 一律拒绝
#   external —— 名字认识但语义未归档的外部调用 → 默认拒绝，--allow-external 显式放行
# 曾经的错误：把 external 混在 EFFECTS 白名单里，于是"标记了未知调用"却不阻止部署，
# 与 fail-closed 原则直接冲突（2026-10-23 审查 P0）。
EFFECTS = KNOWN_EFFECTS | {"external", "unknown"}
CONTEXTS = {"thread", "softirq", "hardirq", "timer_cb"}
IRQ_CTX = {"softirq", "hardirq"}
NEED_SLEEP_OK = {"sleep", "blocking_io"}


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:                      # noqa: BLE001
        print(f"!! 读不了 {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def diag(codes, code, where, msg):
    codes.append({"code": code, "where": where, "msg": msg})


def check_structure(c, codes):
    if not isinstance(c.get("version"), int) or c.get("version") != 1:
        diag(codes, "E_STRUCT", "version", "必须是 1")
    task = c.get("task")
    if not isinstance(task, dict):
        diag(codes, "E_STRUCT", "task", "缺失或不是对象")
        return
    for k in ("name", "entry"):
        if not isinstance(task.get(k), str) or not task.get(k):
            diag(codes, "E_STRUCT", f"task.{k}", "必须是非空字符串")
    for k in ("period_us", "deadline_us"):
        if not isinstance(task.get(k), int):
            diag(codes, "E_STRUCT", f"task.{k}", "必须是整数")
    stages = c.get("stages")
    if not isinstance(stages, list) or not stages:
        diag(codes, "E_STRUCT", "stages", "必须是非空数组")
        return
    for i, s in enumerate(stages):
        w = f"stages[{i}]"
        if not isinstance(s, dict):
            diag(codes, "E_STRUCT", w, "不是对象")
            continue
        for k in ("name", "fn"):
            if not isinstance(s.get(k), str) or not s.get(k):
                diag(codes, "E_STRUCT", f"{w}.{k}", "必须是非空字符串")
        if not isinstance(s.get("context"), str):
            diag(codes, "E_STRUCT", f"{w}.context", "缺失")
        if not isinstance(s.get("effects"), list) or not s.get("effects"):
            diag(codes, "E_STRUCT", f"{w}.effects", "必须是非空数组")
        dep = s.get("depends_on", [])
        if not isinstance(dep, list) or any(not isinstance(d, str) for d in dep):
            diag(codes, "E_STRUCT", f"{w}.depends_on", "必须是字符串数组")


def check_time(c, codes):
    t = c["task"]
    p, d = t.get("period_us"), t.get("deadline_us")
    if isinstance(p, int) and p < 1:
        diag(codes, "E_RANGE", "task.period_us", f"必须 >= 1，实际 {p}")
    if isinstance(d, int) and d < 1:
        diag(codes, "E_RANGE", "task.deadline_us", f"必须 >= 1，实际 {d}")
    if isinstance(p, int) and isinstance(d, int) and d > p:
        diag(codes, "E_DEADLINE_GT_PERIOD", "task.deadline_us",
             f"deadline({d}) > period({p})：单周期内不可能满足")


def check_stages(c, platform, codes, allow_external=False):
    ctxs = set(platform.get("contexts") or [])
    allow_mmio = bool(platform.get("allow_mmio"))
    allow_sleep = platform.get("allow_sleep")   # None = 未声明
    supports_float = platform.get("supports_float")
    for i, s in enumerate(c.get("stages") or []):
        w = s.get("name") or f"stages[{i}]"
        ctx = s.get("context")
        eff = s.get("effects") or []

        # 完全没听过的效果值
        for e in [x for x in eff if x not in EFFECTS]:
            diag(codes, "E_EFFECT_UNKNOWN", w,
                 f"效果 '{e}' 不在白名单 → 按 UNKNOWN 拒绝（白名单: {sorted(EFFECTS)}）")

        # 分析器明确标了 unknown：无法证明安全，必须拒
        if "unknown" in eff:
            diag(codes, "E_UNPROVEN_EFFECT", w,
                 "分析器将该阶段标为 unknown（存在解析不出的调用/无法证明的操作）→ 拒绝")

        # external：名字已知但语义未归档；默认拒，需显式 --allow-external
        if "external" in eff and not allow_external:
            diag(codes, "E_EXTERNAL_UNVERIFIED", w,
                 "阶段含 external 调用（语义未归档）→ 默认拒绝；"
                 "确认安全后可用 --allow-external 放行")

        if ctx not in CONTEXTS:
            diag(codes, "E_CONTEXT_UNKNOWN", w, f"context '{ctx}' 不在全局白名单")
        elif ctx not in ctxs:
            diag(codes, "E_CONTEXT", w,
                 f"平台 '{platform.get('name')}' 只支持 {sorted(ctxs)}，不支持 '{ctx}'")

        if ctx in IRQ_CTX:
            bad = [e for e in eff if e in NEED_SLEEP_OK]
            if bad:
                diag(codes, "E_SLEEP_IN_IRQ", w,
                     f"上下文 '{ctx}' 里声明了 {bad}：中断上下文不能睡眠")

        if "mmio" in eff and not allow_mmio:
            diag(codes, "E_MMIO_FORBIDDEN", w,
                 f"平台 '{platform.get('name')}' 的 allow_mmio=false，但阶段声明了 mmio")

        # allow_sleep：之前 schema 里有这个字段但校验器完全没用（审查指出）
        if allow_sleep is False:
            bad = [e for e in eff if e in NEED_SLEEP_OK]
            if bad:
                diag(codes, "E_SLEEP_FORBIDDEN", w,
                     f"平台 '{platform.get('name')}' 的 allow_sleep=false，但阶段声明了 {bad}")

        # supports_float=False 的平台不接受浮点阶段（当前用 fixed 效果名间接表达）
        if supports_float is False and "float" in eff:
            diag(codes, "E_FLOAT_FORBIDDEN", w,
                 f"平台 '{platform.get('name')}' 的 supports_float=false，但阶段声明了 float")


def check_deps(c, codes):
    stages = c.get("stages") or []
    names = {s.get("name") for s in stages if isinstance(s, dict)}
    graph = {}
    for s in stages:
        if not isinstance(s, dict):
            continue
        n = s.get("name")
        for d in s.get("depends_on") or []:
            if d not in names:
                diag(codes, "E_DEP_UNKNOWN", n, f"依赖 '{d}' 不存在")
                continue
            graph.setdefault(d, set()).add(n)

    # 环检测（DFS）
    state = {}

    def dfs(n, stack):
        if state.get(n) == 1:
            cyc = " → ".join(stack[stack.index(n):] + [n])
            diag(codes, "E_DEP_CYCLE", n, f"依赖成环: {cyc}")
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        for m in graph.get(n, ()):
            if dfs(m, stack + [n]):
                return True
        state[n] = 2
        return False

    for n in names:
        if dfs(n, []):
            break


def maybe_schema_check(c, caps, base):
    try:
        import jsonschema  # noqa: F401
    except Exception:                             # noqa: BLE001
        return []
    import jsonschema
    out = []
    for inst, sp in ((c, "task_contract.schema.json"), (caps, "platform_caps.schema.json")):
        path = os.path.join(base, sp)
        if not os.path.exists(path):
            continue
        schema = load(path)
        try:
            jsonschema.validate(inst, schema)
        except jsonschema.ValidationError as exc:
            out.append({"code": "E_STRUCT", "where": sp, "msg": str(exc).splitlines()[0]})
    return out


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    contract_path = args[0]
    platforms_path = None
    only = None
    as_json = False
    allow_external = False
    i = 1
    while i < len(args):
        if args[i] == "--platforms":
            platforms_path = args[i + 1]
            i += 2
        elif args[i] == "--platform":
            only = args[i + 1]
            i += 2
        elif args[i] == "--allow-external":
            allow_external = True
            i += 1
        elif args[i] == "--json":
            as_json = True
            i += 1
        else:
            raise SystemExit(f"未知参数 {args[i]}")
    if not platforms_path:
        raise SystemExit("--platforms <platform_caps.json> 必填")

    c = load(contract_path)
    caps = load(platforms_path)
    base = os.path.dirname(os.path.abspath(contract_path))
    base = os.path.dirname(base)                  # examples/ 的上一级 = contracts/

    plats = caps.get("platforms") or []
    if not plats:
        # 曾经：空列表 → 循环 0 次 → bad=0 → 以成功退出，等于"什么都没检查就说没问题"
        print("!! platform_caps 里没有任何平台 → 无法判定，按拒绝处理", file=sys.stderr)
        sys.exit(2)
    if only and only != "all":
        plats = [p for p in plats if p.get("name") == only]
        if not plats:
            raise SystemExit(f"平台的 name 里没有 '{only}'")

    report = []
    bad = 0
    for p in plats:
        codes = []
        check_structure(c, codes)
        if not any(d["code"].startswith("E_STRUCT") for d in codes):
            check_time(c, codes)
            check_stages(c, p, codes, allow_external=allow_external)
            check_deps(c, codes)
        codes += maybe_schema_check(c, caps, base)
        ok = not codes
        bad += 0 if ok else 1
        report.append({"platform": p.get("name"), "ok": ok, "codes": codes})

    if as_json:
        print(json.dumps({"contract": contract_path, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        print(f"== {os.path.basename(contract_path)}  (task={c.get('task', {}).get('name')})")
        for r in report:
            mark = "✅ PASS" if r["ok"] else "❌ REJECT"
            print(f"   [{r['platform']}] {mark}")
            for d in r["codes"]:
                print(f"      {d['code']:<20} {d['where']:<22} {d['msg']}")
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()
