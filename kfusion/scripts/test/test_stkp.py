# -*- coding: utf-8 -*-
"""STKP v1 验收：穷举空间、对照 A–F、缺测、混合不可部署。"""
from __future__ import print_function

import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(KF, "scripts"))

spec = importlib.util.spec_from_file_location(
    "ecos_plan_joint", os.path.join(KF, "scripts", "ecos_plan_joint.py"))
stkp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stkp)


def chain_teir(names, kernelizable=None, exec_us=1.0, period=1000.0):
    kernelizable = kernelizable or {n: True for n in names}
    nodes = []
    for i, n in enumerate(names):
        k = bool(kernelizable.get(n, True))
        allowed = (["linux_userspace", "linux_kernel"] if k
                   else ["linux_userspace"])
        blocked = [] if k else ["linux_kernel:mmio"]
        cbd = {
            "linux_userspace": {"value": float(exec_us), "evidence": "test"},
        }
        if k:
            cbd["linux_kernel"] = {"value": float(exec_us), "evidence": "test"}
        nodes.append({
            "name": n,
            "phase_us": float(i) * 10.0,
            "reads": [],
            "writes": [],
            "persistent": [],
            "must_preserve": True,
            "may_delete": False,
            "allowed_domains": allowed,
            "blocked_domains": blocked,
            "migration_reason": ("状态访问可分析" if k else "禁止进内核：mmio"),
            "cost_us_by_domain": cbd,
            "exec_us": float(exec_us),
        })
    edges = []
    for a, b in zip(names, names[1:]):
        edges.append({
            "from": a, "to": b, "kind": "data", "version": "same-job",
            "proven": True, "regions": ["@x"],
        })
    return {
        "version": 1, "kind": "teir",
        "task": {"name": "t", "period_us": period, "deadline_us": period},
        "nodes": nodes, "edges": edges, "events": [],
        "transform": {"closed": False, "alias_uncertain": False, "policy": "ok"},
    }


class StkpV1(unittest.TestCase):
    def setUp(self):
        self.v = stkp.load_verifier()

    def test_raw_space_five(self):
        self.assertEqual(stkp.raw_space_size(5), 162)
        self.assertEqual(stkp.raw_space_size(8), 4374)

    def test_equal_cost_picks_fused_userspace(self):
        teir = chain_teir(["s", "f", "p", "c", "plant"])
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=21, extra_kernel=0)
        self.assertEqual(doc["enumerated"], 162)
        self.assertEqual(doc["chosen"], "B")
        rec = doc["chosen_record"]
        self.assertEqual(rec["domain"], "linux_userspace")
        self.assertTrue(rec["deployment_ready"])
        self.assertEqual(len(rec["plan"]["groups"]), 1)

    def test_cheaper_kernel_picks_d(self):
        teir = chain_teir(["s", "f", "p"])
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=1, extra_kernel=0)
        self.assertEqual(doc["chosen"], "D")
        self.assertEqual(doc["chosen_record"]["domain"], "linux_kernel")

    def test_kernel_tax_keeps_userspace(self):
        teir = chain_teir(["s", "f", "p"])
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=21, extra_kernel=80)
        self.assertEqual(doc["chosen"], "B")

    def test_mmio_prunes_kernel_and_mixed_not_deployable(self):
        teir = chain_teir(
            ["sample", "fir", "pid"],
            kernelizable={"sample": False, "fir": True, "pid": True})
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=1, extra_kernel=0,
                         handoff_us=3.0)
        self.assertGreater(doc["pruned"]["domain"], 0)
        self.assertEqual(doc["chosen_record"]["domain"], "linux_userspace")
        self.assertTrue(doc["chosen_record"]["deployment_ready"])
        mixed = doc.get("model_only") or []
        self.assertTrue(any(not r["deployment_ready"] for r in mixed) or
                        doc["n_model_feasible"] >= doc["n_deployment_ready"])
        for r in mixed:
            self.assertFalse(r["deployment_ready"])
            self.assertEqual(r["domain"], "mixed")

    def test_missing_cost_not_zero_winner(self):
        teir = chain_teir(["a", "b"])
        for n in teir["nodes"]:
            n["cost_us_by_domain"]["linux_kernel"] = {
                "value": None, "evidence": "missing"}
            n.pop("exec_us", None)
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=1)
        rec = doc["chosen_record"]
        self.assertEqual(rec["domain"], "linux_userspace")
        kern = [c for c in doc["candidates"] if c["domain"] == "linux_kernel"]
        self.assertTrue(all(not (c.get("cost") or {}).get("final_rankable")
                            for c in kern))

    def test_partial_kernel_cheaper_in_model_not_deployed(self):
        """FIR+PID 进内核更便宜，但无 xdom → 仍部署全用户态。"""
        names = ["sample", "fir", "pid", "clamp", "plant"]
        teir = chain_teir(names, kernelizable={
            "sample": False, "fir": True, "pid": True,
            "clamp": True, "plant": False,
        })
        override = {
            "linux_userspace": {n: 10.0 for n in names},
            "linux_kernel": {n: 1.0 for n in names},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                         handoff_us=1.0, override=override)
        self.assertEqual(doc["chosen_record"]["domain"], "linux_userspace")
        bm = doc.get("best_model")
        self.assertIsNotNone(bm)
        if bm["domain"] == "mixed":
            self.assertFalse(bm["deployment_ready"])
            self.assertLess(bm["cost"]["total_us"],
                            doc["chosen_record"]["cost"]["total_us"])

    def test_deadline_prunes(self):
        teir = chain_teir(["a", "b"], exec_us=400.0)
        teir["task"]["deadline_us"] = 50.0
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=21)
        self.assertGreater(doc["pruned"]["temporal"], 0)
        self.assertIsNone(doc["chosen_record"])

    def test_empty_allowed_domains_fail_closed(self):
        teir = chain_teir(["a", "b"])
        for n in teir["nodes"]:
            n["allowed_domains"] = []
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=21)
        self.assertIsNone(doc["chosen_record"])
        self.assertGreater(doc["pruned"]["domain"], 0)

    def test_placeholder_cannot_claim_kernel_optimum(self):
        teir = chain_teir(["s", "f", "p"])
        for n in teir["nodes"]:
            for d, slot in (n.get("cost_us_by_domain") or {}).items():
                slot["evidence"] = "placeholder"
                slot["note"] = "不是分域实测"
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=1)
        self.assertEqual(doc["chosen_record"]["domain"], "linux_userspace")
        self.assertEqual(doc.get("claim"), "placeholder_no_optimum")
        self.assertNotEqual(doc["chosen"], "D")

    def test_output_window_rejects_full_fusion(self):
        names = ["sample", "fir", "plant"]
        teir = chain_teir(names, exec_us=5.0)
        for i, n in enumerate(teir["nodes"]):
            n["phase_us"] = [0.0, 200.0, 800.0][i]
        dag = {
            "nodes": [{
                "name": n, "effects": ["state_read"], "contexts": ["thread"],
                "phase_us": ph, "fns": [n],
            } for n, ph in zip(names, [0.0, 200.0, 800.0])],
            "edges": [{"from": a, "to": b, "kind": "data"}
                      for a, b in zip(names, names[1:])],
            "topo_order": names, "topo_order_delayed": names,
            "phase": {"offsets": dict(zip(names, [0.0, 200.0, 800.0])),
                      "period_us": 1000},
            "meta": {"task": {"period_us": 1000, "deadline_us": 1000}},
            "recurrences": [], "algebraic_loops": [], "candidates": [],
        }
        contract = {
            "task": {"period_us": 1000, "deadline_us": 1000},
            "timing_constraints": {
                "actuator": {"stage": "plant", "earliest_us": 650,
                             "latest_us": 850},
            },
            "evidence": {"exec": "empirical"},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=21,
                         dag=dag, contract=contract)
        self.assertGreater(doc["pruned"]["temporal"], 0)
        self.assertNotEqual(doc["chosen"], "B")
        self.assertNotEqual(doc["chosen"], "D")
        rec = doc["chosen_record"]
        self.assertIsNotNone(rec)
        self.assertEqual(rec["domain"], "linux_userspace")
        plant_group = [g for g in rec["plan"]["groups"] if "plant" in g["members"]][0]
        self.assertEqual(plant_group["members"][0], "plant")  # 未与 sample 全融合

    def test_pipeline_does_not_die_on_fusion_plan(self):
        sh = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
        with open(sh, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("失败不阻断 STKP", text)
        self.assertNotIn('|| die "fusion_plan 失败"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
