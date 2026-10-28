# -*- coding: utf-8 -*-
"""verify_generated — Gate 3：与规划器分离的「验生成物」独立门禁。

emit.py 生成 ecos_fused_body.c 之后，本模块**重新解析这份生成的 C 源码**，
独立核对它确实兑现了 plan/dag 契约——不信任 emit.py 自己的生成逻辑：

  · 覆盖  ：生成的阶段集合 == 方案声明的阶段集合（不缺、不夹带）
  · 顺序  ：生成体内的阶段顺序 == 方案的释放/拓扑序（不偷偷重排）
  · 计时钩子：每个阶段都被 ecos_stage_enter(k)/exit(k) 包裹，且 k 是
             0..N-1 全局连续——否则 exec_probe 实测会把计时错配到别的阶段
  · 跨域桥 ：若方案/IR 声明了 N 处订阅/发布，生成的周期体里必须真有对应的
             ecos_xdom_sub / ecos_xdom_pub（不被静默丢弃）

这层检查把"生成的代码"当黑盒重新读一遍，正是「生成可独立检查的实际代码」
那句创新主张要落地的门禁。规划器、emit 各自可能写错，但生成的 C 若与契约不符，
这里一定能抓出来。
"""

import re

# 阶段调用行：\tstage_sample();\t/* stage_sample，相位 0 µs */
_STAGE_RE = re.compile(
    r'^\s*([A-Za-z_]\w*\s*\([^)]*\))\s*;\s*/\*\s*([A-Za-z_]\w*)\s*[，,]\s*相位')
_ENTER_RE = re.compile(r'ecos_stage_enter\(\s*(\d+)\s*\)')
_EXIT_RE = re.compile(r'ecos_stage_exit\(\s*(\d+)\s*\)')
_SUB_RE = re.compile(r'ecos_xdom_sub\(\s*\d+\s*,\s*&?\w+')
_PUB_RE = re.compile(r'ecos_xdom_pub\(\s*\d+\s*,\s*\w+')


def parse_body(text):
    """解析生成的周期体，返回有序阶段列表与跨域桥计数。

    每个阶段：{name, call, enter_idx, exit_idx}
    """
    # 按 ecos_body_gN(void) { 切分；blocks[0] 是文件头（无阶段），其后是各函数体。
    blocks = re.split(r'(?m)^void\s+ecos_body_\w+\s*\(void\)\s*\{', text)
    stages = []
    for blk in blocks[1:]:
        for line in blk.splitlines():
            m = _STAGE_RE.match(line)
            if m:
                stages.append({"name": m.group(2), "call": m.group(1),
                               "enter_idx": None, "exit_idx": None})
        # 钩子索引：块内按顺序收集 enter/exit，按顺序与阶段配对
        # （生成体里每个阶段恰有一对 enter/exit，且顺序一致）。
        enters = [int(x) for x in _ENTER_RE.findall(blk)]
        exits = [int(x) for x in _EXIT_RE.findall(blk)]
        for i, st in enumerate(stages):
            if i < len(enters):
                st["enter_idx"] = enters[i]
            if i < len(exits):
                st["exit_idx"] = exits[i]
    n_sub = len(_SUB_RE.findall(text))
    n_pub = len(_PUB_RE.findall(text))
    return {"stages": stages, "xdom_subs": n_sub, "xdom_pubs": n_pub}


def expected_names_from_plan(plan):
    """从 plan.groups[*].members 提取有序阶段名（释放/拓扑序）。"""
    names = []
    for g in (plan.get("groups") or []):
        for m in (g.get("members") or []):
            names.append(m)
    return names


def verify(body_text, plan=None, expected_names=None, deadline_us=None,
           expected_xdom_subs=0, expected_xdom_pubs=0):
    """独立核对生成体是否兑现契约。

    返回 {ok, errors, warnings, found, expected, parsed}。
    """
    parsed = parse_body(body_text)
    stages = parsed["stages"]
    found = [s["name"] for s in stages]
    errors = []
    warnings = []

    exp = (list(expected_names) if expected_names is not None
           else expected_names_from_plan(plan) if plan else [])
    if not exp:
        errors.append("无法推导期望阶段集合（既没给 expected_names 也没给 plan.groups）")
    else:
        set_exp = set(exp)
        set_found = set(found)
        missing = set_exp - set_found
        extra = set_found - set_exp
        if missing:
            errors.append("生成体缺阶段：%s" % ", ".join(sorted(missing)))
        if extra:
            errors.append("生成体夹带未声明阶段：%s" % ", ".join(sorted(extra)))
        if set_exp == set_found and found != exp:
            errors.append("生成体阶段顺序与方案不一致：\n  方案 %s\n  生成 %s"
                         % (exp, found))

    # 计时钩子：0..N-1 连续且每个阶段配对
    idxs = [s["enter_idx"] for s in stages if s["enter_idx"] is not None]
    if idxs:
        if sorted(idxs) != list(range(len(idxs))):
            errors.append("计时钩子 enter 索引非 0..N-1 连续：%s" % sorted(idxs))
        for s in stages:
            if s["enter_idx"] is None or s["exit_idx"] is None:
                errors.append("阶段 %s 未被 ecos_stage_enter/exit 包裹（实测计时会错配）"
                             % s["name"])
            elif s["enter_idx"] != s["exit_idx"]:
                errors.append("阶段 %s enter/exit 索引不一致（%s/%s）"
                             % (s["name"], s["enter_idx"], s["exit_idx"]))
    elif stages:
        errors.append("生成体有阶段但没有任何计时钩子（exec_probe 无法计时）")

    # 跨域桥：声明了就必须生成，否则就是静默丢弃
    if expected_xdom_subs and parsed["xdom_subs"] != expected_xdom_subs:
        errors.append("跨域订阅钩子数量不符：期望 %d，生成 %d（桥被静默丢弃？）"
                     % (expected_xdom_subs, parsed["xdom_subs"]))
    if expected_xdom_pubs and parsed["xdom_pubs"] != expected_xdom_pubs:
        errors.append("跨域发布钩子数量不符：期望 %d，生成 %d（桥被静默丢弃？）"
                     % (expected_xdom_pubs, parsed["xdom_pubs"]))

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings,
            "found": found, "expected": exp, "parsed": parsed}


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 3:
        print("usage: verify_generated.py <body.c> <plan.json> [--xdom-sub N --xdom-pub M]")
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        text = f.read()
    with open(sys.argv[2], encoding="utf-8") as f:
        pl = json.load(f)
    xsub = xpub = 0
    if "--xdom-sub" in sys.argv:
        xsub = int(sys.argv[sys.argv.index("--xdom-sub") + 1])
    if "--xdom-pub" in sys.argv:
        xpub = int(sys.argv[sys.argv.index("--xdom-pub") + 1])
    rep = verify(text, plan=pl, expected_xdom_subs=xsub, expected_xdom_pubs=xpub)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0 if rep["ok"] else 3)
