# -*- coding: utf-8 -*-
"""PR-18B：残差只按精确匹配键迁移；不覆盖 static/measurement。"""
from __future__ import print_function

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))
sys.path.insert(0, HERE)

import calibrate_cost as cc  # noqa: E402
import feedback_profile as fb  # noqa: E402
from test_feedback_profile import cand_mixed, prof_mixed  # noqa: E402


class CalibrateCost(unittest.TestCase):
    def test_fit_delta(self):
        mk = {"domain": "mixed", "sched": "fifo99", "load": 0,
              "n_releases": 3, "members": [["a"]], "phases_us": [0, 200, 400],
              "n_groups": 3, "fused": False, "platform": "Pi"}
        rec = cc.fit_one(426.0, 427.0, [426, 428], [0, 1], mk)
        self.assertEqual(rec["status"], "fitted")
        self.assertEqual(rec["delta_hat_us"], 1.0)
        self.assertEqual(rec["q99_cal_us"], 427.0)

    def test_no_cross_load_or_sched(self):
        mk0 = {"domain": "mixed", "sched": "fifo99", "load": 0,
               "n_releases": 3, "members": [["s"], ["p"], ["k"]],
               "phases_us": [0.0, 200.0, 400.0], "n_groups": 3,
               "fused": False, "platform": "Pi"}
        table = {cc.key_id(mk0): cc.fit_one(426.0, 427.0, [427], [0], mk0)}
        mk4 = dict(mk0, load=4)
        mk_cfs = dict(mk0, sched="cfs")
        mk_rel = dict(mk0, n_releases=1)
        self.assertEqual(cc.lookup(table, mk4)["status"], "insufficient_evidence")
        self.assertEqual(cc.lookup(table, mk_cfs)["status"], "insufficient_evidence")
        self.assertEqual(cc.lookup(table, mk_rel)["status"], "insufficient_evidence")
        self.assertEqual(cc.lookup(table, mk0)["delta_hat_us"], 1.0)

    def test_apply_does_not_touch_static(self):
        c = cand_mixed()
        stkp = {"candidates": [c]}
        rep = fb.build_report(stkp, {0: prof_mixed(427.0)}, {}, hashes={})
        train_rows = [r for r in rep["rows"] if r["match_key"]["sched"] == "fifo99"]
        table = cc.fit_table(train_rows, [0, 1, 2])
        out = cc.apply_to_report(rep, table)
        fifo = [r for r in out["rows"] if r["match_key"]["sched"] == "fifo99"][0]
        self.assertEqual(fifo["static_prediction"]["predicted_R_us"], 426.0)
        self.assertEqual(fifo["measurement"]["q99_resp_us"], 427.0)
        self.assertEqual(fifo["calibration"]["status"], "fitted")
        self.assertEqual(fifo["calibrated"]["q99_cal_us"], 427.0)
        self.assertEqual(fifo["calibrated"]["holdout_minus_cal_us"], 0.0)
        cfs = [r for r in out["rows"] if r["match_key"]["sched"] == "cfs"][0]
        self.assertEqual(cfs["calibration"]["status"], "insufficient_evidence")
        self.assertIsNone(cfs["calibrated"]["q99_cal_us"])

    def test_apply_q99_refuses_rhat_mismatch(self):
        calib = cc.fit_one(426.0, 427.0, [427], [0], {"load": 0, "sched": "fifo99"})
        self.assertIsNone(cc.apply_q99({"predicted_R_us": 100.0}, calib))
        self.assertEqual(cc.apply_q99({"predicted_R_us": 426.0}, calib), 427.0)


if __name__ == "__main__":
    unittest.main()
