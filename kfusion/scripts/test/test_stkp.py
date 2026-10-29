# -*- coding: utf-8 -*-
"""STKP v1 验收：穷举空间、对照 A–F、缺测、混合不可部署。"""
from __future__ import print_function

import importlib.util
import json
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


def prove_uku_iface(teir, extra_types=None, session_init="zero"):
    """单测里钉 int32_t 与无参调用，模拟 Clang 已证明的接口。

    session_init：PR-9D 要求会话状态有显式初值证据；传 None 表示"没有证据"，
    用来测 E_UKU_STATE_INIT_UNKNOWN。默认 zero，对应示例里
    `int32_t g_integ;`（无初值 → C 语义零初始化）可被静态证明的情形。
    """
    types = dict(teir.get("region_types") or {})
    calls = dict(teir.get("calls") or {})
    for n in teir["nodes"]:
        calls.setdefault(n["name"], "%s()" % n["name"])
        n.setdefault("call", calls[n["name"]])
        for r in (n.get("reads") or []) + (n.get("writes") or []):
            x = r[1:] if str(r).startswith("@") else r
            if x and "." not in x and "[" not in x and "*" not in x:
                types.setdefault(x, "int32_t")
    for e in teir.get("edges") or []:
        for r in e.get("regions") or []:
            x = r[1:] if str(r).startswith("@") else r
            if x and "." not in x and "[" not in x and "*" not in x:
                types.setdefault(x, "int32_t")
    if extra_types:
        types.update(extra_types)
    teir["region_types"] = types
    teir["calls"] = calls
    if session_init is not None:
        meta = teir.setdefault("meta", {})
        m = dict(meta.get("session_init") or {})
        for n in teir["nodes"]:
            for p in n.get("persistent") or []:
                x = p[1:] if str(p).startswith("@") else p
                if x:
                    m.setdefault(x, session_init)
        meta["session_init"] = m
    return teir


class StkpV1(unittest.TestCase):
    def setUp(self):
        self.v = stkp.load_verifier()

    def test_parse_stage_log_uses_avg_not_p50(self):
        text = (
            "# stat_exec_stage_fir_us: min=0.2 avg=0.609 p50=0 p90=1 p99=1 "
            "p99.9=2 p99.99=39 max=39.0 over=0 neg=0 min_signed=0.2 n=2000\n"
            "# stat_response_us: min=9.5 avg=18.2 p50=13 p90=29 p99=67 "
            "p99.9=67 p99.99=67 max=67.9 over=0 neg=0 min_signed=9.5 n=80\n"
        )
        p = stkp.stage_cost_mod.parse_user_stage_log(text, source_log="B.log")
        self.assertEqual(p["stages"]["fir"]["value"], 0.609)
        self.assertEqual(p["stages"]["fir"]["p50_us"], 0)
        self.assertEqual(p["stages"]["fir"]["evidence"], "measured")
        self.assertEqual(p["stages"]["fir"]["source_log"], "B.log")
        self.assertAlmostEqual(p["response"]["avg_us"], 18.2)

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
        prove_uku_iface(teir)
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
        self.assertEqual((cost.get("schedule") or {}).get("model"),
                         "uku_multi_release")
        self.assertAlmostEqual(cost.get("release_us"), 42.0)
        ioctl = cost.get("ioctl_model") or {}
        self.assertAlmostEqual(ioctl.get("T_ioctl_us"), 1.0)
        self.assertAlmostEqual(cost.get("sync_us"), 0.0)
        self.assertEqual(ioctl.get("T_ioctl_evidence"), "cli_override")
        self.assertFalse(cost.get("final_rankable"))
        self.assertTrue(any(k.endswith("T_ioctl:cli_override") or k == "T_ioctl:cli_override"
                            for k in (cost.get("evidence") or [])))

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

    def test_uku_rejects_unknown_and_uint32_types(self):
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["fir"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        teir = chain_teir(["sample", "fir", "sink"])
        teir["nodes"][1]["reads"] = ["g_in"]
        teir["nodes"][1]["writes"] = ["g_out"]
        teir["calls"] = {n: "%s()" % n for n in ("sample", "fir", "sink")}
        for n in teir["nodes"]:
            n["call"] = "%s()" % n["name"]
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_TYPE_UNKNOWN", stkp.uku_mod.codes(chk))
        teir["region_types"] = {
            "g_in": "uint32_t", "g_out": "int32_t", "x": "int32_t"}
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_TYPE", stkp.uku_mod.codes(chk))
        self.assertNotIn("E_UKU_TYPE_UNKNOWN", stkp.uku_mod.codes(chk))

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
        prove_uku_iface(teir)
        teir["nodes"][1]["call"] = "pid(1000)"
        teir["calls"]["pid"] = "pid(1000)"
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_ABI", stkp.uku_mod.codes(chk))
        override = {
            "linux_userspace": {"sample": 1.0, "pid": 80.0, "sink": 1.0},
            "linux_kernel": {"pid": 1.0},
        }
        teir["nodes"][0]["allowed_domains"] = ["linux_userspace"]
        teir["nodes"][2]["allowed_domains"] = ["linux_userspace"]
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                         handoff_us=1.0, override=override)
        rec = doc["chosen_record"]
        self.assertIsNotNone(rec)
        self.assertNotEqual(rec.get("domain"), "mixed")

    def test_uku_rejects_persistent_kernel_state(self):
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["pid"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        teir = chain_teir(["sample", "pid", "sink"], kernelizable={
            "sample": False, "pid": True, "sink": False})
        teir["nodes"][1]["reads"] = ["g_in", "g_integ"]
        teir["nodes"][1]["writes"] = ["g_out", "g_integ"]
        teir["nodes"][1]["persistent"] = ["@g_integ"]
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_PERSISTENT", stkp.uku_mod.codes(chk))
        override = {
            "linux_userspace": {"sample": 1.0, "pid": 80.0, "sink": 1.0},
            "linux_kernel": {"pid": 1.0},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                         handoff_us=1.0, override=override)
        rec = doc["chosen_record"]
        self.assertIsNotNone(rec)
        self.assertNotEqual(rec.get("domain"), "mixed")

    def _session_groups(self):
        return [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["pid"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]

    def _session_teir(self):
        teir = chain_teir(["sample", "pid", "sink"], kernelizable={
            "sample": False, "pid": True, "sink": False})
        teir["nodes"][1]["reads"] = ["g_in", "g_integ"]
        teir["nodes"][1]["writes"] = ["g_out", "g_integ"]
        teir["nodes"][1]["persistent"] = ["@g_integ"]
        return teir

    def test_uku_rejects_userspace_reader_of_session(self):
        """PR-9D：会话状态被内核独占后，组外阶段再读它拿到的就是过期副本。

        会话变量会被摘出邮箱（不再 copy_out），此时用户态读到的永远是旧值，
        必须拒绝而不是静默接受。
        """
        teir = self._session_teir()
        teir["nodes"][2]["reads"] = ["g_integ"]      # sink 在用户态读会话状态
        prove_uku_iface(teir)
        chk = stkp.uku_mod.check(self._uku_plan(self._session_groups()), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_STATE_OWNER", stkp.uku_mod.codes(chk))

    def test_uku_rejects_userspace_writer_of_session(self):
        """PR-9D：组外阶段写会话状态，写回会被内核那份覆盖。"""
        teir = self._session_teir()
        teir["nodes"][2]["writes"] = ["g_integ"]
        prove_uku_iface(teir)
        chk = stkp.uku_mod.check(self._uku_plan(self._session_groups()), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_STATE_OWNER", stkp.uku_mod.codes(chk))

    def test_uku_rejects_session_without_init_evidence(self):
        """PR-9D：不得假定内核打开设备时把会话状态清零。"""
        teir = self._session_teir()
        prove_uku_iface(teir, session_init=None)
        chk = stkp.uku_mod.check(self._uku_plan(self._session_groups()), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_STATE_INIT_UNKNOWN", stkp.uku_mod.codes(chk))

    def test_uku_rejects_session_with_bad_init(self):
        teir = self._session_teir()
        prove_uku_iface(teir, session_init="garbage")
        chk = stkp.uku_mod.check(self._uku_plan(self._session_groups()), teir)
        self.assertFalse(chk["ok"])
        self.assertIn("E_UKU_STATE_INIT", stkp.uku_mod.codes(chk))

    def test_uku_pid_session_single_int32_is_deployable(self):
        names = ["sample", "pid", "sink"]
        teir = chain_teir(names, kernelizable={
            "sample": False, "pid": True, "sink": False})
        teir["nodes"][1]["reads"] = ["g_in", "g_integ"]
        teir["nodes"][1]["writes"] = ["g_out", "g_integ"]
        teir["nodes"][1]["persistent"] = ["@g_integ"]
        prove_uku_iface(teir)
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["pid"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertTrue(chk["ok"], chk.get("errors"))
        self.assertEqual((chk["interface"] or {}).get("session", {}).get("region"),
                         "g_integ")
        self.assertNotIn("g_integ", chk["interface"]["copy_in"])
        self.assertNotIn("g_integ", chk["interface"]["copy_out"])
        self.assertIn("g_in", chk["interface"]["copy_in"])
        self.assertIn("g_out", chk["interface"]["copy_out"])
        override = {
            "linux_userspace": {"sample": 1.0, "pid": 80.0, "sink": 1.0},
            "linux_kernel": {"pid": 1.0},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                         handoff_us=1.0, override=override)
        rec = doc["chosen_record"]
        self.assertEqual(rec.get("domain"), "mixed")
        self.assertEqual((rec.get("plan") or {}).get("meta", {}).get(
            "uku_session", {}).get("kind"), "integ_i32")
        teir2 = json.loads(json.dumps(teir))
        teir2["nodes"][1]["persistent"] = ["@g_integ", "@g_other"]
        prove_uku_iface(teir2, extra_types={"g_other": "int32_t"})
        chk2 = stkp.uku_mod.check(self._uku_plan(groups), teir2)
        self.assertFalse(chk2["ok"])
        self.assertIn("E_UKU_PERSISTENT", stkp.uku_mod.codes(chk2))
        cg_path = os.path.join(KF, "scripts", "mixed_uku_codegen__混合交接.py")
        spec_cg = importlib.util.spec_from_file_location("uku_cg_pid", cg_path)
        cg = importlib.util.module_from_spec(spec_cg)
        spec_cg.loader.exec_module(cg)
        plan = self._uku_plan(groups)
        plan["meta"]["uku_session"] = {"kind": "integ_i32", "region": "g_integ"}
        drv = cg.emit_driver(
            plan, ["pid"], ["g_in"], ["g_out"],
            {"sample": "stage_sample()", "pid": "stage_pid()",
             "sink": "stage_sink()"},
            "stage_impl.c", "plc_probe.h", 1000, 8,
            ["g_out", "g_seq", "g_integ"])
        self.assertNotIn("DIGEST_MIX(g_integ)", drv)
        self.assertIn("DIGEST_MIX(g_out)", drv)

    def test_uku_does_not_drop_g_seq(self):
        groups = [
            {"domain": "linux_userspace", "members": ["sample"]},
            {"domain": "linux_kernel", "members": ["add"]},
            {"domain": "linux_userspace", "members": ["sink"]},
        ]
        teir = chain_teir(["sample", "add", "sink"])
        teir["nodes"][1]["reads"] = ["g_in", "g_seq"]
        teir["nodes"][1]["writes"] = ["g_out"]
        prove_uku_iface(teir)
        chk = stkp.uku_mod.check(self._uku_plan(groups), teir)
        self.assertTrue(chk["ok"], chk)
        self.assertIn("g_seq", chk["interface"]["copy_in"])

    def test_uku_rejects_output_window(self):
        names = ["sample", "add", "sink"]
        teir = chain_teir(names)
        teir["nodes"][1]["reads"] = ["g_in"]
        teir["nodes"][1]["writes"] = ["g_out"]
        prove_uku_iface(teir)
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
        prove_uku_iface(teir)
        override = {
            "linux_userspace": {"sample": 1.0, "add": 80.0, "sink": 1.0},
            "linux_kernel": {n: 1.0 for n in names},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5,
                         extra_kernel=1000.0, handoff_us=1.0, override=override)
        rec = doc["chosen_record"]
        self.assertEqual(rec["domain"], "mixed")
        self.assertAlmostEqual((rec.get("cost") or {}).get("sync_us"), 0.0)
        self.assertEqual((rec.get("cost") or {}).get("schedule", {}).get("model"),
                         "uku_multi_release")
        # φ = 0/10/20；末组再付一次 L_U。不是单释放 Ĉ=L_U+ΣC+T_ioctl。
        self.assertAlmostEqual((rec.get("cost") or {}).get("predicted_R_us"),
                               46.0)
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
        self.assertIn("start_lat", drv)
        self.assertIn("exec_stage_", drv)
        self.assertNotIn("uku_handoff", drv)
        idx_copy = kmod.find("copy_to_user")
        idx_inc = kmod.find("uku_expect++")
        self.assertGreater(idx_copy, 0)
        self.assertGreater(idx_inc, idx_copy)
        self.assertIn("loop_reset();", kmod.split("uku_release")[1][:400])
        self.assertLess(kmod.find("copy_to_user"), kmod.find("uku_expect++"))
        self.assertIn("loop_reset();", kmod)

    def test_parse_ioctl_roundtrip_is_not_kernel_compute(self):
        text = (
            "# stat_kernel_compute_us: min=0.148 avg=0.222 p50=0 p90=0 p99=0 "
            "p99.9=1 p99.99=1 max=1.111 over=0 neg=0 min_signed=0.148 n=200\n"
            "# stat_ioctl_roundtrip_us: min=2.796 avg=4.312 p50=3 p90=6 p99=11 "
            "p99.9=24 p99.99=24 max=24.259 over=0 neg=0 min_signed=2.796 n=200\n"
        )
        p = stkp.stage_cost_mod.parse_user_stage_log(text, source_log="M.log")
        self.assertAlmostEqual(p["ioctl_roundtrip"]["value"], 4.312)
        self.assertAlmostEqual(p["kernel_compute"]["value"], 0.222)
        self.assertAlmostEqual(p["T_ioctl"]["value"], 4.312 - 0.222)
        self.assertEqual(p["T_ioctl"]["evidence"], "measured")
        self.assertEqual(p["T_ioctl"]["source_log"], "M.log")

    def test_uku_without_measured_ioctl_cannot_claim_final(self):
        names = ["sample", "add", "sink"]
        teir = chain_teir(names, kernelizable={
            "sample": False, "add": True, "sink": False})
        teir["nodes"][1]["reads"] = ["g_in"]
        teir["nodes"][1]["writes"] = ["g_out"]
        prove_uku_iface(teir)
        slot = {"value": 1.0, "evidence": "measured",
                "source_log": "/tmp/U.log", "statistic": "avg", "samples": 80}
        us = {n: dict(slot) for n in names}
        us["add"] = dict(slot)
        us["add"]["value"] = 80.0
        ov = {
            "linux_userspace": us,
            "linux_kernel": {"add": dict(slot)},
        }
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                         handoff_us=4.3, override=ov)
        mixed = [c for c in doc["candidates"] if c.get("domain") == "mixed"]
        self.assertTrue(mixed)
        self.assertFalse((mixed[0].get("cost") or {}).get("final_rankable"))
        self.assertEqual(doc["chosen_record"]["domain"], "linux_userspace")
        self.assertEqual(doc.get("claim"), "empirical_model_not_wcet")
        ioctl = {"value": 4.09, "evidence": "measured",
                 "source_log": "/tmp/M.log", "statistic": "avg", "samples": 200}
        doc2 = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                          handoff_us=0, override=ov, ioctl_cost=ioctl)
        rec2 = doc2["chosen_record"]
        self.assertEqual(rec2["domain"], "mixed")
        self.assertTrue((rec2.get("cost") or {}).get("final_rankable"))
        self.assertEqual(doc2.get("claim"), "empirical_model_not_wcet")
        self.assertEqual((rec2.get("cost") or {}).get("ioctl_model", {}).get(
            "T_ioctl_evidence"), "measured")

    def test_uku_sample_window_uses_start_not_finish(self):
        names = ["sample", "add", "sink"]
        teir = chain_teir(names, kernelizable={
            "sample": False, "add": True, "sink": False})
        teir["nodes"][1]["reads"] = ["g_in"]
        teir["nodes"][1]["writes"] = ["g_out"]
        prove_uku_iface(teir)
        override = {
            "linux_userspace": {"sample": 8.0, "add": 80.0, "sink": 1.0},
            "linux_kernel": {"add": 1.0},
        }
        # L=21, sample start=21 finish=29。窗口 [20,22] 只覆盖开始。
        contract = {"timing_constraints": {
            "sample": {"stage": "sample", "earliest_us": 20, "latest_us": 22}}}
        doc = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                         handoff_us=1.0, override=override, contract=contract)
        rec = doc["chosen_record"]
        self.assertEqual(rec["domain"], "mixed")
        ev = ((rec.get("cost") or {}).get("schedule") or {}).get("events_us") or {}
        self.assertAlmostEqual(ev["sample"]["start_us"], 21.0)
        self.assertAlmostEqual(ev["sample"]["finish_us"], 29.0)
        late = {"timing_constraints": {
            "sample": {"stage": "sample", "earliest_us": 0, "latest_us": 20}}}
        doc2 = stkp.solve(teir, self.v, c_user=21, c_kernel=5, extra_kernel=0,
                          handoff_us=1.0, override=override, contract=late)
        rec2 = doc2["chosen_record"]
        if rec2 is not None:
            self.assertNotEqual(rec2.get("domain"), "mixed")

    def test_pipeline_does_not_die_on_fusion_plan(self):
        sh = os.path.join(KF, "scripts", "ecos_from_c__从C编译.sh")
        with open(sh, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("失败不阻断 STKP", text)
        self.assertNotIn('|| die "fusion_plan 失败"', text)
        self.assertIn('LET_V+=(--contract "$TIME_CONTRACT")', text)
        self.assertIn("SKIP_LET_EMIT", text)
        self.assertIn("不生成 let_host（STKP plan 仍是部署源）", text)
        self.assertIn("contract_gate.py", text)
        self.assertIn("rec.get(\"deployable\") is False", text)

    def test_let_chain_window_rejects_full_fuse(self):
        spec = importlib.util.spec_from_file_location(
            "let_decide", os.path.join(KF, "scripts", "ecos", "let_decide.py"))
        let = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(let)

        class V(object):
            def verify(self, teir, plan):
                return True, []

        names = ["s1", "s2", "s3", "s4"]
        teir = chain_teir(names, exec_us=0.6)
        for i, n in enumerate(teir["nodes"]):
            n["phase_us"] = float((i) * 200)
        con_path = os.path.join(
            KF, "contracts", "examples", "chain_window.contract.json")
        with open(con_path, encoding="utf-8") as f:
            contract = json.load(f)
        hydrated = let.hydrate_pins(contract, teir)
        self.assertEqual(hydrated["pins"]["s1"]["kind"], "input_snapshot")
        self.assertEqual(hydrated["pins"]["s3"]["kind"], "output_commit")
        self.assertTrue(hydrated["pins"]["s3"]["early_commit_forbidden"])
        doc = let.decide(teir, contract, V(), 21.0, 21.0, 0.0)
        by = {c["id"]: c for c in doc["candidates"]}
        self.assertFalse(by["full_fuse"]["legal"])
        self.assertNotEqual(doc["chosen"], "full_fuse")
        self.assertIn("s3", doc["contract"]["pinned"])
        codes = [d["code"] for d in by["full_fuse"]["diagnostics"]]
        self.assertIn("E_PIN_EARLY_COMMIT", codes)
        self.assertFalse(by["full_fuse_let"]["deployable"])
        spec_g = importlib.util.spec_from_file_location(
            "contract_gate", os.path.join(KF, "scripts", "ecos", "contract_gate.py"))
        gate = importlib.util.module_from_spec(spec_g)
        spec_g.loader.exec_module(gate)
        fused = by["full_fuse"]["plan"]
        split = {"groups": [
            {"id": "g0", "members": ["s1", "s2"], "domain": "linux_userspace"},
            {"id": "g1", "members": ["s3", "s4"], "domain": "linux_userspace"},
        ]}
        self.assertFalse(gate.check(teir, fused, contract)["deploy_ok"])
        self.assertTrue(gate.check(teir, split, contract)["deploy_ok"])
        self.assertTrue(gate.check(teir, split, contract)["teir_ok"])

    def test_pr6_empty_board_is_not_end_to_end(self):
        board = {}
        board_pass = bool(
            board.get("digest_match") and board.get("seq_ok")
            and board.get("gold") and board.get("mixed"))
        self.assertFalse(board_pass)
        src = open(os.path.join(KF, "scripts", "paper", "pr5d_uku_inject.c"),
                   encoding="utf-8").read()
        self.assertIn('errno != EINVAL', src)
        self.assertIn("poison n_out want EINVAL", src)

    def test_pr7c_fold_and_holdout_gates(self):
        sc = stkp.stage_cost_mod
        slot = {"value": 0.4, "evidence": "measured", "source_log": "/tmp/M.log"}
        gmeas = sc.group_measurement(slot, ["add", "shift"], "linux_kernel")
        self.assertEqual(gmeas["members"], ["add", "shift"])
        self.assertFalse(gmeas["reusable_for_other_groups"])
        gold = {"stages": {
            "sample": {"value": 0.3, "evidence": "measured",
                       "source_log": "/tmp/G.log"},
            "add": {"value": 0.5, "evidence": "measured",
                    "source_log": "/tmp/G.log"},
            "sink": {"value": 0.2, "evidence": "measured",
                     "source_log": "/tmp/G.log"},
        }}
        mixed = {
            "kernel_compute": dict(slot),
            "T_ioctl": {"value": 3.3, "evidence": "measured",
                        "source_log": "/tmp/M.log"},
        }
        ov, ioctl = sc.override_from_gold_mixed(
            gold, mixed, ["sample", "add", "sink"], ["add"])
        hit = sc.lookup_group_cost(ov, ["add"], "linux_kernel")
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit["value"], 0.4)
        self.assertIsNone(sc.lookup_group_cost(ov, ["shift"], "linux_kernel"))
        self.assertEqual(ov["linux_kernel"]["sample"]["evidence"], "missing")
        spec = importlib.util.spec_from_file_location(
            "pr7c", os.path.join(
                KF, "scripts", "paper", "pr7c_cost_calib__成本标定.py"))
        pr7c = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pr7c)
        ok, why = pr7c.calib_inputs_ok(
            ov, ioctl, ["sample", "add", "sink"], ["add"])
        self.assertTrue(ok, why)
        fake = json.loads(json.dumps(ov))
        fake["linux_userspace"]["add"] = {"value": 80.0, "evidence": "measured"}
        ok2, why2 = pr7c.calib_inputs_ok(
            fake, ioctl, ["sample", "add", "sink"], ["add"])
        self.assertFalse(ok2)
        self.assertIn("user:add", why2)
        okh, wh = pr7c.holdout_independent(
            ("/tmp/G.log", "/tmp/M.log"), ("/tmp/G.log", "/tmp/M.log"))
        self.assertFalse(okh)
        self.assertEqual(wh, "same_log_as_calib")
        okh2, _ = pr7c.holdout_independent(
            ("/tmp/G.log", "/tmp/M.log"), ("/tmp/G2.log", "/tmp/M2.log"))
        self.assertTrue(okh2)

    def test_pr8_pid_gold_matches_c_formula(self):
        spec = importlib.util.spec_from_file_location(
            "pr8", os.path.join(
                KF, "scripts", "paper", "pr8_pid_session__积分会话.py"))
        pr8 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pr8)
        t = pr8.gold_traj(3)
        # seq=1 gin=(13&0x7ff)-256=-243; err=243; integ=243; out=486+30=516
        self.assertEqual(t[0]["g_in"], pr8.sample(1))
        self.assertEqual(t[0]["g_in"], (13 & 0x7ff) - 256)
        self.assertEqual(t[0]["integ"], 243)
        self.assertEqual(t[0]["g_out"], (243 << 1) + (243 >> 3))
        hdr = open(os.path.join(KF, "scripts", "paper", "pr8_pid_session.h"),
                   encoding="utf-8").read()
        self.assertIn("KFUSION_UKU_PID_MAGIC", hdr)
        self.assertIn("kfusion_pid_step", hdr)

    def test_group_kernel_cost_not_inherited_by_subset(self):
        names = ["sample", "add", "shift", "sink"]
        teir = chain_teir(names, kernelizable={
            "sample": False, "add": True, "shift": True, "sink": False})
        teir["nodes"][1]["reads"] = ["g_in"]
        teir["nodes"][1]["writes"] = ["g_mid"]
        teir["nodes"][2]["reads"] = ["g_mid"]
        teir["nodes"][2]["writes"] = ["g_out"]
        prove_uku_iface(teir)
        slot = {"value": 1.0, "evidence": "measured",
                "source_log": "/tmp/U.log", "statistic": "avg"}
        us = {n: dict(slot) for n in names}
        kcomp = {"value": 0.197, "evidence": "measured",
                 "source_log": "/tmp/M.log"}
        ov, _ioctl = stkp.stage_cost_mod.override_from_gold_mixed(
            {"stages": us}, {"kernel_compute": kcomp}, names, ["add", "shift"])
        ioctl = {"value": 3.3, "evidence": "measured",
                 "source_log": "/tmp/M.log"}
        plan_full = stkp.build_plan(
            names, [["sample"], ["add", "shift"], ["sink"]],
            ["linux_userspace", "linux_kernel", "linux_userspace"],
            nodes=stkp.node_map(teir))
        plan_full["meta"]["xdom_adapter"] = "uku_ioctl"
        plan_full["meta"]["deployment_ready"] = True
        sched = stkp.candidate_schedule(plan_full, teir, ov, 12.0, 12.0)
        self.assertTrue(sched["complete"])
        self.assertAlmostEqual(sched["exec_us"]["add"] + sched["exec_us"]["shift"],
                               0.197)
        plan_shift = stkp.build_plan(
            names, [["sample", "add"], ["shift"], ["sink"]],
            ["linux_userspace", "linux_kernel", "linux_userspace"],
            nodes=stkp.node_map(teir))
        sched2 = stkp.candidate_schedule(plan_shift, teir, ov, 12.0, 12.0)
        self.assertIn("shift@linux_kernel", sched2["missing"])
        self.assertFalse(sched2["complete"])
        self.assertNotIn("shift", sched2["exec_us"])
        doc = stkp.solve(teir, self.v, c_user=12, c_kernel=12, extra_kernel=0,
                         handoff_us=0, override=ov, ioctl_cost=ioctl)
        for c in doc.get("candidates") or []:
            groups = (c.get("plan") or {}).get("groups") or []
            kern = [g.get("members") for g in groups
                    if g.get("domain") == "linux_kernel"]
            if kern == [["shift"]]:
                self.assertFalse((c.get("cost") or {}).get("comparable"))
                self.assertNotEqual(
                    (c.get("cost") or {}).get("grade"), "empirical_model_not_wcet")


class PlanJointCostSeparation(unittest.TestCase):
    """PR-9E：组级实测成本 ↔ 组内阶段执行时间必须分开。

    组总额记在组尾、其余成员记 0，只用于把整组代价加进 Ĉ；
    这些 0 一旦流进相位窗口/顺序校验，就等于凭空证明「FIR 只要 0 µs」。
    """

    def _fixture(self):
        groups = [
            {"id": "g0", "domain": "linux_userspace",
             "members": ["fir", "pid"], "release_phase_us": 0.0},
            {"id": "g1", "domain": "linux_userspace",
             "members": ["sink"], "release_phase_us": 500.0},
        ]
        plan = {"version": 2, "groups": groups,
                "meta": {"platform": "linux_userspace"}}
        teir = chain_teir(["fir", "pid", "sink"])
        dag = {"nodes": [{"name": "fir", "phase_us": 0.0},
                         {"name": "pid", "phase_us": 8.0},
                         {"name": "sink", "phase_us": 500.0}],
               "edges": []}
        override = {"groups": [{"members": ["fir", "pid"],
                                "domain": "linux_userspace",
                                "value": 20.0, "evidence": "measured"}]}
        return plan, teir, dag, override

    def test_group_bound_members_are_flagged(self):
        plan, teir, dag, override = self._fixture()
        sched = stkp.candidate_schedule(plan, teir, override, 21.0, 5.0)
        self.assertEqual(sched["exec_us"]["pid"], 20.0)
        self.assertEqual(sched["exec_us"]["fir"], 0.0)
        self.assertIn("fir", sched["timing_bound"])
        self.assertNotIn("pid", sched["timing_bound"])

    def test_window_check_rejects_group_bound_substitution(self):
        plan, teir, dag, override = self._fixture()
        sched = stkp.candidate_schedule(plan, teir, override, 21.0, 5.0)
        contract = {"timing_constraints": {"sample": {"latest_us": 100.0}}}
        ok, errs = stkp.dag_temporal_ok(plan, dag, contract, None,
                                        21.0, 5.0, sched)
        self.assertFalse(ok, "组级成本不得当组内阶段时间通过窗口检查")
        self.assertIn("E_WINDOW_GROUP_BOUND",
                      [e.get("code") for e in (errs or [])])

    def test_no_window_keeps_cost_ranking(self):
        plan, teir, dag, override = self._fixture()
        sched = stkp.candidate_schedule(plan, teir, override, 21.0, 5.0)
        _ok, errs = stkp.dag_temporal_ok(plan, dag, None, None,
                                         21.0, 5.0, sched)
        self.assertNotIn("E_WINDOW_GROUP_BOUND",
                         [e.get("code") for e in (errs or [])])


if __name__ == "__main__":
    unittest.main(verbosity=2)
