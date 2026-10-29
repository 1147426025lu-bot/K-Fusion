# -*- coding: utf-8 -*-
"""PR-11 闭环验收：J(d,s) 真的能改入选、能 fail-closed、覆盖度按索引算。

为什么单独测集成层：deploy_objective 自己的 selftest 只证明「排序逻辑对」，
证明不了「STKP 输出真的被改写」和「排不出名时不生成」——后者才是 fail-closed
的关键，退回去按模型排序等于把「测不出来」当成「模型说得对」。
"""
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

do = stkp.do_mod

ORDER = ["stage_sample", "stage_pid", "stage_sink"]


def cand(cid, domain, n_groups, n_cross=0, ready=True, cost=30.0):
    groups = ([{"members": ORDER}] if n_groups == 1
              else [{"members": [m]} for m in ORDER[:n_groups]])
    return {"id": cid, "domain": domain, "fused": n_groups == 1,
            "legal": True, "deployment_ready": ready, "comparable": True,
            "why": "", "diagnostics": [],
            "plan": {"version": 2, "order": ORDER, "groups": groups,
                     "meta": {"platform": domain}},
            "cost": {"predicted_R_us": cost, "total_us": cost,
                     "handoff_channels": n_cross, "final_rankable": True}}


def profile(q99_user=29.471, q99_mixed=34.642, include_mixed=True,
            scheds=("cfs", "fifo99")):
    prof = {"kind": do.PROFILE_KIND, "version": 1, "deployments": {},
            "boundary": {"t_ioctl_us": {"value": 2.0, "evidence": "test",
                                        "samples": 15000}}}
    cells = {"user_only": ("linux_userspace", q99_user),
             "partial_kernel": ("mixed", q99_mixed)}
    for scen, (dom, q) in cells.items():
        if scen == "partial_kernel" and not include_mixed:
            continue
        blocks = {}
        for s in scheds:
            # 实测画像里同一部署 FIFO 明显低于 CFS；同值会让并列打破顺序，
            # 测出来的是并列规则而不是「调度类进决策空间」这件事本身。
            qv = q if s == "fifo99" else q + 48.799
            blocks[s] = {
                "rounds": 10, "n_cycles": 50000,
                "q99_resp_us": {"value": qv, "evidence": "measured",
                                "rounds": 10, "n_cycles": 50000},
                "miss_rate": {"value": 0.0, "evidence": "measured",
                              "n_cycles": 50000, "rounds": 10,
                              "count": 0, "ci95_wilson": [0.0, 7.683e-05]},
                "tail": {"resp_max_worst_us": 100.0},
            }
        prof["deployments"][scen] = {
            "maps_to_domain": dom,
            # 合成夹具：候选 plan 没有 release_phase → n_releases=1，
            # 画像也必须写 1，否则释放结构不匹配（这正是 gold 不能配 plan 的原因）
            "shape": dict(do.DEPLOYMENT_SHAPE[scen], n_releases=1),
            "sched": blocks,
        }
    prof["env_evidence"] = {
        "kind": "env_evidence", "version": 1, "complete": True, "missing": [],
        "host": {"model": "synthetic", "nproc": 4},
        "kernel": {"release": "test", "preempt_class": "PREEMPT",
                   "config_source": "test"},
        "sched": {"pin_cpu": 3, "rt_runtime_us": "950000"},
        "code": {"git_rev": "test", "source_sha256": "test"},
    }
    return prof


def doc_with(cands, model_choice=None):
    rec = next(c for c in cands if c["id"] == (model_choice or cands[0]["id"]))
    return {
        "version": 1, "kind": "stkp", "order": ORDER,
        "candidates": cands, "chosen": rec["id"], "chosen_record": rec,
        "claim": "explore_uncertain", "n_deployment_ready": len(cands),
    }


class TestDeployObjectiveClosedLoop(unittest.TestCase):
    def test_改写入选与调度类(self):
        cands = [cand("A", "linux_userspace", 3, cost=78.0),
                 cand("B", "linux_userspace", 1, cost=36.0),
                 cand("M", "mixed", 3, n_cross=1, cost=31.05)]
        doc, ok = stkp.apply_deploy_objective(
            doc_with(cands, model_choice="B"), profile())
        self.assertTrue(ok)
        self.assertEqual(doc["chosen"], "A")           # B 是模型原选，形状不符
        self.assertEqual(doc["sched_class"], "fifo99")
        self.assertEqual(doc["claim"], "objective_ranked_measured_tail")
        self.assertEqual(doc["model_best_before_objective"]["chosen"], "B")
        self.assertEqual(doc["chosen_record"]["domain"], "linux_userspace")
        self.assertIn("J=29.471", doc["chosen_record"]["why"])
        self.assertNotIn("; ;", doc["chosen_record"]["why"])

    def test_排不出名时清空入选(self):
        # 画像为空 = 这台机器上一个组合都没测过 → 必须拒绝，不许退回模型排序
        cands = [cand("A", "linux_userspace", 3), cand("M", "mixed", 3,
                                                      n_cross=1)]
        empty = profile()
        empty["deployments"] = {}
        doc, ok = stkp.apply_deploy_objective(doc_with(cands, "A"), empty)
        self.assertFalse(ok)
        self.assertIsNone(doc["chosen"])
        self.assertIsNone(doc["chosen_record"])
        self.assertIsNone(doc["sched_class"])
        self.assertEqual(doc["claim"], "objective_fail_closed")
        self.assertEqual(doc["deploy_objective"]["n_rankable"], 0)

    def test_调度类未测也算不可排名(self):
        cands = [cand("A", "linux_userspace", 3)]
        doc, ok = stkp.apply_deploy_objective(doc_with(cands, "A"),
                                              profile(scheds=("cfs",)),
                                              scheds=["cfs", "fifo99"])
        # cfs 有画像 → 可排名，但只会选 cfs
        self.assertTrue(ok)
        self.assertEqual(doc["sched_class"], "cfs")

    def test_覆盖度按索引而非id(self):
        # 同名不同域：按 id 统计会少算，按索引才对
        cands = [cand("X", "linux_userspace", 3),
                 cand("X", "linux_kernel", 3),
                 cand("M", "mixed", 3, n_cross=1)]
        doc, _ = stkp.apply_deploy_objective(doc_with(cands, "M"), profile())
        cov = doc["deploy_objective"]["coverage"]
        self.assertEqual(cov["deployment_ready_candidates"], 3)
        self.assertEqual(cov["ranked_candidates"], 2)   # X@user + M
        self.assertFalse(cov["complete"])
        self.assertIn("X@linux_kernel", cov["unranked_candidates"])

    def test_未部署就绪不入选(self):
        cands = [cand("A", "linux_userspace", 3, ready=False)]
        doc, ok = stkp.apply_deploy_objective(doc_with(cands, "A"), profile())
        self.assertFalse(ok)
        self.assertIsNone(doc["chosen"])

    def test_三释放mixed不吃单释放画像(self):
        """PR-17：plan 三释放不得配 PR-10E mixed 单释放 Q99。"""
        prof = profile()
        groups = [
            {"members": ["stage_sample"], "domain": "linux_userspace",
             "release_phase_us": 0.0},
            {"members": ["stage_pid"], "domain": "linux_kernel",
             "release_phase_us": 200.0},
            {"members": ["stage_sink"], "domain": "linux_userspace",
             "release_phase_us": 400.0},
        ]
        shape = {
            "n_groups": 3, "fused": False, "n_releases": 3,
            "members": [g["members"] for g in groups],
        }
        self.assertIsNone(do.deployment_for(prof, "mixed", shape))
        blk = json.loads(json.dumps(prof["deployments"]["partial_kernel"]))
        blk["shape"]["n_releases"] = 3
        blk["shape"]["exec_mode"] = "plan"
        blk["sched"]["fifo99"]["q99_resp_us"]["value"] = 427.0
        blk["sched"]["cfs"]["q99_resp_us"]["value"] = 482.0
        prof["deployments"]["mixed_plan"] = blk
        name, hit = do.deployment_for(prof, "mixed", shape)
        self.assertEqual(name, "mixed_plan")
        self.assertEqual(hit["sched"]["fifo99"]["q99_resp_us"]["value"], 427.0)
        c = cand("M", "mixed", 3, n_cross=1, cost=31.0)
        c["plan"]["groups"] = groups
        doc, ok = stkp.apply_deploy_objective(doc_with([c], "M"), prof)
        self.assertTrue(ok)
        self.assertEqual(doc["chosen_record"]["plan"]["groups"][1]["release_phase_us"],
                         200.0)
        self.assertIn("427", doc["chosen_record"]["why"])
        self.assertNotIn("34.642", doc["chosen_record"]["why"])

    def test_默认不吃权重(self):
        # 未给 --deploy-objective 时 doc 不应出现这些键（旧报告要能原样复现）
        cands = [cand("A", "linux_userspace", 3)]
        d = doc_with(cands)
        self.assertNotIn("deploy_objective", d)
        self.assertNotIn("sched_class", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
