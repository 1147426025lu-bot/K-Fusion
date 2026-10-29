# -*- coding: utf-8 -*-
"""PR-18C：shadow 不改 before；apply 才换；缺校准不得借用。"""
from __future__ import print_function

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(KF, "scripts", "ecos"))
sys.path.insert(0, os.path.join(KF, "scripts"))
sys.path.insert(0, HERE)

import calibrate_cost as cc  # noqa: E402
import feedback_loop as fl  # noqa: E402
import feedback_profile as fb  # noqa: E402

from test_deploy_objective import cand, profile  # noqa: E402


class FeedbackLoop(unittest.TestCase):
    def _table_flip(self, env, c_fast, c_slow):
        """让校准把慢候选的 Q99 变得更差，快候选保持，从而可能翻转。"""
        hashes = {}
        mk_fast = fb.match_key(c_fast, "fifo99", 0, env)
        mk_slow = fb.match_key(c_slow, "fifo99", 0, env)
        # 合成画像 fifo Q99：user 29.471 mixed 34.642 → 选 user
        # 校准把 user fifo 抬到 500，mixed 保持 34.642 → 选 mixed
        t = {}
        t[cc.key_id(mk_fast)] = cc.fit_one(
            (c_fast.get("cost") or {}).get("predicted_R_us") or 30.0,
            500.0, [500], [0], mk_fast)
        t[cc.key_id(mk_slow)] = cc.fit_one(
            (c_slow.get("cost") or {}).get("predicted_R_us") or 31.0,
            34.642, [34.642], [0], mk_slow)
        return t

    def test_shadow_does_not_apply(self):
        prof = profile()
        env = prof["env_evidence"]
        a = cand("A", "linux_userspace", 3, cost=30.0)
        m = cand("M", "mixed", 3, n_cross=1, cost=31.0)
        table = self._table_flip(env, a, m)
        rec, _ = fl.shadow_reselect(
            [a, m], prof, table, env, 0, {}, apply=False)
        self.assertEqual(rec["chosen_before"]["candidate_id"], "A")
        self.assertEqual(rec["chosen_before"]["sched"], "fifo99")
        self.assertEqual(rec["chosen_after"]["candidate_id"], "M")
        self.assertEqual(rec["apply_status"], "accepted")
        self.assertFalse(rec["applied"])

    def test_rejected_when_no_table(self):
        prof = profile()
        env = prof["env_evidence"]
        a = cand("A", "linux_userspace", 3)
        rec, after = fl.shadow_reselect(
            [a], prof, {}, env, 0, {}, apply=True, fallback=fl.M2_DROP)
        self.assertEqual(rec["apply_status"], "rejected")
        self.assertIsNone(rec["chosen_after"])
        self.assertFalse(rec["applied"])

    def test_m3_fallback_unchanged(self):
        prof = profile()
        env = prof["env_evidence"]
        a = cand("A", "linux_userspace", 3)
        rec, _ = fl.shadow_reselect(
            [a], prof, {}, env, 0, {}, apply=False, fallback=fl.M3_FALLBACK)
        self.assertEqual(rec["apply_status"], "unchanged")
        self.assertEqual(rec["chosen_after"]["candidate_id"], "A")


if __name__ == "__main__":
    unittest.main()
