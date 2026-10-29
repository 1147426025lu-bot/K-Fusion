# -*- coding: utf-8 -*-
"""PR-17：UKU 多释放 plan 与单释放模型不得混用。"""
from __future__ import print_function

import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(KF, "scripts"))
sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))

spec = importlib.util.spec_from_file_location(
    "ecos_plan_joint", os.path.join(KF, "scripts", "ecos_plan_joint.py"))
stkp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stkp)
import uku_backend_caps as caps  # noqa: E402


def uku_plan(phases=(0.0, 200.0, 400.0)):
    groups = [
        {"id": "g0", "domain": "linux_userspace", "members": ["sample"],
         "release_phase_us": phases[0]},
        {"id": "g1", "domain": "linux_kernel", "members": ["pid"],
         "release_phase_us": phases[1]},
        {"id": "g2", "domain": "linux_userspace", "members": ["sink"],
         "release_phase_us": phases[2]},
    ]
    return {
        "meta": {"platform": "mixed", "xdom_adapter": "uku_ioctl",
                 "deployment_ready": True, "fusion": False},
        "groups": groups,
        "order": ["sample", "pid", "sink"],
    }


class UkuPlan(unittest.TestCase):
    def test_caps_uku_only(self):
        ok = caps.check_plan(uku_plan())
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["kind"], "uku_multi_release")
        four = uku_plan()
        four["groups"].append({
            "id": "g3", "domain": "linux_userspace", "members": ["extra"],
            "release_phase_us": 600.0})
        bad = caps.check_plan(four)
        self.assertFalse(bad["ok"])
        self.assertIn("E_UKU_PLAN_TOPOLOGY", bad["codes"])

    def test_same_phase_is_single_release(self):
        p = uku_plan((0.0, 0.0, 0.0))
        self.assertEqual(caps.uku_release_kind(p), "uku_single_release")
        sched = stkp.candidate_schedule(p, {"nodes": []}, None, 21, 5)
        self.assertEqual(sched["model"], "uku_single_release")
        lus = [r["L_us"] for r in sched["release"]]
        self.assertEqual(lus[0], 21.0)
        self.assertEqual(lus[1], 0.0)
        self.assertEqual(lus[2], 0.0)

    def test_multi_keeps_second_wakeup(self):
        p = uku_plan()
        sched = stkp.candidate_schedule(p, {"nodes": []}, None, 21, 5)
        self.assertEqual(sched["model"], "uku_multi_release")
        lus = [r["L_us"] for r in sched["release"]]
        self.assertEqual(lus, [21.0, 0.0, 21.0])
        end, ev = stkp.uku_events(p, dict(sched, exec_us={
            "sample": 1.0, "pid": 1.0, "sink": 1.0}), 4.0)
        # g0: max(0,0)+21+1=22; g1: max(200,22)+0+4+1=205; g2: max(400,205)+21+1=422
        self.assertAlmostEqual(end, 422.0)
        self.assertAlmostEqual(ev["sample"]["start_us"], 21.0)
        self.assertAlmostEqual(ev["sink"]["start_us"], 421.0)

    def test_driver_emits_plan_and_plan_user(self):
        cg_path = os.path.join(KF, "scripts", "mixed_uku_codegen__混合交接.py")
        spec_cg = importlib.util.spec_from_file_location("uku_cg", cg_path)
        cg = importlib.util.module_from_spec(spec_cg)
        spec_cg.loader.exec_module(cg)
        plan = uku_plan()
        drv = cg.emit_driver(
            plan, ["pid"], ["g_in"], ["g_out"],
            {"sample": "stage_sample()", "pid": "stage_pid()",
             "sink": "stage_sink()"},
            "stage_impl.c", "plc_probe.h", 1000, 8, ["g_out"])
        self.assertIn('strcmp(mode, "plan")', drv)
        self.assertIn('strcmp(mode, "plan_user")', drv)
        self.assertIn("gold|mixed|plan|plan_user", drv)
        self.assertIn("kRelPhase", drv)
        self.assertIn("200000LL", drv)
        self.assertIn("400000LL", drv)
        self.assertIn("TIMER_ABSTIME", drv)
        self.assertIn("releases_per_period", drv)
        self.assertIn("plan_phases_us", drv)
        self.assertIn("int multi = plan || plan_user;", drv)
        self.assertIn("kNGrp", drv)
        self.assertLess(drv.find("if (g_csv == 2)"), drv.find("else if (g_csv)"))
        self.assertIn("#define PLC_PROBE_BUCKETS 4096", drv)
        self.assertIn("#define PLC_PROBE_BUCKETS 4096", drv)

    def test_driver_nrel_is_unique_phases(self):
        cg_path = os.path.join(KF, "scripts", "mixed_uku_codegen__混合交接.py")
        spec_cg = importlib.util.spec_from_file_location("uku_cg2", cg_path)
        cg = importlib.util.module_from_spec(spec_cg)
        spec_cg.loader.exec_module(cg)
        plan = uku_plan((0.0, 0.0, 400.0))
        drv = cg.emit_driver(
            plan, ["pid"], ["g_in"], ["g_out"],
            {"sample": "stage_sample()", "pid": "stage_pid()",
             "sink": "stage_sink()"},
            "stage_impl.c", "plc_probe.h", 1000, 4, ["g_out"])
        self.assertIn("static const int kNRel = 2;", drv)
        self.assertIn("static const int kNGrp = 3;", drv)

    def test_parse_stats_rejects_bad_rc_contract(self):
        from importlib.util import spec_from_file_location, module_from_spec
        p = os.path.join(KF, "scripts", "pr11_shapes__形状补测.py")
        spec = spec_from_file_location("pr11s", p)
        sh = module_from_spec(spec)
        spec.loader.exec_module(sh)
        text = (
            "# mode: plan  releases_per_period=3  groups_per_period=3  cycles=8\n"
            "# plan_phases_us: 0 200 400\n"
            "# digest: abcd1234\n"
            "# uku_ioctl_error: 0/8\n"
            "# uku_seq_mismatch: 0/8\n"
        )
        st = sh.parse_stats(text)
        self.assertEqual(st["n_releases"], 3)
        self.assertEqual(st["n_groups"], 3)
        self.assertEqual(st["plan_phases_us"], [0.0, 200.0, 400.0])

    def test_codegen_reject_clears_ready(self):
        teir_nodes = []
        for i, n in enumerate(["a", "b", "c", "d"]):
            teir_nodes.append({
                "name": n, "phase_us": i * 10.0,
                "allowed_domains": ["linux_userspace", "linux_kernel"],
                "reads": ["g_in"] if n == "b" else [],
                "writes": ["g_out"] if n == "b" else [],
                "must_preserve": True, "may_delete": False,
                "cost_us_by_domain": {
                    "linux_userspace": {"value": 1.0, "evidence": "test"},
                    "linux_kernel": {"value": 1.0, "evidence": "test"},
                },
            })
        # 四组混合不是 U-K-U，接口过了也不得 ready。
        plan = {
            "meta": {"platform": "mixed", "xdom_adapter": "uku_ioctl",
                     "deployment_ready": True},
            "groups": [
                {"id": "g0", "domain": "linux_userspace", "members": ["a"],
                 "release_phase_us": 0},
                {"id": "g1", "domain": "linux_kernel", "members": ["b"],
                 "release_phase_us": 10},
                {"id": "g2", "domain": "linux_userspace", "members": ["c"],
                 "release_phase_us": 20},
                {"id": "g3", "domain": "linux_userspace", "members": ["d"],
                 "release_phase_us": 30},
            ],
        }
        chk = caps.check_plan(plan)
        self.assertFalse(chk["ok"])


if __name__ == "__main__":
    unittest.main()
