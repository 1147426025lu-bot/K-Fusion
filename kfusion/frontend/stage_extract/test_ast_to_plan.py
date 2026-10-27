# -*- coding: utf-8 -*-
"""Gate 1 本机端到端测试：stage_extract JSON -> ast_to_plan -> emit.py -> 生成源码。

stage_extract 本体需要 clang（在 Pi 上验证）；这里用它的**真实输出格式**做 fixture，
验证「自动构造 dag/plan（经真实验证器）-> 生成用户态/内核态源码」这条链在本机成立。
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SYNTH = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "backend", "synthesis")
for _p in (_HERE, _SYNTH):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ast_to_plan           # noqa: E402
import emit as emit_mod      # noqa: E402


_EXTRACT = {
    "tool": "stage_extract", "version": 2, "source": "src.c",
    "entry": "plc_cycle",
    "stages": [
        {"name": "stage_sample", "fn_type": "void ()"},
        {"name": "stage_pid", "fn_type": "void ()"},
        {"name": "stage_clamp", "fn_type": "void ()"},
    ],
    "globals": [
        {"name": "g_target", "type": "i32"},
        {"name": "g_output", "type": "i32"},
        {"name": "g_integral", "type": "i32"},
    ],
    "ok": True,
}

_SRC = """\
int g_target;
int g_output;
int g_integral;
void stage_sample(void) { g_output += g_target; }
void stage_pid(void) { g_integral += g_output; g_output = g_integral; }
void stage_clamp(void) { if (g_output > 100) g_output = 100; }
void plc_cycle(void) { stage_sample(); stage_pid(); stage_clamp(); }
"""


def _run_main(mod, argv):
    old = sys.argv
    sys.argv = argv
    try:
        return mod.main()
    finally:
        sys.argv = old


def _read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _read_text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class Gate1Local(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gate1_")
        self.extract_p = os.path.join(self.d, "extract.json")
        self.src_p = os.path.join(self.d, "src.c")
        with open(self.extract_p, "w", encoding="utf-8") as f:
            json.dump(_EXTRACT, f)
        with open(self.src_p, "w", encoding="utf-8") as f:
            f.write(_SRC)
        self.out = os.path.join(self.d, "out")

    def test_ast_to_plan_builds_verified_plan(self):
        rc = _run_main(ast_to_plan, [
            "ast_to_plan.py", self.extract_p,
            "--period-us", "1000",
            "--exec-us", '{"stage_sample":10,"stage_pid":5,"stage_clamp":3}',
            "--task-name", "demo",
            "--out-dir", self.out,
        ])
        self.assertEqual(rc, 0, "ast_to_plan 应成功并经验证器签署")
        dag = _read_json(os.path.join(self.out, "dag.json"))
        self.assertEqual(dag["topo_order_delayed"],
                         ["stage_sample", "stage_pid", "stage_clamp"])
        # 相位：0 / 10 / 15（执行时间累加）
        self.assertEqual(dag["phase"]["offsets"]["stage_sample"], 0.0)
        self.assertEqual(dag["phase"]["offsets"]["stage_pid"], 10.0)
        self.assertEqual(dag["phase"]["offsets"]["stage_clamp"], 15.0)
        plan = _read_json(os.path.join(self.out, "plan.json"))
        self.assertTrue(plan["verification"]["ok"], "plan 必须带真实验证凭据")

    def test_full_chain_emits_source(self):
        # 1) ast_to_plan
        rc = _run_main(ast_to_plan, [
            "ast_to_plan.py", self.extract_p, "--period-us", "1000",
            "--exec-us", '{"stage_sample":10,"stage_pid":5,"stage_clamp":3}',
            "--task-name", "demo", "--out-dir", self.out,
        ])
        self.assertEqual(rc, 0)
        # 2) emit linux-kernel
        gen = os.path.join(self.d, "gen")
        rc2 = _run_main(emit_mod, [
            "emit.py",
            "--plan", os.path.join(self.out, "plan.json"),
            "--dag", os.path.join(self.out, "dag.json"),
            "--contract", os.path.join(self.out, "contract.json"),
            "--source", self.src_p,
            "--out-dir", gen, "--target", "linux-kernel", "--name", "demo",
        ])
        self.assertEqual(rc2, 0, "emit.py 应能从自动构造的 plan 生成源码")
        for fn in ("ecos_fused_body.c", "ecos_fused_body.h",
                   "ecos_fused_linux.c", "Kbuild"):
            self.assertTrue(os.path.exists(os.path.join(gen, fn)),
                            "缺少产物 %s" % fn)
        body = _read_text(os.path.join(gen, "ecos_fused_body.c"))
        # 生成的周期体必须按序调用三个阶段
        i1 = body.find("stage_sample()")
        i2 = body.find("stage_pid()")
        i3 = body.find("stage_clamp()")
        self.assertTrue(0 <= i1 < i2 < i3, "阶段调用顺序应为 sample->pid->clamp")

    def test_reject_when_no_entry(self):
        bad = dict(_EXTRACT, entry=None, ok=False, stages=[])
        p = os.path.join(self.d, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        with self.assertRaises(SystemExit):
            ast_to_plan.build_dag(bad, 1000, 1000, {}, 10.0)

    def test_reject_when_kc0_false(self):
        bad = dict(_EXTRACT, k_c0={
            "ok": False,
            "rejections": [{"function": "stage_pid", "call": "malloc",
                            "kind": "mem", "reason": "动态内存"}]})
        with self.assertRaises(SystemExit):
            ast_to_plan.build_dag(bad, 1000, 1000, {}, 10.0)

    def test_kc0_ok_proceeds(self):
        good = dict(_EXTRACT, k_c0={"ok": True, "rejections": []})
        dag, ex, ph = ast_to_plan.build_dag(good, 1000, 1000, {}, 10.0)
        self.assertEqual(len(dag["nodes"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
