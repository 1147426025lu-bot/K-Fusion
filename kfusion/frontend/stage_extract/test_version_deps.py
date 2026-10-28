# -*- coding: utf-8 -*-
"""Gate 2 后期 + Gate 3 准备：数据版本依赖（跨域 / 多速率）建模与独立复核。

验证 version_deps 模块 + ast_to_plan 的版本标注：
  · 单域同周期 → same_period（回归，不得 over-claim）
  · 跨域边默认 → prev_period（lag≥1），绝不默认同拍可见（PlanVerifier 诚实边界）
  · 多速率生产者更慢 → prev_period，lag = R-1
  · 跨域但有相位证明且 R≤1 → same_period
  · over-claim（声称 same_period 但必要是 prev_period）→ verify 判错（fail-closed）
"""

import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_IR = os.path.join(_REPO, "kfusion", "ir")


def _load(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ast_to_plan = _load(os.path.join(_HERE, "ast_to_plan.py"), "ast_to_plan_vd")
version_deps = _load(os.path.join(_IR, "version_deps.py"), "version_deps_vd")
teir_mod = _load(os.path.join(_IR, "temporal_effect_ir.py"), "teir_vd")


def _build(extract, period_us=1000, exec_us=None, domain="linux_kernel",
           domain_period_us=None):
    return ast_to_plan.build_dag(
        extract, period_us, period_us, exec_us or {}, 10.0,
        None, domain, domain_period_us)


class VersionDepsUnit(unittest.TestCase):
    def test_compute_lag_same_domain_ordered(self):
        # 同域、相位 A≤B、同周期 → same_period (lag 0)
        lag, kind = version_deps.compute_lag(1000, 1000, False, 0.0, 10.0, False)
        self.assertEqual((lag, kind), (0, "same_domain_same_period"))

    def test_compute_lag_xdom_default_prev(self):
        # 跨域、无证明 → prev_period，lag = max(1, R-1) = 1
        lag, kind = version_deps.compute_lag(1000, 1000, True, 0.0, 10.0, False)
        self.assertEqual(lag, 1)
        self.assertEqual(kind, "xdom_default_prev_period")

    def test_compute_lag_xdom_proven_same(self):
        # 跨域、有相位证明且 R≤1 且相位有序 → same_period
        lag, kind = version_deps.compute_lag(1000, 1000, True, 0.0, 10.0, True)
        self.assertEqual((lag, kind), (0, "xdom_proven_same_period"))

    def test_compute_lag_multirate_slow_producer(self):
        # 同域多速率：生产者周期 2000、消费者 1000（R=2）→ prev_period lag 1
        lag, kind = version_deps.compute_lag(2000, 1000, False, 0.0, 10.0, False)
        self.assertEqual((lag, kind), (1, "same_domain_multirate_prev"))

    def test_compute_lag_multirate_fast_producer(self):
        # 同域多速率：生产者更快（R=0.5）→ 每消费者周期都跑 ⇒ same_period
        lag, kind = version_deps.compute_lag(500, 1000, False, 0.0, 10.0, False)
        self.assertEqual((lag, kind), (0, "same_domain_same_period"))


class AstToPlanVersionLabeling(unittest.TestCase):
    def _ctl(self, domains=None, period_us_map=None, stage_periods=None):
        stages = [
            {"name": "stage_sample", "fn_type": "void ()",
             "reads": ["g_target", "g_output"], "writes": ["g_output"]},
            {"name": "stage_pid", "fn_type": "void ()",
             "reads": ["g_output", "g_integral"], "writes": ["g_integral", "g_output"]},
            {"name": "stage_clamp", "fn_type": "void ()",
             "reads": ["g_output"], "writes": ["g_output"]},
        ]
        if domains:
            for s, d in zip(stages, domains):
                s["domain"] = d
        if stage_periods:
            for s, p in zip(stages, stage_periods):
                s["period_us"] = p
        extract = {
            "tool": "stage_extract", "version": 2, "source": "src.c",
            "entry": "plc_cycle", "stages": stages,
            "globals": [{"name": "g_target", "type": "i32"},
                        {"name": "g_output", "type": "i32"},
                        {"name": "g_integral", "type": "i32"}],
            "ok": True,
        }
        if period_us_map:
            extract["meta"] = {"domain_period_us": period_us_map}
        return extract

    def test_single_domain_all_same_period(self):
        (dag, ex, ph, data_edges, cross, rw) = _build(self._ctl())
        self.assertEqual(len(data_edges), 2)
        for e in data_edges:
            self.assertEqual(e["version"], "same_period")
            self.assertEqual(e["lag_periods"], 0)
            self.assertFalse(e["xdom"])
        # 独立复核：单域不应有 over-claim
        rep = version_deps.verify(dag)
        self.assertTrue(rep["ok"], "单域同周期不应有版本 over-claim：%s" % rep["errors"])

    def test_cross_domain_default_prev_period(self):
        # sample 在 linux_kernel，pid/clamp 在 nuttx → sample→pid 是跨域边
        (dag, ex, ph, data_edges, cross, rw) = _build(
            self._ctl(domains=["linux_kernel", "nuttx", "nuttx"]))
        xdom_edges = [e for e in data_edges if e["xdom"]]
        self.assertEqual(len(xdom_edges), 1)
        e = xdom_edges[0]
        self.assertEqual((e["from"], e["to"]), ("stage_sample", "stage_pid"))
        self.assertEqual(e["version"], "prev_period")
        self.assertEqual(e["lag_periods"], 1)
        rep = version_deps.verify(dag)
        self.assertTrue(rep["ok"], "跨域默认 prev_period、lag 充足，应过：%s" % rep["errors"])

    def test_multirate_2to1_xdom_prev_period(self):
        # pid 域周期 2000（慢），sample/clamp 域周期 1000 → sample→pid 跨域多速率
        extract = self._ctl(domains=["linux_kernel", "nuttx", "linux_kernel"],
                            period_us_map={"linux_kernel": 1000, "nuttx": 2000})
        (dag, ex, ph, data_edges, cross, rw) = _build(extract)
        e = [x for x in data_edges if x["xdom"]][0]
        self.assertEqual(e["version"], "prev_period")
        self.assertEqual(e["lag_periods"], 1)  # max(1, ceil(2)-1)=1
        rep = version_deps.verify(dag)
        self.assertTrue(rep["ok"])

    def test_overclaim_detected(self):
        # 手工构造：一条跨域边声称 same_period 但无相位证明 → verify 必须判错
        dag = {
            "nodes": [
                {"name": "a", "domain": "linux_kernel", "period_us": 1000,
                 "phase_us": 0.0},
                {"name": "b", "domain": "nuttx", "period_us": 1000,
                 "phase_us": 10.0},
            ],
            "edges": [
                {"from": "a", "to": "b", "kind": "data", "var": "g",
                 "version": "same_period", "lag_periods": 0, "xdom": True},
            ],
            "phase": {"offsets": {"a": 0.0, "b": 10.0}},
            "meta": {"domain_period_us": {"linux_kernel": 1000, "nuttx": 1000},
                     "task": {"period_us": 1000}},
        }
        rep = version_deps.verify(dag)
        self.assertFalse(rep["ok"], "跨域 same_period 无证明必须被复核判错")
        self.assertTrue(any("OVERCLAIM" in er for er in rep["errors"]))

    def test_proven_same_period_xdom_passes(self):
        # 跨域但有相位证明且 R≤1 → 允许 same_period，verify 通过
        dag = {
            "nodes": [
                {"name": "a", "domain": "linux_kernel", "period_us": 1000,
                 "phase_us": 0.0},
                {"name": "b", "domain": "nuttx", "period_us": 1000,
                 "phase_us": 10.0},
            ],
            "edges": [
                {"from": "a", "to": "b", "kind": "data", "var": "g",
                 "version": "same_period", "lag_periods": 0, "xdom": True,
                 "same_period_xdom_proof": True},
            ],
            "phase": {"offsets": {"a": 0.0, "b": 10.0}},
            "meta": {"domain_period_us": {"linux_kernel": 1000, "nuttx": 1000},
                     "task": {"period_us": 1000}},
        }
        rep = version_deps.verify(dag)
        self.assertTrue(rep["ok"], "带相位证明的跨域同拍应放行：%s" % rep["errors"])

    def test_teir_carries_version(self):
        (dag, ex, ph, data_edges, cross, rw) = _build(
            self._ctl(domains=["linux_kernel", "nuttx", "nuttx"]))
        order = ["stage_sample", "stage_pid", "stage_clamp"]
        teir = teir_mod.build_teir(self._ctl(domains=["linux_kernel", "nuttx", "nuttx"]),
                                  dag, ex, order, rw, data_edges, cross, True)
        xdom_teir = [e for e in teir["data_edges"] if e["xdom"]]
        self.assertEqual(len(xdom_teir), 1)
        self.assertEqual(xdom_teir[0]["version"], "prev_period")
        self.assertIn("version_model", teir["meta"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
