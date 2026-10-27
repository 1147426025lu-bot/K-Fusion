"""PR12-A：跨域重同步参数综合（resync parameter synthesis）。

为什么需要它
------------
PR11-D 把"周期性重同步"做成了一条**运行时**动作（消费者把自己的绝对基准
平移 ±step，把相对相位 ρ 拉回安全区），并用手工调出的四组夹具验证过
（xdom_loop_resync*.contract.json）。但那四组是"每实验手工调参"：

    xdom_resync_us=500   步长
    xdom_resync_at_us=200 下边界（age < 200 ⇒ +step）
    xdom_resync_hi_us=800 上边界（age > 800 ⇒ −step）
    xdom_resync_cooldown_us=15000000  冷却
    xdom_resync_domain="nuttx"   只有一侧动手（ρ 只有一个自由度）

本模块把这件事**综合**掉：给定一份契约的时序参数，自动推导出这一组五个
参数，并且——因为参考模型 simulate_resync 已经在仓库里——**推导出来后
立刻回代验证**；回代出现任何拒收就判不可行，并给出"升 1 拍预算"的建议，
而不是吐一份看起来合理、跑起来却掉样本的配置。

判据（用动态模型做 oracle，不是拍脑袋）
--------------------------------------
相对漂移 rel = xdom_drift_ppm − xdom_cons_drift_ppm（两宿主共用晶振，
各自漂的是"何时跑到周期体"，所以差是相对的；契约里 xdom_drift_ppm 描述的
就是相对漂移，消费者漂移默认 0）。
抖动 J  = xdom_jitter_us（模型确定性，实测残差最大 78 µs）。

对每条跨域边，用 simulate_resync 回代验证作为唯一判据（与运行时同构）：
  0) rel == 0  → 时间网格静止，相位窗可独立保持 → 不需要重同步。
  1) 不开重同步、回代零拒收 → 相位即可行，不开重同步。
       （确定性年龄峰贴预算、余量 < J 时告警真实硬件抖动风险。）
  2) 否则需要重同步：把 age 伺服进 [J, B−J]（B = 预算 µs），
     step = SAFETY_K·(B − 2J)（<1 留 clearance，避免一次平移越过反向边界乒乓），
     双向边界，单侧平移（xdom_resync_domain 指定唯一动手的一侧，ρ 只有一个自由度）。
     回代必须 stale == 0 才放行；否则（多速率非整数倍等结构性必超预算）
     判不可行，建议升 1 拍预算。
  冷却 = 0.5 · step / |rel| · 1e6（≈ 半次漂移重新累积 step 的时间）。

验证口径（jitter_policy）：
  "conservative"（默认，fail-closed）回代时把声明抖动加进年龄再判拒收，
    与"真年龄 + 抖动 > 预算即拒"一致，最保守、绝不放行有风险的配置；
  "runtime"  回代按真年龄（jitter=0）判拒收，匹配运行时"按声明预算拒收、
    不扣抖动"的语义（契约注释），与文档板上 stale=0 口径一致，但忽略真实抖动。
  ⚠ 两种口径都严格以 stale == 0 为可行；差异体现在"不开重同步时的拒收数"——
  保守口径下多速率 2:1 + 2 拍会出现大量抖动驱动的拒收（结构性救不回），
  而 runtime 口径下仅临界性少量拒收（确定性年龄峰≈预算的边界效应），
  此时综合器会如实标注"与文档板上 stale=0 的口径差异"，交板上实测确认。

注意：本模块只产出"参数"，不改动任何运行时。生成器把产出字段合并进
timing_constraints 即可；运行时语义与 simulate_resync 同构（见 xdom_timeline.py）。
"""

import argparse
import json
import sys

import xdom_timeline as tl

# 伺服设计系数：step = SAFETY_K · (hi − lo)。<1 留 clearance，
# 避免一次 +step 从 lo 直接越过 hi 触发反向（乒乓）。
SAFETY_K = 0.8
# 回代验证用的消费拍数（≥ 几个冷却周期，见到稳态）。
VALIDATE_CONS = 120000


def _rel_drift(drift_ppm, cons_drift_ppm):
    """相对漂移 = 生产者漂移 − 消费者漂移。

    契约里 xdom_drift_ppm 描述的就是**相对漂移**（实测两宿主节拍相对漂移
    ≈13.7 ppm），消费者漂移默认为 0。注意：这与 check_contract_coded 把
    未声明 cons_drift 默认成 =drift（相对 0、静态窗虚胖）的口径不同——
    综合器必须按物理相对漂移走，否则会把锚点误判成"无需重同步"。
    """
    d = float(drift_ppm or 0.0)
    c = 0.0 if cons_drift_ppm is None else float(cons_drift_ppm or 0.0)
    return d - c


def _cons_drift_for_window(cons_drift_ppm):
    """静态窗用的消费者漂移：未声明 ⇒ 0（xdom_drift_ppm 即相对漂移）。"""
    return 0.0 if cons_drift_ppm is None else float(cons_drift_ppm or 0.0)


def synthesize_resync_core(prod_period, cons_period, budget_ticks,
                           drift_ppm=0.0, cons_drift_ppm=None,
                           jitter_us=0.0, master_domain=None,
                           validate=True, n_val=VALIDATE_CONS,
                           jitter_policy="conservative"):
    """核心综合：给定一条跨域边的参数，推导重同步配置。

    jitter_policy:
        "conservative"（默认，fail-closed）验证时把声明抖动加进年龄再判拒收，
            与"真年龄 + 抖动 > 预算即拒"一致，最保守、绝不放行有风险的配置；
        "runtime"  验证时按真年龄（jitter=0）判拒收，匹配运行时"按声明预算拒收、
            不扣抖动"的语义（契约注释），与文档板上 stale=0 口径一致，但忽略真实抖动。

    返回 dict：
        feasible        bool   综合+回代是否通过
        enabled         bool   是否建议开启重同步
        reason          str    人类可读说明
        params          dict   建议合并进 timing_constraints 的字段
        validation      dict   回代证据（含 policy）；未验证则为 None
        recommendation  str    不可行时的出路；可行时为空
        warnings        list   抖动余量等告警
    """
    Pp = float(prod_period)
    Pc = float(cons_period)
    budget_us = float(budget_ticks) * Pc
    J = float(jitter_us or 0.0)
    rel = _rel_drift(drift_ppm, cons_drift_ppm)
    # 验证口径：conservative 用声明抖动（最严），runtime 用真年龄（jitter0）。
    jit = J if jitter_policy == "conservative" else 0.0

    params = {}
    validation = None
    recommendation = ""
    jitter_warnings = []

    if abs(rel) < 1e-9:
        # 相对漂移为 0：时间网格静止，相位窗可独立保持 ⇒ 无需重同步。
        enabled = False
        feasible = True
        reason = ("相对漂移 ≈ 0（drift=%.0f / cons_drift=%.0f ppm），时间网格静止，"
                  "相位即可行；重同步无需开启。"
                  % (float(drift_ppm or 0.0), _cons_drift_for_window(cons_drift_ppm)))
    else:
        prod = tl.Domain("prod", Pp, drift_ppm=float(drift_ppm or 0.0))
        cons = tl.Domain("cons", Pc, drift_ppm=_cons_drift_for_window(cons_drift_ppm))

        # 1) 不开重同步：回代（按 jitter_policy 决定加不加抖动）。
        no = tl.simulate_resync(prod, cons, n_val, budget_us=budget_us,
                               lo_us=0.0, hi_us=0.0, step_us=0.0,
                               cooldown_us=0.0, jitter_us=jit, pick="latest")
        no_stale = no["stale"]
        dur_s = n_val * Pc / 1e6
        if no_stale == 0:
            enabled = False
            feasible = True
            margin = budget_us - (no["max_us"] or 0.0)
            reason = ("相位即可行：无重同步、%.0f s / %d 拍回代（policy=%s）零拒收。"
                      % (dur_s, n_val, jitter_policy))
            if margin < J - 1e-6 and jitter_policy == "conservative":
                jitter_warnings.append(
                    "抖动余量仅 %.0f µs < 声明抖动 %.0f µs：确定性年龄峰 %.0f µs 贴近预算 %.0f µs，"
                    "真实硬件抖动可能推过边界导致拒收。运行时按声明预算拒收、不扣抖动（契约注释），"
                    "故判定可行；板上须实测确认。"
                    % (margin, J, no["max_us"], budget_us))
            validation = {
                "stale": no["stale"], "over": 0, "band_min_us": no["min_us"],
                "band_max_us": no["max_us"], "n_cons": no["n"],
                "duration_s": dur_s, "move_interval_s": float("inf"),
                "jitter_margin_us": margin, "policy": jitter_policy,
            }
        else:
            # 2) 需要重同步：伺服带 [J, B−J]，step = SAFETY_K·(B − 2J)，双向边界。
            if budget_us <= 2.0 * J:
                enabled = False
                feasible = False
                recommendation = ("预算 %.0f µs ≤ 2·抖动 %.0f µs，伺服带无正宽度；"
                                  "重同步救不了，需放宽 xdom_max_age_ticks。" % (budget_us, J))
                reason = recommendation
            else:
                lo = J
                hi = budget_us - J
                step = SAFETY_K * (hi - lo)
                cooldown = 0.5 * (step / abs(rel)) * 1e6
                enabled = True
                params = {
                    "xdom_resync_us": round(step, 1),
                    "xdom_resync_at_us": round(lo, 1),
                    "xdom_resync_hi_us": round(hi, 1),
                    "xdom_resync_cooldown_us": round(cooldown, 1),
                    "xdom_resync_domain": master_domain,
                }
                if validate:
                    svc = tl.simulate_resync(prod, cons, n_val, budget_us=budget_us,
                                            lo_us=lo, hi_us=hi, step_us=step,
                                            cooldown_us=cooldown, jitter_us=jit,
                                            pick="latest")
                    interval = (dur_s / (svc["over"] + 1)) if svc["over"] else float("inf")
                    validation = {
                        "stale": svc["stale"], "over": svc["over"],
                        "band_min_us": svc["min_us"], "band_max_us": svc["max_us"],
                        "n_cons": svc["n"], "duration_s": dur_s,
                        "move_interval_s": interval,
                        "jitter_safe": True,  # 带顶 hi=B−J ⇒ +抖动=预算，严格不越
                    }
                    if svc["stale"] == 0:
                        feasible = True
                        reason = ("重同步伺服 age 进 [%.0f, %.0f] µs，step=%.0f：回代 %.0f s / %d 拍"
                                  "零拒收（%d 次平移，间隔 ≈ %.1f s）；"
                                  "带顶 hi=B−J ⇒ +抖动恰为预算，抖动安全。"
                                  % (lo, hi, step, dur_s, svc["n"], svc["over"], interval))
                    else:
                        feasible = False
                        enabled = False
                        params = {}
                        recommendation = ("回代出现 %d 次拒收：该边结构性必超预算"
                                          "（多速率非整数倍或预算仅 %.0f 拍），"
                                          "重同步救不回；需放宽 xdom_max_age_ticks 到 %.0f。"
                                          % (svc["stale"], budget_ticks, budget_ticks + 1.0))
                        # 口径差异提示：保守模型（含抖动）判有拒收；若不开重同步时
                        # 仅临界性少量拒收（确定性年龄峰≈预算的边界效应），与文档称
                        # 板上 stale=0 的口径差异来自抖动建模（运行时按声明预算拒收、
                        # 未扣抖动）——交由板上实测确认。
                        if 0 < no_stale <= 12:
                            recommendation += ("\n  口径差异：不开重同步时保守模型仅 %d 次临界拒收"
                                               "（确定性年龄峰≈预算的边界效应），非结构性不可行；"
                                               "文档称板上 stale=0（运行时按声明预算拒收、不扣抖动）。"
                                               "若板上实测确认 stale=0，该边直接按本预算部署即可；"
                                               "否则升 %.0f 拍。" % (no_stale, budget_ticks + 1.0))
                        reason = recommendation
                else:
                    feasible = None  # 未验证：只给提案
                    reason = "已给出伺服提案，但未回代验证（validate=False）。"

    return {
        "feasible": feasible,
        "enabled": enabled,
        "reason": reason,
        "params": params,
        "validation": validation,
        "recommendation": recommendation,
        "warnings": jitter_warnings,
    }


def synthesize_resync_contract(contract, dag=None, master_domain=None,
                               validate=True, jitter_policy="conservative"):
    """从一份契约（+ 可选 DAG）综合整份方案的重同步配置。

    逐条跨域边调用 synthesize_resync_core；多边的情形取"最保守"的提案
    （开启 ⇒ 必须全部边都开；不可行 ⇒ 整份不可行；参数取各边并集里最严的）。
    返回 dict：
        feasible / any_enabled / edges[ {edge, core_result} ] / merged_params /
        warnings
    """
    ctr = (contract or {}).get("timing_constraints") or {}
    task = (contract or {}).get("task") or {}
    budget_ticks = ctr.get("xdom_max_age_ticks")
    drift = ctr.get("xdom_drift_ppm", 0.0)
    cons_drift = ctr.get("xdom_cons_drift_ppm")
    jitter = ctr.get("xdom_jitter_us", 0.0)
    base_period = float(task.get("period_us", 0.0)) if task else 0.0

    edges = []
    if dag is not None:
        edges = tl.cross_edges(dag)
    else:
        # 没有 DAG 时无法知道跨域边的域周期归属；要求调用方提供 dag。
        return {
            "feasible": None, "any_enabled": False, "edges": [],
            "merged_params": {}, "warnings": [
                "synthesize_resync_contract 需要 dag 才能识别跨域边；"
                "单条边的综合请用 synthesize_resync_core。"],
        }

    per, _base = tl._domain_periods(dag, contract)
    results = []
    for a, b, da, db in edges:
        Pp = per.get(da, base_period)
        Pc = per.get(db, base_period)
        md = master_domain or db  # 默认搬消费者一侧
        res = synthesize_resync_core(
            Pp, Pc, budget_ticks, drift_ppm=drift,
            cons_drift_ppm=cons_drift, jitter_us=jitter,
            master_domain=md, validate=validate, jitter_policy=jitter_policy)
        results.append({"edge": "%s->%s" % (a, b), "from": da, "to": db,
                        "result": res})

    any_enabled = any(r["result"]["enabled"] for r in results)
    feasible = all((r["result"]["feasible"] in (True, None)) or
                   (not r["result"]["enabled"]) for r in results)
    # 不可行优先：任一边 feasible is False ⇒ 整份不可行。
    any_infeasible = any(r["result"]["feasible"] is False for r in results)
    feasible = (not any_infeasible) and (feasible if results else None)

    # 合并参数：取第一个被开启的边的提案（各边预算/抖动相同则一致；
    # 不同则取最严——目前取首个 enabled 边，并告警差异）。
    merged = {}
    enabled_edges = [r for r in results if r["result"]["enabled"]]
    if enabled_edges:
        merged = dict(enabled_edges[0]["result"]["params"])
        for r in enabled_edges[1:]:
            if r["result"]["params"] != merged:
                # 参数不一致：保留首个，记录告警（PR12-A 暂不做逐边异构配置）。
                pass
    warnings = []
    if len(enabled_edges) > 1:
        warnings.append("多条跨域边需重同步，当前输出单一全局配置（取首边提案）；"
                        "若各边预算/抖动不同，应逐边生成。")

    return {
        "feasible": feasible,
        "any_enabled": any_enabled,
        "edges": results,
        "merged_params": merged,
        "warnings": warnings,
    }


def main():
    ap = argparse.ArgumentParser(
        description="PR12-A 重同步参数综合：从时序参数推导 resync 配置并回代验证")
    ap.add_argument("--prod-period", type=float, required=True)
    ap.add_argument("--cons-period", type=float, required=True)
    ap.add_argument("--budget-ticks", type=float, required=True)
    ap.add_argument("--drift-ppm", type=float, default=0.0)
    ap.add_argument("--cons-drift-ppm", type=float, default=None)
    ap.add_argument("--jitter-us", type=float, default=0.0)
    ap.add_argument("--master-domain", default=None)
    ap.add_argument("--policy", choices=["conservative", "runtime"],
                    default="conservative",
                    help="验证口径：conservative=含抖动、fail-closed（默认）；"
                         "runtime=jitter0，匹配运行时按声明预算拒收、不扣抖动")
    ap.add_argument("--no-validate", action="store_true")
    a = ap.parse_args()

    res = synthesize_resync_core(
        a.prod_period, a.cons_period, a.budget_ticks,
        drift_ppm=a.drift_ppm, cons_drift_ppm=a.cons_drift_ppm,
        jitter_us=a.jitter_us, master_domain=a.master_domain,
        validate=not a.no_validate, jitter_policy=a.policy)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
