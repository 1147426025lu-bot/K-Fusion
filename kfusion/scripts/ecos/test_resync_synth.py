"""PR12-A 重同步参数综合 —— 回归测试。

覆盖四条判定分支 + 契约级 DAG 集成：
  T1  1:1 + 1 拍 + 15 ppm + 抖动 200  → 需要重同步、回代零拒收
  T2  多速率 2:1 + 2 拍 + 15 ppm      → 相位即可行，无需重同步
  T3  多速率 2:1 + 1 拍 + 15 ppm      → 结构性必超预算，判不可行
  T4  漂移 0 + 1:1 + 1 拍             → 网格静止，无需重同步
  T5  契约级 DAG（锚点同构）           → 输出合并参数且可行
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resync_synth as rs
import xdom_timeline as tl


def check(name, cond, detail=""):
    if cond:
        print("ok %s%s" % (name, ("  " + detail) if detail else ""))
    else:
        print("FAIL %s%s" % (name, ("  " + detail) if detail else ""))
        raise SystemExit("PR12-A resync synth test failed at %s" % name)


# ---- T1：1:1 必需 + 回代零拒收 ----
r1 = rs.synthesize_resync_core(1000, 1000, 1, drift_ppm=15,
                               jitter_us=200, master_domain="nuttx")
check("T1.enabled", r1["enabled"] is True, r1["reason"])
check("T1.feasible", r1["feasible"] is True)
check("T1.stale0", r1["validation"]["stale"] == 0,
      "stale=%d over=%d band=%.0f..%.0f" % (
          r1["validation"]["stale"], r1["validation"]["over"],
          r1["validation"]["band_min_us"], r1["validation"]["band_max_us"]))
check("T1.lo", abs(r1["params"]["xdom_resync_at_us"] - 200.0) < 1e-6)
check("T1.hi", abs(r1["params"]["xdom_resync_hi_us"] - 800.0) < 1e-6)
check("T1.step", abs(r1["params"]["xdom_resync_us"] - 480.0) < 1e-6,
      "step=%.1f" % r1["params"]["xdom_resync_us"])
check("T1.master", r1["params"]["xdom_resync_domain"] == "nuttx")

# ---- T2：多速率 2:1 + 2 拍，保守口径（含抖动，fail-closed）判不可行 ----
# 注：文档称此组合板上 stale=0（运行时按声明预算拒收、不扣抖动），但保守模型
# 含抖动预测有拒收、且重同步救不回（结构性）→ 判不可行并建议升 3 拍或板上确认。
r2 = rs.synthesize_resync_core(2000, 1000, 2, drift_ppm=15,
                               jitter_us=200, master_domain="nuttx")
check("T2.enabled_false", r2["enabled"] is False, r2["reason"])
check("T2.feasible_false", r2["feasible"] is False, r2["reason"])
check("T2.recommend", "xdom_max_age_ticks" in r2["recommendation"],
      r2["recommendation"])

# ---- T2b：同组合走 runtime 口径（jitter0）时，不开重同步仅 2 次边界性临界拒收 ----
# （确定性年龄峰≈预算的边界效应），综合器严格以 stale==0 为可行仍判不可行，
# 但必须如实暴露"与文档板上 stale=0 的口径差异"（抖动建模口径）。
r2b = rs.synthesize_resync_core(2000, 1000, 2, drift_ppm=15,
                                jitter_us=200, master_domain="nuttx",
                                jitter_policy="runtime")
check("T2b.discrepancy_noted", "板上 stale=0" in r2b["recommendation"],
      r2b["recommendation"])
check("T2b.infeasible", r2b["feasible"] is False)

# ---- T3：多速率 2:1 + 1 拍，结构性不可行 ----
r3 = rs.synthesize_resync_core(2000, 1000, 1, drift_ppm=15,
                               jitter_us=200, master_domain="nuttx")
check("T3.feasible_false", r3["feasible"] is False, r3["reason"])
check("T3.recommend", "xdom_max_age_ticks" in r3["recommendation"],
      r3["recommendation"])

# ---- T4：漂移 0，网格静止 ----
r4 = rs.synthesize_resync_core(1000, 1000, 1, drift_ppm=0,
                               jitter_us=200, master_domain="nuttx")
check("T4.enabled_false", r4["enabled"] is False, r4["reason"])
check("T4.feasible", r4["feasible"] is True)

# ---- T5：契约级 DAG（锚点同构） ----
dag = {
    "nodes": [{"name": "p", "domain": "nuttx"},
              {"name": "c", "domain": "root"}],
    "edges": [{"from": "p", "to": "c"}],
}
contract = {
    "task": {"period_us": 1000},
    "timing_constraints": {
        "xdom_max_age_ticks": 1, "xdom_drift_ppm": 15, "xdom_jitter_us": 200,
    },
}
r5 = rs.synthesize_resync_contract(contract, dag, master_domain="nuttx")
check("T5.any_enabled", r5["any_enabled"] is True)
check("T5.feasible", r5["feasible"] is True)
check("T5.merged_lo", abs(r5["merged_params"]["xdom_resync_at_us"] - 200.0) < 1e-6)
check("T5.merged_step", abs(r5["merged_params"]["xdom_resync_us"] - 480.0) < 1e-6)

# ---- 独立回代：把合成参数直接喂 simulate_resync，确认零拒收（不依赖内部验证） ----
p5 = r5["merged_params"]
prod = tl.Domain("prod", 1000, drift_ppm=15.0)
cons = tl.Domain("cons", 1000, drift_ppm=0.0)
rb = tl.simulate_resync(prod, cons, 120000, budget_us=1000.0,
                        lo_us=p5["xdom_resync_at_us"], hi_us=p5["xdom_resync_hi_us"],
                        step_us=p5["xdom_resync_us"],
                        cooldown_us=p5["xdom_resync_cooldown_us"],
                        jitter_us=200.0, pick="latest")
check("T5.independent_stale0", rb["stale"] == 0,
      "stale=%d over=%d" % (rb["stale"], rb["over"]))

print("PR12-A RESYNC SYNTH TESTS PASSED")
