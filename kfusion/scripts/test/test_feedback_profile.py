# -*- coding: utf-8 -*-
"""PR-18A：三层画像、匹配键、缺测 fail-closed、load 不得混。"""
from __future__ import print_function

import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))

import deploy_objective as do  # noqa: E402
import feedback_profile as fb  # noqa: E402


def cand_mixed():
    groups = [
        {"members": ["stage_sample"], "domain": "linux_userspace",
         "release_phase_us": 0.0},
        {"members": ["stage_pid"], "domain": "linux_kernel",
         "release_phase_us": 200.0},
        {"members": ["stage_sink"], "domain": "linux_userspace",
         "release_phase_us": 400.0},
    ]
    return {
        "id": "M", "domain": "mixed", "fused": False, "deployment_ready": True,
        "plan": {"groups": groups, "order": ["stage_sample", "stage_pid",
                                             "stage_sink"]},
        "cost": {"predicted_R_us": 426.0, "handoff_channels": 1,
                 "evidence": ["T_ioctl:cli_override"], "grade": "explore_uncertain"},
    }


def prof_mixed(q99=427.0, n_rel=3):
    return {
        "kind": do.PROFILE_KIND, "version": 2,
        "deployments": {
            "mixed_plan": {
                "maps_to_domain": "mixed",
                "shape": {"n_groups": 3, "fused": False, "n_releases": n_rel,
                          "members": [["stage_sample"], ["stage_pid"],
                                      ["stage_sink"]]},
                "sched": {
                    "fifo99": {
                        "rounds": 10, "n_cycles": 50000, "n_miss": 0,
                        "q99_resp_us": {"value": q99, "evidence": "measured",
                                        "rounds": 10, "n_cycles": 50000},
                        "miss_rate": {"value": 0.0, "evidence": "measured",
                                      "n_cycles": 50000},
                    }
                },
            }
        },
    }


class FeedbackProfile(unittest.TestCase):
    def test_layers_not_overwritten(self):
        c = cand_mixed()
        rec = {"match_key": fb.match_key(c, "fifo99", 0, {}, "s", "p")}
        static = fb.static_from_cand(c)
        meas = {"status": "measured", "q99_resp_us": 427.0}
        out = fb.attach_layers(rec, static, meas)
        static["predicted_R_us"] = 0
        meas["q99_resp_us"] = 0
        self.assertEqual(out["static_prediction"]["predicted_R_us"], 426.0)
        self.assertEqual(out["measurement"]["q99_resp_us"], 427.0)
        self.assertEqual(out["calibration"]["status"], "not_computed")
        out["calibration"] = {"status": "hack"}
        out2 = fb.attach_layers(rec, out["static_prediction"], out["measurement"])
        self.assertEqual(out2["calibration"]["status"], "not_computed")
        self.assertEqual(out2["static_prediction"]["predicted_R_us"], 426.0)

    def test_delta_mixed_fifo(self):
        c = cand_mixed()
        meas = fb.measurement_from_profile(prof_mixed(427.0), c, "fifo99")
        err = fb.error_from(fb.static_from_cand(c), meas)
        self.assertEqual(err["delta_q99_minus_Rhat_us"], 1.0)

    def test_missing_shape_is_insufficient(self):
        c = cand_mixed()
        meas = fb.measurement_from_profile(prof_mixed(427.0, n_rel=1), c, "fifo99")
        self.assertEqual(meas["status"], "insufficient_evidence")
        self.assertIsNone(fb.error_from(fb.static_from_cand(c), meas))

    def test_load_keys_differ(self):
        c = cand_mixed()
        env = {"host": {"model": "Pi"}, "kernel": {"release": "5.15",
                                                   "preempt_class": "PREEMPT"}}
        k0 = fb.match_key(c, "fifo99", 0, env, "s", "p")
        k4 = fb.match_key(c, "fifo99", 4, env, "s", "p")
        self.assertNotEqual(k0, k4)
        self.assertEqual(k0["n_releases"], 3)
        self.assertEqual(k0["phases_us"], [0.0, 200.0, 400.0])

    def test_build_report_splits_load(self):
        stkp = {"candidates": [cand_mixed()]}
        env = {}
        p0 = prof_mixed(427.0)
        p4 = copy.deepcopy(p0)
        p4["deployments"]["mixed_plan"]["sched"]["fifo99"]["q99_resp_us"]["value"] = 428.0
        # cfs 缺测
        rep = fb.build_report(stkp, {0: p0, 4: p4}, env, hashes={"src_sha256": "aa"})
        self.assertEqual(rep["n_rows"], 4)  # 2 load × 2 sched
        self.assertEqual(rep["n_measured"], 2)
        self.assertEqual(rep["n_insufficient"], 2)
        fifo = [r for r in rep["rows"] if r["match_key"]["sched"] == "fifo99"]
        d0 = [r["error"]["delta_q99_minus_Rhat_us"] for r in fifo if r["match_key"]["load"] == 0]
        d4 = [r["error"]["delta_q99_minus_Rhat_us"] for r in fifo if r["match_key"]["load"] == 4]
        self.assertEqual(d0, [1.0])
        self.assertEqual(d4, [2.0])
        for r in fifo:
            self.assertIsNone(r["calibration"].get("model"))


if __name__ == "__main__":
    unittest.main()
