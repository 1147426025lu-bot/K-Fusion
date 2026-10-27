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
import xdom_bridge                         # noqa: E402


def _write(out_dir, name, text):
    p = os.path.join(out_dir, name)
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
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
    ap.add_argument("--digest-var", action="append", default=[],
                    help="要发布成只读 sysfs 参数的 int32_t 全局量（可重复）。"
                         "没有观察点就只能证明『能加载』，证明不了『算得对』")
    ap.add_argument("--xdom-lag", type=int, default=0,
                    help="跨域消费者取『LAG 拍前』的值（受控 A/B 注入延迟）；0=取最新（默认）")
    ap.add_argument("--xdom-mode", default="latest", choices=["latest", "oldest", "one"],
                    help="跨域取数语义（A/B 开关，同一代码只切这一项）："
                         "latest=每拍抽干取最新（默认）；"
                         "oldest=每拍抽干取最旧（旧桥的选择顺序）；"
                         "one=每拍只取一条、其余留在环里（旧桥的消费速率，会积压）")
    ap.add_argument("--xdom-max-age", type=int, default=None,
                    help="跨域数据年龄预算（拍）：超过即拒收并计 n_stale。"
                         "不给则取契约 timing_constraints.xdom_max_age_ticks；都没有=0（只记录不拒）")
    ap.add_argument("--probe", action="store_true",
                    help="加死区探针：量环闭合边（上拍末组结束 → 本拍首组开始），"
                         "输出 dz_min/avg/max_ns 与采样数（Linux）/ # dz 行（NuttX）")
    ap.add_argument("--nuttx-progname", default=None,
                    help="NuttX app 的 PROGNAME（默认取模块名）。NuttX 的 builtin "
                         "注册表按它生成入口符号 <PROGNAME 去横线>_main，"
                         "源码里的入口名必须与之一致——两者都由这里派生。"
                         "接进既有 app 骨架时用它对齐（如 fiveloop）")
    ap.add_argument("--nuttx-main", default=None,
                    help="NuttX 后端的入口函数名。**一般不用给**：默认由 "
                         "--nuttx-progname 派生（<PROGNAME 去横线>_main）。"
                         "显式给时必须与派生值一致，否则报错——"
                         "不一致会在板上链接期变成 undefined reference")
    ap.add_argument("--xdom", default=None,
                    help="拆分器写的 per-domain meta.json（PR10-C）。给了就在周期体里"
                         "为跨域数据插发布/订阅，并生成 ecos_xdom.{h,c}")
    ap.add_argument("--experiment-period-us", type=int, default=None,
                    help="【装置实验】覆盖本域控制周期（µs）。与方案不一致时必须同时给 "
                         "--allow-experiment-deploy；产物写 EXPERIMENT.txt、不得进论文主张。"
                         "多速率装置就靠它：一端 1000、另一端 2000")
    ap.add_argument("--experiment-shift-us", type=int, default=0,
                    help="【装置实验】把每个组的相位整体平移 N µs（按周期取模）。"
                         "用于同一速率下只切相位做 A/B——多速率装置里相位才第一次有非平凡作用")
    ap.add_argument("--allow-infeasible-deployment", action="store_true",
                    help="承认这个跨域部署在数据年龄预算内**排不出安全相位**（PR11-C 判据），"
                         "仍然生成。产物写 EXPERIMENT.txt、不得进论文主张。"
                         "唯一用途是复现已证明不可行的装置（§15/§16 的多速率臂）")
    ap.add_argument("--allow-experiment-deploy", action="store_true",
                    help="承认这是装置实验（周期/相位偏离已验证的方案）。"
                         "与 --allow-unverified 同一套规矩：产物明确标记、不得进论文主张")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="允许用未验证方案生成（产物标记为对照用，不得进论文）")
    a = ap.parse_args()

    plan = fusion_ir.load(a.plan)
    dag = fusion_ir.load(a.dag)
    contract = fusion_ir.load(a.contract) if a.contract else None
    xdom = fusion_ir.load(a.xdom) if a.xdom else None
    # ring-0 宿主的桥要走 ioremap（IVSHMEM 不在内核线性映射里，直写会 SIGSEGV）
    if xdom and a.target in ("linux-kernel", "both"):
        xdom = dict(xdom)
        xdom["kernel"] = True
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

    # ---- 门 0：跨域方案不能合成成一个宿主 ----
    # 本 emitter 的产物是"一个宿主 + 一个周期体"。如果方案的组分属多个域
    # （DAG 节点带 domain），把两组塞进同一个模块就等于**绕过了 PR10-B 的硬约束**
    # （跨域融合不存在），而且生成出来的东西在板上根本跑不起来——
    # 两个 cell 各有内核，一个 .ko 不可能同时属于两边。
    # 正确做法：按域拆成两份 plan，各配自己的阶段源码，分别 emit。
    # 域的权威来源是 DAG 节点；方案里的 group.domain 只是规划器写下来的摘要，
    # 两处都有时以 DAG 为准（摘要可能与旧版 DAG 对不上）。
    dag_dom = {n["name"]: n["domain"] for n in (dag.get("nodes") or [])
               if n.get("domain")}
    doms = {}
    for g in (plan.get("groups") or []):
        for m in (g.get("members") or []):
            d = dag_dom.get(m) or g.get("domain")
            if d:
                doms[m] = d
    if len(set(doms.values())) > 1:
        per = {}
        for m, d in doms.items():
            per.setdefault(d, []).append(m)
        print("❌ 跨域方案：本 emitter 只合成**单域**产物，而方案的组分属 %d 个域："
              % len(set(doms.values())), file=sys.stderr)
        for d, ms in sorted(per.items()):
            print("     %-10s %s" % (d, ", ".join(sorted(ms))), file=sys.stderr)
        print("   → 按域拆成两份 plan、各配自己的阶段源码，分别 emit；"
              "两端的通信走 PR10 的跨域环，不是一个宿主", file=sys.stderr)
        return 6

    name = a.name or ((dag.get("meta") or {}).get("task") or {}).get("name") or "ecos_fused"
    name = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in name)

    # ---- 跨域数据年龄预算：一处声明、两处生效 ----
    # 契约（timing_constraints.xdom_max_age_ticks）是**声明**，生成的
    # ECOS_XDOM_MAX_AGE 是**运行时强制**（超限拒收 + n_stale 计数）。
    # 两者必须同一个数，否则"契约说 age≤1、代码却照收 age=3"就是又一处静默失效
    # ——PR10-E 已经栽过一次（上限写在 control 下、从来没被读到）。
    ctr = (contract or {}).get("timing_constraints") or {}
    c_budget = ctr.get("xdom_max_age_ticks")
    if c_budget is not None:
        try:
            c_budget = int(c_budget)
        except (TypeError, ValueError):
            print("❌ 契约 timing_constraints.xdom_max_age_ticks 不是整数：%r"
                  % (c_budget,), file=sys.stderr)
            return 2
    if a.xdom_max_age is None:
        xdom_max_age = c_budget if c_budget is not None else 0
        if c_budget is not None:
            print("  跨域数据年龄预算 %.0f 拍（来自契约 timing_constraints）" % c_budget)
    else:
        if c_budget is not None and int(a.xdom_max_age) != c_budget:
            print("❌ --xdom-max-age=%d 与契约 xdom_max_age_ticks=%d 不一致：\n"
                  "  契约是声明、生成代码是强制，两者必须同源；"
                  "改哪一个都行，但不能各说一套" % (a.xdom_max_age, c_budget),
                  file=sys.stderr)
            return 2
        xdom_max_age = int(a.xdom_max_age)

    ir = fusion_ir.FusionIR.from_plan(plan, dag, contract=contract, exec_us=exec_us,
                                      calls=calls, source_name=name,
                                      digest_vars=a.digest_var, probe=a.probe,
                                      xdom=xdom, subset=bool(xdom))

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

    # ---- 装置实验：周期/相位覆盖（必须显式承认，且留痕）----
    # 为什么单独一个口子而不是直接改契约：契约是**已验证**的那份，改它等于让验证结论失效。
    # 实验装置要的是"同一份方案、换一个部署"，所以这里只改生成用的节拍，并把偏离写进
    # EXPERIMENT.txt —— 与 --allow-unverified 同一套规矩（产物不得进论文主张）。
    exp_notes = []
    if a.experiment_period_us is not None or a.experiment_shift_us:
        if not a.allow_experiment_deploy:
            print("❌ 给了 --experiment-* 但没给 --allow-experiment-deploy：\n"
                  "  这些开关会让产物偏离已通过验证的方案（周期/相位），"
                  "必须显式承认是装置实验", file=sys.stderr)
            return 2
    if a.experiment_period_us is not None:
        want = int(a.experiment_period_us)
        if want <= 0:
            print("❌ --experiment-period-us 必须 > 0", file=sys.stderr)
            return 2
        exp_notes.append("周期：方案 %.0f µs → 装置 %d µs" % (ir.period_us or 0, want))
        ir.period_us = want
    if a.experiment_shift_us:
        if ir.period_us:
            for g in ir.groups:
                g.phase_us = (float(g.phase_us or 0) + float(a.experiment_shift_us)) % float(ir.period_us)
        exp_notes.append("相位整体平移 %+d µs（按周期 %.0f µs 取模）"
                         % (a.experiment_shift_us, ir.period_us or 0))
    if exp_notes:
        print("⚠ 装置实验（偏离已验证方案）：", file=sys.stderr)
        for n in exp_notes:
            print("    %s" % n, file=sys.stderr)

    # ---- 契约声明的各域周期必须真的落到生成代码（PR11-C）----
    # 否则契约说 root=2000、生成的却是 1000：声明与实现不一致，
    # 后面的可行性判据就变成了"拿一个不存在的部署去算"。
    if xdom and a.experiment_period_us is None:
        _dom = str(xdom.get("domain") or "")
        _decl = ((contract or {}).get("timing_constraints") or {}).get("domain_period_us") or {}
        if _dom and _dom in _decl:
            try:
                _want = int(float(_decl[_dom]))
            except (TypeError, ValueError):
                print("❌ 契约 timing_constraints.domain_period_us[%s] 不是数字：%r"
                      % (_dom, _decl[_dom]), file=sys.stderr)
                return 2
            if int(ir.period_us or 0) != _want:
                print("  周期取自契约（域 %s）：%d µs（plan 里是 %.0f µs）"
                      % (_dom, _want, ir.period_us or 0))
                ir.period_us = _want
    elif xdom and a.experiment_period_us is not None:
        _dom = str(xdom.get("domain") or "")
        _decl = ((contract or {}).get("timing_constraints") or {}).get("domain_period_us") or {}
        if _dom and _dom in _decl and int(float(_decl[_dom])) != int(a.experiment_period_us):
            print("❌ --experiment-period-us=%d 与契约 domain_period_us[%s]=%s 冲突"
                  % (int(a.experiment_period_us), _dom, _decl[_dom]), file=sys.stderr)
            return 2

    # 重同步开关：只认契约（命令行不加开关，避免与契约脱钩）。
    # ⚠ 还必须**只有一个域**动手：age 是两个域的**相对**相位（只有一个自由度），
    #   两侧各按自己的门限搬，等于对同一个量下两次控制——实测（§26.4）两侧在同一秒
    #   一个 +500 一个 −500，相对相位一次被推 1000 µs（整拍），当场制造了一批拒收。
    #   所以契约必须写明"谁动"，另一侧的桥与宿主完全不生成这套代码。
    _ctr_rs = (contract or {}).get("timing_constraints") or {}
    _resync_on = bool(int(_ctr_rs.get("xdom_resync_us") or 0))
    _rs_master = str(_ctr_rs.get("xdom_resync_domain") or "").strip()
    _this_dom = str((xdom or {}).get("domain") or "").strip() if xdom else ""
    _is_rs_master = bool(_resync_on and _this_dom and _this_dom == _rs_master)

    # ---- 跨域部署可行性（PR11-C）：排不下就不生成 ----
    # 为什么放在装置覆盖之后：--experiment-period-us 会改本域周期，判据必须用**最终**节拍。
    # 判据本身在 xdom_timeline.check_edge（事件时间线模型），这里只负责"拒绝或放行"。
    if xdom:
        try:
            _tl_dir = os.path.normpath(os.path.join(_HERE, "..", "..", "scripts", "ecos"))
            if _tl_dir not in sys.path:
                sys.path.insert(0, _tl_dir)
            import xdom_timeline as _tl
            _ctr = (contract or {}).get("timing_constraints") or {}
            _per = {}
            for k, v in (_ctr.get("domain_period_us") or {}).items():
                try:
                    _per[str(k)] = float(v)
                except (TypeError, ValueError):
                    pass
            _base = float((dag.get("phase") or {}).get("period_us") or 0) or None
            _drift = float(_ctr.get("xdom_drift_ppm") or 0)
            _jitter = float(_ctr.get("xdom_jitter_us") or 0)
            _p_self = float(ir.period_us or 0)
            _bad, _narrow = [], []
            # 两个方向都要看：我消费谁（我是消费者）+ 谁消费我（我是生产者）。
            # 只看一个方向会漏：$P_p$ 与 $P_c$ 互换后可行性完全不同
            # （快生产者 + 慢消费者 ⇒ age 只在 {a,a+P_c} 摆动；慢生产者 + 快消费者则安全）。
            _pairs = [("from", _s.get("from")) for _s in (xdom.get("subscribe") or [])] \
                   + [("to", _s.get("to")) for _s in (xdom.get("publish") or [])]
            for _dir, _peer in _pairs:
                _peer = str(_peer)
                _peer_p = _per.get(_peer, _base) or _p_self
                if _dir == "from":          # 对端生产、本域消费
                    _pp, _pc = _peer_p, _p_self
                else:                       # 本域生产、对端消费
                    _pp, _pc = _p_self, _peer_p
                if int(xdom_max_age) <= 0:
                    print("⚠ 未声明跨域数据年龄预算（xdom_max_age_ticks）："
                          "无法判定这个部署排不排得下安全相位", file=sys.stderr)
                    continue
                _r = _tl.check_edge(_pp, _pc, float(xdom_max_age) * _pc,
                                    drift_ppm=_drift, jitter_us=_jitter)
                _who = ("%s(%.0f µs) → 本域(%.0f µs)" % (_peer, _pp, _pc)
                        if _dir == "from" else
                        "本域(%.0f µs) → %s(%.0f µs)" % (_pp, _peer, _pc))
                if not _r["ok"]:
                    _bad.append("%s，预算 %d 拍，抖动 %.0f µs，漂移 %.0f ppm ⇒ %s"
                                % (_who, int(xdom_max_age), _jitter, _drift,
                                   _r["reason"]))
                elif _r["frac"] < 0.10:
                    _narrow.append("%s ⇒ %s" % (_who, _r["reason"]))
            for _n in _narrow:
                print("⚠ 跨域相位窗很窄：%s（漂移会让它慢慢走掉）" % _n, file=sys.stderr)
            if _bad:
                if not a.allow_infeasible_deployment:
                    for _b in _bad:
                        print("❌ 跨域部署不可行：%s" % _b, file=sys.stderr)
                    print("   → 事件时间线在生成期就能判定，不必等板上跑一轮：\n"
                          "     ① 放宽契约的 xdom_max_age_ticks  ② 让两域同速率  "
                          "③ 窗宽 0 时调相位没用\n"
                          "   若只是要复现已知不可行的装置，加 --allow-infeasible-deployment "
                          "（产物会标 EXPERIMENT.txt）", file=sys.stderr)
                    return 3
                for _b in _bad:
                    print("⚠ 跨域部署不可行（已承认）：%s" % _b, file=sys.stderr)
                exp_notes.append("跨域部署在年龄预算内不可行（已承认）：" + "；".join(_bad))
        except ImportError as _e:
            # 判不了就不生成：跨域部署的可行性没有"默认可行"这一说
            print("❌ 跨域部署可行性检查无法执行（找不到 xdom_timeline：%s）；"
                  "拒绝生成" % _e, file=sys.stderr)
            return 2

    # ---- 阶段函数必须真实存在 ----
    try:
        protos = stage_manifest.prototypes_for(ir, a.source)
    except stage_manifest.StageMissing as e:
        print("❌ %s" % e, file=sys.stderr)
        return 3

    # ⚠ NuttX 产物的入口名校验必须在**任何落盘之前**做完：校验失败时
    #   不该留下半成品目录（之前是先写 body 再校验，失败后 out-dir 里
    #   还留着一堆文件，下次跑会误以为"上次生成过了"）。
    nuttx_files = None
    if a.target in ("nuttx", "both"):
        try:
            nuttx_files = nuttx_em.emit(ir, name,
                                        main_name=a.nuttx_main,
                                        probe=getattr(ir, "probe", False),
                                        xdom=bool(xdom),
                                        prog_name=a.nuttx_progname,
                                        xdom_resync=_is_rs_master)
        except ValueError as e:
            # 入口名与 PROGNAME 不一致 → 生成期就拒，不让它跑到板上才炸
            print("emit: %s" % e, file=sys.stderr)
            return 2

    os.makedirs(a.out_dir, exist_ok=True)
    written = []
    written.append(_write(a.out_dir, "ecos_fused_body.h", body_emit.emit_body_h(ir)))
    written.append(_write(a.out_dir, "ecos_fused_body.c",
                          body_emit.emit_body_c(ir, protos)))
    written.append(_write(a.out_dir, "fusion_ir.json",
                          json.dumps(ir.to_json(), ensure_ascii=False, indent=2) + "\n"))
    if exp_notes:
        written.append(_write(a.out_dir, "EXPERIMENT.txt",
                              "装备实验产物 —— 不得用于论文主张\n"
                              "================================\n"
                              + "\n".join("- " + n for n in exp_notes)
                              + "\n\n原因：这些偏离没有经过 plan_verify，"
                                "\n合法性的唯一凭据是 ecos/plan_verify.py，"
                                "\n而不是\"它跑起来了\"。\n"))

    # ---- 跨域数据桥（PR10-C）----
    # 只在给了 --xdom 时生成；单域产物一个字节都不变。
    if xdom:
        # 周期长度要把物理计数器换算成"周期号"，必须把方案周期传给桥。
        # 周期性重同步（相位协调的最小实现）：只从契约读，默认关闭。
        # 触发/平移量都写进契约，是为了让"这次跑的是什么配置"能溯源——
        # 命令行再加一个开关就会和契约脱钩（PR10-E 的教训）。
        _ctr = (contract or {}).get("timing_constraints") or {}
        for fn, text in xdom_bridge.emit(
                xdom, period_us=int(ir.period_us or 0),
                max_age=int(xdom_max_age),
                lag=int(a.xdom_lag or 0),
                mode=a.xdom_mode,
                resync_us=int(_ctr.get("xdom_resync_us") or 0) if _is_rs_master else 0,
                resync_at_us=int(_ctr.get("xdom_resync_at_us") or 0) if _is_rs_master else 0,
                resync_hi_us=int(_ctr.get("xdom_resync_hi_us") or 0) if _is_rs_master else 0,
                resync_cool_us=(int(_ctr.get("xdom_resync_cooldown_us") or 60000000)
                                if _is_rs_master else 0),
        ).items():
            written.append(_write(a.out_dir, fn, text))
        print("=== 跨域数据桥 ===")
        print("  %s" % xdom_bridge.summary(xdom))
        print("  取数语义 %s%s，年龄预算 %s"
              % (a.xdom_mode,
                 ("（注入 %d 拍延迟）" % a.xdom_lag) if a.xdom_lag else "",
                 ("%d 拍（超限拒收）" % xdom_max_age) if xdom_max_age else "未设（只记录）"))
        n_sub = sum(len(s.xdom_sub) for g in ir.groups for s in g.stages)
        n_pub = sum(len(s.xdom_pub) for g in ir.groups for s in g.stages)
        if n_sub == 0 and n_pub == 0:
            print("  ⚠ 本域没有任何发布/订阅——如果它本该有，说明 --xdom 给错了",
                  file=sys.stderr)
            return 3

    # ⚠ 先把两个后端都生成好、再落盘：生成期校验失败时不能留下半成品目录
    #   （之前入口名校验放在写盘之后，失败后 out-dir 里还留着一堆文件，
    #   下次跑会以为"上次生成过了"）。
    if a.target in ("linux-kernel", "both"):
        d = a.out_dir if a.target == "linux-kernel" else os.path.join(a.out_dir, "linux")
        os.makedirs(d, exist_ok=True)
        for fn, text in linux_em.emit(ir, name, xdom=bool(xdom),
                                      xdom_resync=_is_rs_master).items():
            written.append(_write(d, fn, text))

    if nuttx_files is not None:
        d = a.out_dir if a.target == "nuttx" else os.path.join(a.out_dir, "nuttx")
        os.makedirs(d, exist_ok=True)
        for fn, text in nuttx_files.items():
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
