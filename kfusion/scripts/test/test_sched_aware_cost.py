# -*- coding: utf-8 -*-
"""PR-14 验收：三列必须分得开，且没有「可调度」这一档。

最关键的一条（对应审查 P1）：**旧模型判 feasible、实测却是 infeasible** 的组合
必须能同时表达出来——如果测试只验「模型说可行」或只验「实测超期」，就验不到
那个脱节本身。
"""
from __future__ import print_function

import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))

import sched_aware_cost as sac  # noqa: E402


def cell(q99, misses, n=50000, lat99=12.0, resp_max=100.0, work99=None):
    c = {
        "rounds": 10, "n_cycles": n,
        "q99_resp_us": {"value": q99, "evidence": "test", "rounds": 10,
                        "n_cycles": n},
        "miss_rate": {"value": misses / float(n), "evidence": "test",
                      "n_cycles": n, "rounds": 10, "count": misses,
                      "ci95_wilson": [0.0, 7.683e-05 if misses == 0 else 1e-3]},
        "tail": {"start_lat_p99_median_us": lat99,
                 "start_lat_p50_median_us": 12.0,
                 "resp_max_worst_us": resp_max},
    }
    if work99 is not None:
        c["work_p99_us"] = {"value": work99, "evidence": "test", "rounds": 10}
    return c


def profile(cfs=None, fifo=None, members=None):
    members = members or [["stage_sample"], ["stage_pid"], ["stage_sink"]]
    sched = {}
    if cfs:
        sched["cfs"] = cfs
    if fifo:
        sched["fifo99"] = fifo
    return {
        "kind": "sched_profile", "version": 2,
        "deployments": {"user_only": {
            "maps_to_domain": "linux_userspace",
            "shape": {"n_groups": 3, "fused": False, "n_releases": 1,
                      "members": members},
            "sched": sched}},
        "measure_env": {"period_us": 1000},
        "boundary": {"t_ioctl_us": {"value": 2.0, "evidence": "test",
                                    "samples": 15000}},
    }


def cand(r_hat=78.0, n_groups=3, legal=True):
    order = ["stage_sample", "stage_pid", "stage_sink"]
    groups = [{"members": order}] if n_groups == 1 else \
             [{"members": [m]} for m in order[:n_groups]]
    return {"id": "A", "domain": "linux_userspace", "fused": n_groups == 1,
            "legal": legal,
            "plan": {"order": order, "groups": groups},
            "cost": {"predicted_R_us": r_hat, "compute_us": 15.0,
                     "release_us": 63.0, "handoff_us": 0.0,
                     "evidence": ["stage_sample@linux_userspace:scenario"]}}


class TestSchedAwareCost(unittest.TestCase):
    def test_模型可行但实测超期必须同时表达(self):
        # 旧模型只有 78 µs，远低于 1000；实测 33/50000 超期
        p = profile(cfs=cell(78.27, 33, resp_max=11786.0))
        r = sac.estimate(cand(r_hat=78.0), p, deadline_us=1000.0,
                         horizon_s=3600)
        self.assertTrue(r["model"]["feasible"])          # 模型说行
        cs = [m for m in r["measured"] if m["sched"] == "cfs"][0]
        self.assertEqual(cs["deadline_verdict"], sac.VERDICT_INFEASIBLE)
        self.assertEqual(r["deadline_verdict"], sac.VERDICT_INFEASIBLE)

    def test_零超期在长时长内不能背书_短时长才可行(self):
        p = profile(fifo=cell(15.0, 0))
        long_r = sac.estimate(cand(), p, deadline_us=1000.0, horizon_s=3600)
        fifo_l = [m for m in long_r["measured"] if m["sched"] == "fifo99"][0]
        self.assertEqual(fifo_l["deadline_verdict"], sac.VERDICT_CANNOT_CERTIFY)
        self.assertGreaterEqual(fifo_l["horizon"]["expected_misses_upper"], 1.0)

        short = sac.estimate(cand(), p, deadline_us=1000.0, horizon_s=5)
        fifo_s = [m for m in short["measured"] if m["sched"] == "fifo99"][0]
        self.assertEqual(fifo_s["deadline_verdict"], sac.VERDICT_FEASIBLE)
        self.assertLess(fifo_s["horizon"]["expected_misses_upper"], 1.0)

    def test_同一候选不同调度类结论不同(self):
        p = profile(cfs=cell(78.27, 33), fifo=cell(29.47, 0))
        r = sac.estimate(cand(), p, deadline_us=1000.0, horizon_s=3600)
        by = {m["sched"]: m["deadline_verdict"] for m in r["measured"]}
        self.assertEqual(by["cfs"], sac.VERDICT_INFEASIBLE)
        self.assertEqual(by["fifo99"], sac.VERDICT_CANNOT_CERTIFY)
        # 整行取最差：CFS 的超期不能被 FIFO 的平均掉
        self.assertEqual(r["deadline_verdict"], sac.VERDICT_INFEASIBLE)

    def test_形状未测只给证据不足(self):
        p = profile(fifo=cell(15.0, 0))
        r = sac.estimate(cand(n_groups=3), p, deadline_us=1000.0)  # 形状对
        self.assertNotEqual(r["deadline_verdict"], sac.VERDICT_NO_EVIDENCE)
        # 换个未测形状（成员顺序不同的两组）
        c2 = cand(n_groups=2)
        r2 = sac.estimate(c2, p, deadline_us=1000.0)
        self.assertEqual(r2["deadline_verdict"], sac.VERDICT_NO_EVIDENCE)
        self.assertIn("未测", " ".join(r2["missing"]))

    def test_无horizon不许给可行(self):
        p = profile(fifo=cell(15.0, 0))
        r = sac.estimate(cand(), p, deadline_us=1000.0, horizon_s=None)
        self.assertEqual(r["deadline_verdict"], sac.VERDICT_CANNOT_CERTIFY)

    def test_分解只在两项同源时给(self):
        p1 = profile(fifo=cell(15.0, 0, lat99=11.0, work99=4.0))
        r1 = sac.estimate(cand(), p1, deadline_us=1000.0)
        rec1 = [m for m in r1["measured"] if m["sched"] == "fifo99"][0]
        self.assertEqual(rec1["reconstruction"]["R_hat_us"], 15.0)
        # 没有 work p99 时不许拿 Q99 反推 Q99
        p2 = profile(fifo=cell(15.0, 0, lat99=11.0))
        r2 = sac.estimate(cand(), p2, deadline_us=1000.0)
        rec2 = [m for m in r2["measured"] if m["sched"] == "fifo99"][0]
        self.assertIsNone(rec2["reconstruction"]["R_hat_us"])

    def test_声明里没有可调度字样(self):
        self.assertNotIn("可调度性已", sac.CLAIM)
        self.assertIn("不宣称", sac.CLAIM)
        self.assertNotIn("WCET 是", sac.CLAIM)


if __name__ == "__main__":
    unittest.main(verbosity=2)
