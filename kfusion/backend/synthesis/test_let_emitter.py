# -*- coding: utf-8 -*-
"""PR12-B 测试：LET 部署合成（let_emitter）+ emit.py --target let 端到端。

本机无 clang/gcc，所以"构建"这一步留给目标侧；这里的测试只验证：
  · 生成的 C 结构是确定、正确的（作业表 / 入口 / 周期相位换算）；
  · emit.py 在 --target let 下能端到端跑通（通过已验证方案门禁 + 写盘）。
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import fusion_ir          # noqa: E402
import let_emitter as let_em  # noqa: E402
import emit as emit_mod    # noqa: E402


def _make_ir():
    """构造一个 2 组 / 2 阶段、周期 1000µs、相位 0/500µs 的 IR。"""
    plan = {
        "version": 1,
        "verification": {"ok": True},
        "groups": [
            {"id": "g0", "members": ["a"]},
            {"id": "g1", "members": ["b"]},
        ],
        "cost_model": {},
    }
    dag = {
        "nodes": [
            {"name": "a", "domain": "linux", "phase_us": 0.0},
            {"name": "b", "domain": "linux", "phase_us": 500.0},
        ],
        "topo_order": ["a", "b"],
        "phase": {"known": True, "offsets": {"a": 0.0, "b": 500.0},
                  "period_us": 1000.0},
        "meta": {"task": {"period_us": 1000.0, "name": "demo"}},
    }
    return fusion_ir.FusionIR.from_plan(plan, dag, source_name="demo")


class LetEmitterUnit(unittest.TestCase):
    def setUp(self):
        self.ir = _make_ir()

    def test_job_count_and_symbols(self):
        out = let_em.emit(self.ir, "demo", let_frac=0.8, skip_missed=True, loops=0)
        self.assertEqual(set(out), {
            "ecos_let_jobs.h", "ecos_let_jobs.c",
            "ecos_let_main.c", "README.let.txt"})
        c = out["ecos_let_jobs.c"]
        # 两个作业函数，分别对应 ecos_body_g0 / ecos_body_g1
        self.assertIn("ecos_let_fn_ecos_body_g0(void *ctx)", c)
        self.assertIn("ecos_let_fn_ecos_body_g1(void *ctx)", c)
        self.assertIn("ecos_body_g0();", c)
        self.assertIn("ecos_body_g1();", c)
        # 作业计数
        self.assertIn("demo_njobs", c)
        # 结构里应有两条作业表项
        self.assertEqual(c.count(".period_ns ="), 2)

    def test_timing_conversion_us_to_ns(self):
        c = let_em.emit(self.ir, "demo", let_frac=0.8,
                        skip_missed=True, loops=0)["ecos_let_jobs.c"]
        # 周期 1000µs → 1,000,000 ns
        self.assertIn(".period_ns = 1000000,", c)
        # 相位 0 与 500µs → 0 与 500,000 ns
        self.assertIn(".phase_ns = 0,", c)
        self.assertIn(".phase_ns = 500000,", c)
        # LET = 0.8 × 周期 = 800,000 ns
        self.assertIn(".let_ns = 800000,", c)

    def test_skip_missed_flag_default_on(self):
        c = let_em.emit(self.ir, "demo", skip_missed=True)["ecos_let_jobs.c"]
        self.assertIn("PLC_LET_SKIP_MISSED", c)
        c2 = let_em.emit(self.ir, "demo", skip_missed=False)["ecos_let_jobs.c"]
        self.assertNotIn("PLC_LET_SKIP_MISSED", c2)
        self.assertIn(".flags = 0 },", c2)

    def test_let_frac_capped_at_period(self):
        c = let_em.emit(self.ir, "demo", let_frac=1.0)["ecos_let_jobs.c"]
        # let_frac=1.0 → 上限为周期 = 1,000,000 ns（单周期作业不能超过周期）
        self.assertIn(".let_ns = 1000000,", c)

    def test_let_frac_validation(self):
        with self.assertRaises(ValueError):
            let_em.emit(self.ir, "demo", let_frac=0.0)
        with self.assertRaises(ValueError):
            let_em.emit(self.ir, "demo", let_frac=1.5)

    def test_main_scaffold(self):
        main = let_em.emit(self.ir, "demo")["ecos_let_main.c"]
        self.assertIn("volatile int shutdown;", main)
        self.assertIn("plc_let_init(&rt, demo_jobs, demo_njobs,", main)
        self.assertIn("PLC_LET_MODE_STRICT", main)
        self.assertIn("plc_let_run(&rt);", main)
        self.assertIn("plc_let_print_summary(stdout, &rt, \"app\", 0);", main)
        self.assertIn("#include \"plc_let__LET.h\"", main)
        self.assertIn("#include \"ecos_let_jobs.h\"", main)

    def test_jobs_header(self):
        h = let_em.emit(self.ir, "demo")["ecos_let_jobs.h"]
        self.assertIn("#include \"plc_let__LET.h\"", h)
        self.assertIn("extern const struct plc_let_job demo_jobs[];", h)
        self.assertIn("extern const unsigned demo_njobs;", h)

    def test_nonzero_period_required(self):
        ir = self.ir
        ir.period_us = 0
        with self.assertRaises(ValueError):
            let_em.emit(ir, "demo")


class EmitLetIntegration(unittest.TestCase):
    """端到端：emit.py --target let 跑过已验证方案门禁并写盘。"""

    def _fixture(self, d):
        plan = {
            "version": 1,
            "verification": {"ok": True},
            "groups": [
                {"id": "g0", "members": ["a"]},
                {"id": "g1", "members": ["b"]},
            ],
            "cost_model": {},
        }
        dag = {
            "nodes": [
                {"name": "a", "domain": "linux", "phase_us": 0.0},
                {"name": "b", "domain": "linux", "phase_us": 500.0},
            ],
            "topo_order": ["a", "b"],
            "phase": {"known": True, "offsets": {"a": 0.0, "b": 500.0},
                      "period_us": 1000.0},
            "meta": {"task": {"period_us": 1000.0, "name": "demo"}},
        }
        source = (
            "void a(void) {}\n"
            "void b(void) {}\n"
        )
        p = os.path.join(d, "plan.json")
        g = os.path.join(d, "dag.json")
        s = os.path.join(d, "src.c")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(plan, f)
        with open(g, "w", encoding="utf-8") as f:
            json.dump(dag, f)
        with open(s, "w", encoding="utf-8") as f:
            f.write(source)
        return p, g, s

    def test_emit_target_let_end_to_end(self):
        d = tempfile.mkdtemp(prefix="let_test_")
        out = os.path.join(d, "out")
        plan, dag, source = self._fixture(d)
        old = sys.argv
        sys.argv = ["emit.py", "--plan", plan, "--dag", dag, "--source", source,
                    "--out-dir", out, "--target", "let", "--name", "demo"]
        try:
            rc = emit_mod.main()
        finally:
            sys.argv = old
        self.assertEqual(rc, 0, "emit.py --target let 应返回 0")
        for fn in ("ecos_fused_body.c", "ecos_fused_body.h",
                   "ecos_let_jobs.c", "ecos_let_main.c", "README.let.txt"):
            self.assertTrue(os.path.exists(os.path.join(out, fn)),
                            "缺少产物 %s" % fn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
