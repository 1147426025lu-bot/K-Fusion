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

    def test_parse_stage_log_uses_avg_not_p50(self):
        text = (
            "# stat_exec_stage_fir_us: min=0.2 avg=0.609 p50=0 p90=1 p99=1 "
            "p99.9=2 p99.99=39 max=39.0 over=0 neg=0 min_signed=0.2 n=2000\n"
        )
        p = stkp.stage_cost_mod.parse_user_stage_log(text, source_log="B.log")
        self.assertEqual(p["stages"]["fir"]["value"], 0.609)
        self.assertEqual(p["stages"]["fir"]["p50_us"], 0)
        self.assertEqual(p["stages"]["fir"]["evidence"], "measured")
        self.assertEqual(p["stages"]["fir"]["source_log"], "B.log")

    def test_pin_teir_from_source(self):
        sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))
        import pin_teir
        with open(os.path.join(
                KF, "examples", "ecos", "mixed_uku__无状态混合.c"),
                encoding="utf-8") as fh:
            src = fh.read()
        pins = pin_teir.pins_from_source(src)
        self.assertEqual(pins.get("stage_sample"), "linux_userspace")
        self.assertEqual(pins.get("stage_sink"), "linux_userspace")
        self.assertNotIn("stage_add", pins)

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
        """UK（无收回段）不是 UKU，混合仍不可部署。"""
        teir = chain_teir(
            ["sample", "fir"],
            kernelizable={"sample": False, "fir": True})
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=1, extra_kernel=0,
                         handoff_us=3.0)
        self.assertGreater(doc["pruned"]["domain"], 0)
        self.assertEqual(doc["chosen_record"]["domain"], "linux_userspace")
        self.assertTrue(doc["chosen_record"]["deployment_ready"])
        mixed = [c for c in (doc.get("candidates") or []) + (doc.get("model_only") or [])
                 if c.get("domain") == "mixed"]
        for r in mixed:
            self.assertFalse(r["deployment_ready"])
            self.assertNotEqual(
                (r.get("plan") or {}).get("meta", {}).get("xdom_adapter"),
                "uku_ioctl")

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

    def test_uku_mixed_is_deployable_when_cheaper(self):
        """采样/执行器留用户态，中间计算进内核：UKU ioctl 可部署。"""
        names = ["sample", "fir", "pid", "clamp", "plant"]
        teir = chain_teir(names, kernelizable={
            "sample": False, "fir": True, "pid": True,
            "clamp": True, "plant": False,
        })
        override = {
            "linux_userspace": {
                "sample": 1.0, "fir": 40.0, "pid": 40.0,
                "clamp": 40.0, "plant": 1.0,
            },
            "linux_kernel": {n: 1.0 for n in names},
        }
        for n in teir["nodes"]:
            if n["name"] in ("fir", "pid", "clamp"):
                n["reads"] = ["g_x"]
                n["writes"] = ["g_y"]
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                         handoff_us=1.0, override=override)
        rec = doc["chosen_record"]
        self.assertEqual(rec["domain"], "mixed")
        self.assertTrue(rec["deployment_ready"])
        self.assertEqual(rec["plan"]["meta"].get("xdom_adapter"), "uku_ioctl")
        groups = rec["plan"]["groups"]
        self.assertEqual(len(groups), 3)
        self.assertEqual([g["domain"] for g in groups],
                         ["linux_userspace", "linux_kernel", "linux_userspace"])
        cost = rec.get("cost") or {}
        self.assertEqual(cost.get("handoff_channels"), 1)
        self.assertAlmostEqual(cost.get("release_us"), 21.0)
        ioctl = cost.get("ioctl_model") or {}
        self.assertAlmostEqual(ioctl.get("T_ioctl_us"), 1.0)
        self.assertAlmostEqual(cost.get("sync_us"), 0.0)

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

    def test_cli_override_is_not_measured(self):
        names = ["s", "f", "p"]
        teir = chain_teir(names)
        bare = {
            "linux_userspace": {n: 1.0 for n in names},
            "linux_kernel": {n: 0.1 for n in names},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=10, override=bare)
        self.assertEqual(doc.get("claim"), "explore_uncertain")
        rec = doc["chosen_record"]
        self.assertFalse((rec.get("cost") or {}).get("final_rankable"))
        self.assertTrue((rec.get("cost") or {}).get("explore_rankable"))

    def test_forged_measured_downgraded(self):
        names = ["s", "f", "p"]
        teir = chain_teir(names)
        fake = {n: {"value": 0.1, "evidence": "measured"} for n in names}
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, override={
            "linux_userspace": fake, "linux_kernel": fake,
        })
        self.assertNotEqual(doc.get("claim"), "empirical_model_not_wcet")
        kinds = (doc["chosen_record"].get("cost") or {}).get("evidence") or []
        self.assertTrue(any(k.endswith("cli_override") for k in kinds))

    def test_measured_with_source_log_is_final(self):
        names = ["s", "f", "p"]
        teir = chain_teir(names)
        slot = {"value": 1.0, "evidence": "measured",
                "source_log": "/tmp/B.log", "statistic": "avg", "samples": 2000}
        kslot = dict(slot)
        kslot["value"] = 0.2
        ov = {
            "linux_userspace": {n: dict(slot) for n in names},
            "linux_kernel": {n: dict(kslot) for n in names},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=10, override=ov)
        self.assertEqual(doc.get("claim"), "empirical_model_not_wcet")
        self.assertTrue(doc["chosen_record"]["cost"]["final_rankable"])
        self.assertEqual(doc["chosen"], "D")

    def test_dag_window_uses_same_override_exec(self):
        """teir exec 使融合越窗；覆盖大 exec 后窗口必须用同一份数据放行 B。"""
        names = ["sample", "fir", "plant"]
        phases = [0.0, 10.0, 20.0]
        teir = chain_teir(names, exec_us=1.0)
        for n, ph in zip(teir["nodes"], phases):
            n["phase_us"] = ph
        dag = {
            "nodes": [{
                "name": n, "effects": ["state_read"], "contexts": ["thread"],
                "phase_us": ph, "fns": [n],
            } for n, ph in zip(names, phases)],
            "edges": [{"from": a, "to": b, "kind": "data"}
                      for a, b in zip(names, names[1:])],
            "topo_order": names, "topo_order_delayed": names,
            "phase": {"offsets": dict(zip(names, phases)), "period_us": 1000},
            "meta": {"task": {"period_us": 1000, "deadline_us": 1000}},
            "recurrences": [], "algebraic_loops": [], "candidates": [],
        }
        contract = {
            "task": {"period_us": 1000, "deadline_us": 1000},
            "timing_constraints": {
                "actuator": {"stage": "plant", "earliest_us": 50,
                             "latest_us": 1000},
            },
            "evidence": {"exec": "empirical"},
        }
        # teir exec=1：融合 inst(plant)≈23 早于 50，只能 A
        small = stkp.solve(teir, self.v, c_user=21, c_kernel=21,
                           dag=dag, contract=contract)
        self.assertEqual(small["chosen"], "A")
        ov = {
            "linux_userspace": {n: 25.0 for n in names},
            "linux_kernel": {n: 25.0 for n in names},
        }
        # 同一份覆盖让融合 inst(plant)≈71 进窗；若 DAG 仍用 teir exec 会继续拒 B
        big = stkp.solve(teir, self.v, c_user=21, c_kernel=21,
                         dag=dag, contract=contract, override=ov)
        self.assertEqual(big["chosen"], "B")
        sched = (big["chosen_record"].get("plan") or {}).get("schedule") or {}
        self.assertEqual(sched.get("exec_us", {}).get("plant"), 25.0)

    def _uku_plan(self, groups, teir=None):
        return {
            "groups": groups,
            "meta": {"platform": "mixed", "xdom_adapter": "uku_ioctl"},
        }

    def test_uku_rejects_two_kernel_groups(self):
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["fir"]},
            {"domain": "linux_kernel", "members": ["pid"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        self.assertIsNone(stkp.mixed_adapter_kind(groups))
        teir = chain_teir(["sample", "fir", "pid", "sink"])
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_GROUP_SHAPE", stkp.uku_mod.codes(chk))

    def test_uku_rejects_capacity(self):
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["fir"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        teir = chain_teir(["sample", "fir", "sink"])
        teir["nodes"][1]["reads"] = ["g_%d" % i for i in range(9)]
        teir["nodes"][1]["writes"] = ["g_out"]
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_CAPACITY", stkp.uku_mod.codes(chk))

    def test_uku_rejects_struct_and_pointer(self):
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["fir"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        teir = chain_teir(["sample", "fir", "sink"])
        teir["nodes"][1]["reads"] = ["g_st.field"]
        teir["nodes"][1]["writes"] = ["g_out"]
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_TYPE", stkp.uku_mod.codes(chk))
        teir["nodes"][1]["reads"] = ["g_ptr"]
        teir["region_types"] = {"g_ptr": "int32_t *", "g_out": "int32_t"}
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_TYPE", stkp.uku_mod.codes(chk))

    def test_uku_rejects_kernel_args(self):
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["pid"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        teir = chain_teir(["sample", "pid", "sink"])
        teir["nodes"][1]["reads"] = ["g_in"]
        teir["nodes"][1]["writes"] = ["g_out"]
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir,
                                 calls={"pid": "pid(1000)"})
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_ABI", stkp.uku_mod.codes(chk))

    def test_uku_does_not_drop_g_seq(self):
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["add"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        teir = chain_teir(["sample", "add", "sink"])
        teir["nodes"][1]["reads"] = ["g_in", "g_seq"]
        teir["nodes"][1]["writes"] = ["g_out"]
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertTrue(chk["ok"], chk)
        self.assertIn("g_seq", chk["interface"]["copy_in"])

    def test_uku_rejects_output_window(self):
        names = ["sample", "add", "sink"]
        teir = chain_teir(names)
        teir["nodes"][1]["reads"] = ["g_in"]
        teir["nodes"][1]["writes"] = ["g_out"]
        contract = {"timing_constraints": {
            "actuator": {"stage": "sink", "earliest_us": 400, "latest_us": 600}}}
        override = {
            "linux_userspace": {n: 1.0 for n in names},
            "linux_kernel": {n: 1.0 for n in names},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                         handoff_us=1.0, override=override, contract=contract)
        self.assertGreater(doc["pruned"]["temporal"], 0)
        rec = doc["chosen_record"]
        if rec is not None:
            self.assertNotEqual(rec.get("domain"), "mixed")

    def test_uku_ignores_hrtimer_tax_and_rejects_negative_cost(self):
        names = ["sample", "add", "sink"]
        teir = chain_teir(names, kernelizable={
            "sample": False, "add": True, "sink": False})
        teir["nodes"][1]["reads"] = ["g_in"]
        teir["nodes"][1]["writes"] = ["g_out"]
        override = {
            "linux_userspace": {"sample": 1.0, "add": 80.0, "sink": 1.0},
            "linux_kernel": {n: 1.0 for n in names},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5,
                         extra_kernel=1000.0, handoff_us=1.0, override=override)
        rec = doc["chosen_record"]
        self.assertEqual(rec["domain"], "mixed")
        self.assertAlmostEqual((rec.get("cost") or {}).get("sync_us"), 0.0)
        self.assertAlmostEqual((rec.get("cost") or {}).get("predicted_R_us"),
                               21.0 + 1.0 + 1.0 + 1.0 + 1.0)
        override["linux_kernel"]["add"] = -4.0
        doc2 = stkp.solve(teir, self.v, c_user=21, c_kernel=5,
                          extra_kernel=0, handoff_us=1.0, override=override)
        mixed = [c for c in (doc2.get("candidates") or [])
                 if c.get("domain") == "mixed"]
        for r in mixed:
            self.assertFalse((r.get("cost") or {}).get("comparable"))

    def test_uku_runtime_exclusive_and_fail_closed(self):
        cg_path = os.path.join(KF, "scripts", "mixed_uku_codegen__混合交接.py")
        spec_cg = importlib.util.spec_from_file_location("uku_cg", cg_path)
        cg = importlib.util.module_from_spec(spec_cg)
        spec_cg.loader.exec_module(cg)
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["add"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        plan = {"groups": groups}
        kmod = cg.emit_mod(
            ["add"], ["g_in"], ["g_out"], {"add": "stage_add()"}, "stage_impl.c")
        self.assertIn("uku_busy", kmod)
        self.assertIn("return -EBUSY", kmod)
        self.assertIn("rc = -EILSEQ", kmod)
        self.assertIn(".mode = 0600", kmod)
        self.assertIn(".release = uku_release", kmod)
        self.assertNotIn("0666", kmod)
        drv = cg.emit_driver(
            plan, ["add"], ["g_in"], ["g_out"],
            {"sample": "stage_sample()", "add": "stage_add()",
             "sink": "stage_sink()"},
            "stage_impl.c", "plc_probe.h", 1000, 8, ["g_in", "g_out"])
        self.assertIn("goto uku_fail", drv)
        self.assertIn("uku_fail_closed", drv)
        self.assertIn("ioctl_roundtrip", drv)
        self.assertIn("kernel_compute", drv)
        self.assertNotIn("uku_handoff", drv)

    def test_pipeline_does_not_die_on_fusion_plan(self):
        sh = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
        with open(sh, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("失败不阻断 STKP", text)
        self.assertNotIn('|| die "fusion_plan 失败"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
