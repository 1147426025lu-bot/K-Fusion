# -*- coding: utf-8 -*-
"""PR-17C：中位数口径、边界追溯、extra-shapes 合并。"""
from __future__ import print_function

import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))

spec = importlib.util.spec_from_file_location(
    "deploy_objective",
    os.path.join(KF, "scripts", "ecos", "deploy_objective.py"))
do = importlib.util.module_from_spec(spec)
spec.loader.exec_module(do)

spec15 = importlib.util.spec_from_file_location(
    "pr15_holdout",
    os.path.join(KF, "scripts", "pr15_holdout__训练验证分离.py"))
pr15 = importlib.util.module_from_spec(spec15)
spec15.loader.exec_module(pr15)


class Pr15Stats(unittest.TestCase):
    def test_median_even_matches_profile(self):
        self.assertEqual(do._median([412, 427, 427, 428]), 427.0)
        self.assertEqual(do._median([478, 479]), 478.5)

    def test_stats_on_uses_same_median(self):
        cells = {
            "mixed_plan": {
                "sched": {
                    "fifo99": [
                        {"repeat": 0, "n": 10, "valid": True,
                         "response": {"p99_us": 426, "max_us": 500},
                         "miss": 0},
                        {"repeat": 1, "n": 10, "valid": True,
                         "response": {"p99_us": 428, "max_us": 500},
                         "miss": 0},
                    ]
                }
            }
        }
        st = pr15.stats_on(cells, "mixed_plan", "fifo99", {0, 1})
        self.assertEqual(st["q99_us"], 427.0)

    def test_stats_on_skips_invalid(self):
        cells = {
            "x": {
                "sched": {
                    "cfs": [
                        {"repeat": 0, "n": 10, "valid": False,
                         "response": {"p99_us": 1, "max_us": 1}, "miss": 0},
                        {"repeat": 1, "n": 10, "valid": True,
                         "response": {"p99_us": 40, "max_us": 40}, "miss": 0},
                    ]
                }
            }
        }
        st = pr15.stats_on(cells, "x", "cfs", {0, 1})
        self.assertEqual(st["q99_us"], 40.0)
        self.assertEqual(st["rounds"], 1)

    def test_boundary_from_file_has_hash(self):
        path = os.path.join(KF, "docs", "pr10c_boundary", "boundary.json")
        blk = do.boundary_from_file(path)
        slot = blk["t_ioctl_us"]
        self.assertEqual(slot["value"], 2.0)
        self.assertEqual(slot["evidence"], "measured")
        self.assertEqual(len(slot["source_sha256"]), 64)
        self.assertTrue(os.path.isfile(slot["source_log"]))

    def test_extra_shapes_merge_and_clash(self):
        td = tempfile.mkdtemp()
        base = {
            "cells": [{
                "scenario": "fused_user", "status": "ok", "load": 0,
                "shape": {"n_groups": 1, "fused": True,
                          "domains": ["linux_userspace"]},
                "conditions": [],
            }]
        }
        extra = {
            "cells": [{
                "scenario": "mixed_plan", "status": "ok", "load": 0,
                "shape": {"n_groups": 3, "fused": False, "n_releases": 3,
                          "domains": ["linux_userspace", "linux_kernel",
                                      "linux_userspace"]},
                "conditions": [],
            }]
        }
        clash = {
            "cells": [{
                "scenario": "fused_user", "status": "ok", "load": 0,
                "shape": {"n_groups": 1, "fused": True,
                          "domains": ["linux_userspace"]},
                "conditions": [],
            }]
        }
        p_base = os.path.join(td, "base.json")
        p_ex = os.path.join(td, "ex.json")
        p_cl = os.path.join(td, "cl.json")
        p_bl = os.path.join(td, "bl.json")
        for p, obj in ((p_base, base), (p_ex, extra), (p_cl, clash)):
            with open(p, "w") as f:
                json.dump(obj, f)
        with open(p_bl, "w") as f:
            json.dump({"cells": []}, f)
        cells = pr15.collect_cells(p_base, p_bl, load=0, extra_shapes=[p_ex])
        self.assertIn("fused_user", cells)
        self.assertIn("mixed_plan", cells)
        self.assertEqual(cells["mixed_plan"]["shape"]["n_releases"], 3)
        with self.assertRaises(SystemExit):
            pr15.collect_cells(p_base, p_bl, load=0, extra_shapes=[p_cl])


if __name__ == "__main__":
    unittest.main()
