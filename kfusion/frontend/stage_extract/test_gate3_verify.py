# -*- coding: utf-8 -*-
"""Gate 3：与规划器分离的验证（验生成物 + 独立事件解释器）单元测试。

- verify_generated：重新解析生成的周期体，独立核对覆盖/顺序/计时钩子/跨域桥。
- event_interpreter：从执行 trace 反推并校验 same_period / prev_period / 期限。
- emit 集成：真跑 ast_to_plan + emit，确认生成的 body 通过验生成物门禁；
  再手工篡改顺序，确认门禁能抓出背离。
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_IR = os.path.join(_REPO, "kfusion", "ir")
_SYNTH = os.path.join(_REPO, "kfusion", "backend", "synthesis")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


verify_generated = _load(os.path.join(_IR, "verify_generated.py"), "verify_generated_vg")
event_interpreter = _load(os.path.join(_IR, "event_interpreter.py"), "event_interpreter_vg")
ast_to_plan = _load(os.path.join(_HERE, "ast_to_plan.py"), "ast_to_plan_g3")
emit_mod = _load(os.path.join(_SYNTH, "emit.py"), "emit_g3")


_SAMPLE_BODY = """\
/* ecos_fused_body.c —— 由 ECoS 融合方案生成 */
#include "ecos_fused_body.h"
extern void ecos_stage_enter(unsigned int idx);
extern void ecos_stage_exit(unsigned int idx);
__attribute__((weak)) void ecos_stage_enter(unsigned int idx) { (void)idx; }
__attribute__((weak)) void ecos_stage_exit(unsigned int idx) { (void)idx; }

void ecos_body_g0(void)
{
\tecos_stage_enter(0);
\tstage_sample();\t/* stage_sample，相位 0 µs */
\tecos_stage_exit(0);
\tecos_stage_enter(1);
\tstage_pid();\t/* stage_pid，相位 10 µs */
\tecos_stage_exit(1);
\tecos_stage_enter(2);
\tstage_clamp();\t/* stage_clamp，相位 15 µs */
\tecos_stage_exit(2);
}
"""

_PLAN = {"groups": [
    {"id": "g0", "members": ["stage_sample", "stage_pid", "stage_clamp"],
     "domain": "linux_kernel", "exec_us": 18.0, "release_phase_us": 0.0},
]}


def _run(mod, argv):
    old = sys.argv
    sys.argv = argv
    try:
        return mod.main()
    finally:
        sys.argv = old


class VerifyGeneratedUnit(unittest.TestCase):
    def test_parse_body(self):
        p = verify_generated.parse_body(_SAMPLE_BODY)
        self.assertEqual([s["name"] for s in p["stages"]],
                         ["stage_sample", "stage_pid", "stage_clamp"])
        self.assertEqual([s["enter_idx"] for s in p["stages"]], [0, 1, 2])
        self.assertEqual([s["exit_idx"] for s in p["stages"]], [0, 1, 2])
        self.assertEqual(p["xdom_subs"], 0)
        self.assertEqual(p["xdom_pubs"], 0)

    def test_verify_ok(self):
        rep = verify_generated.verify(_SAMPLE_BODY, plan=_PLAN)
        self.assertTrue(rep["ok"], "正确生成体应通过：%s" % rep["errors"])

    def test_verify_missing_stage(self):
        body = _SAMPLE_BODY.replace("\tstage_clamp();\t/* stage_clamp，相位 15 µs */\n", "")
        rep = verify_generated.verify(body, plan=_PLAN)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("缺阶段" in e for e in rep["errors"]))

    def test_verify_wrong_order(self):
        swapped = _SAMPLE_BODY.replace(
            "\tstage_sample();\t/* stage_sample，相位 0 µs */",
            "\tstage_pid();\t/* stage_pid，相位 0 µs */").replace(
            "\tstage_pid();\t/* stage_pid，相位 10 µs */",
            "\tstage_sample();\t/* stage_sample，相位 10 µs */")
        rep = verify_generated.verify(swapped, plan=_PLAN)
        self.assertFalse(rep["ok"], "顺序被篡改必须被抓出")
        self.assertTrue(any("顺序" in e for e in rep["errors"]))

    def test_verify_missing_hook(self):
        # 把 stage_pid 的 enter/exit 删掉，模拟漏包裹
        broken = _SAMPLE_BODY.replace("\tecos_stage_enter(1);\n", "").replace(
            "\tecos_stage_exit(1);\n", "")
        rep = verify_generated.verify(broken, plan=_PLAN)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("enter/exit" in e or "钩子" in e for e in rep["errors"]))

    def test_verify_noncontiguous_indices(self):
        # 把 enter(2) 改成 enter(5)
        broken = _SAMPLE_BODY.replace("ecos_stage_enter(2)", "ecos_stage_enter(5)")
        rep = verify_generated.verify(broken, plan=_PLAN)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("连续" in e for e in rep["errors"]))

    def test_verify_xdom_bridge_mismatch(self):
        # 声明 1 处订阅但生成体没有 → 必须报桥被静默丢弃
        rep = verify_generated.verify(_SAMPLE_BODY, plan=_PLAN,
                                      expected_xdom_subs=1, expected_xdom_pubs=0)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("订阅" in e for e in rep["errors"]))

    def test_verify_xdom_bridge_ok(self):
        body = (_SAMPLE_BODY
                .replace("\tstage_sample();\t/* stage_sample，相位 0 µs */",
                         "\tecos_xdom_sub(0, &g_output);\n"
                         "\tstage_sample();\t/* stage_sample，相位 0 µs */")
                .replace("\tstage_pid();\t/* stage_pid，相位 10 µs */",
                         "\tstage_pid();\t/* stage_pid，相位 10 µs */\n"
                         "\tecos_xdom_pub(0, g_output);"))
        rep = verify_generated.verify(body, plan=_PLAN,
                                      expected_xdom_subs=1, expected_xdom_pubs=1)
        self.assertTrue(rep["ok"], "跨域桥数量吻合应通过：%s" % rep["errors"])

    def test_multi_group_global_hook_indices(self):
        """多组方案：钩子索引跨组累加（g0 用 0，g1 接着用 1），不是每组从 0 开始。

        回归 bug：曾按「块内位置」却拿全局 stages 下标配对，导致 g1 的阶段
        拿到 enter_idx=None 而残留在 [1]，误报「非连续 / 未包裹」，
        把合法的 LET 多作业产物拦下来（emit --target let 返回 3）。
        """
        body = (
            "void ecos_body_g0(void)\n{\n"
            "\tecos_stage_enter(0);\n"
            "\ta();\t/* a，相位 0 µs */\n"
            "\tecos_stage_exit(0);\n"
            "}\n"
            "void ecos_body_g1(void)\n{\n"
            "\tecos_stage_enter(1);\n"
            "\tb();\t/* b，相位 500 µs */\n"
            "\tecos_stage_exit(1);\n"
            "}\n")
        plan = {"groups": [{"id": "g0", "members": ["a"]},
                           {"id": "g1", "members": ["b"]}]}
        rep = verify_generated.verify(body, plan=plan)
        self.assertTrue(rep["ok"], "多组累加索引应通过：%s" % rep["errors"])
        self.assertEqual([s["enter_idx"] for s in rep["parsed"]["stages"]], [0, 1])
        # 反向：把 g1 的钩子改成 3（跳号）必须被抓出
        bad = body.replace("ecos_stage_enter(1)", "ecos_stage_enter(3)")
        rep2 = verify_generated.verify(bad, plan=plan)
        self.assertFalse(rep2["ok"])
        self.assertTrue(any("连续" in e for e in rep2["errors"]))


class EventInterpreterUnit(unittest.TestCase):
    def _teir(self, edges):
        return {"data_edges": edges}

    def test_same_period_ok(self):
        teir = self._teir([{"from": "A", "to": "B", "version": "same_period",
                            "lag_periods": 0, "xdom": False}])
        trace = [{"k": 0, "stages": {"A": (0.0, 10.0), "B": (12.0, 20.0)}}]
        rep = event_interpreter.check_trace(teir, trace, deadline_us=30.0)
        self.assertTrue(rep["ok"], rep["errors"])

    def test_same_period_violation(self):
        teir = self._teir([{"from": "A", "to": "B", "version": "same_period",
                            "lag_periods": 0, "xdom": False}])
        # A 退出(15) 晚于 B 进入(12) → 版本违例
        trace = [{"k": 0, "stages": {"A": (0.0, 15.0), "B": (12.0, 20.0)}}]
        rep = event_interpreter.check_trace(teir, trace, deadline_us=30.0)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("版本违例" in e for e in rep["errors"]))

    def test_prev_period_ok(self):
        # B 读的是 A(k-1)：k=1 时 B 进入(1012) 晚于 A(0) 退出(10)
        teir = self._teir([{"from": "A", "to": "B", "version": "prev_period",
                            "lag_periods": 1, "xdom": True}])
        trace = [
            {"k": 0, "stages": {"A": (0.0, 10.0)}},
            {"k": 1, "stages": {"B": (1012.0, 1020.0)}},
        ]
        rep = event_interpreter.check_trace(teir, trace, deadline_us=1030.0)
        self.assertTrue(rep["ok"], rep["errors"])

    def test_prev_period_violation(self):
        # 声称 prev_period lag1，但 B(k=1) 进入(5) 早于 A(k=0) 退出(10) → 不可能读到
        teir = self._teir([{"from": "A", "to": "B", "version": "prev_period",
                            "lag_periods": 1, "xdom": True}])
        trace = [
            {"k": 0, "stages": {"A": (0.0, 10.0)}},
            {"k": 1, "stages": {"B": (5.0, 12.0)}},
        ]
        rep = event_interpreter.check_trace(teir, trace, deadline_us=1030.0)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("版本违例" in e for e in rep["errors"]))

    def test_deadline_violation(self):
        teir = self._teir([{"from": "A", "to": "B", "version": "same_period",
                            "lag_periods": 0, "xdom": False}])
        trace = [{"k": 0, "stages": {"A": (0.0, 10.0), "B": (12.0, 900.0)}}]
        rep = event_interpreter.check_trace(teir, trace, deadline_us=100.0)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("期限违例" in e for e in rep["errors"]))


class EmitIntegration(unittest.TestCase):
    """真跑 ast_to_plan + emit，确认生成的 body 过验生成物门禁。"""

    _SRC = """\
int g_target;
int g_output;
int g_integral;
void stage_sample(void) { g_output += g_target; }
void stage_pid(void) { g_integral += g_output; g_output = g_integral; }
void stage_clamp(void) { if (g_output > 100) g_output = 100; }
void plc_cycle(void) { stage_sample(); stage_pid(); stage_clamp(); }
"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gate3_")
        self.extract_p = os.path.join(self.d, "extract.json")
        self.src_p = os.path.join(self.d, "src.c")
        with open(self.extract_p, "w", encoding="utf-8") as f:
            json.dump({
                "tool": "stage_extract", "version": 2, "source": "src.c",
                "entry": "plc_cycle",
                "stages": [
                    {"name": "stage_sample", "fn_type": "void ()",
                     "reads": ["g_target", "g_output"], "writes": ["g_output"]},
                    {"name": "stage_pid", "fn_type": "void ()",
                     "reads": ["g_output", "g_integral"],
                     "writes": ["g_integral", "g_output"]},
                    {"name": "stage_clamp", "fn_type": "void ()",
                     "reads": ["g_output"], "writes": ["g_output"]},
                ],
                "globals": [
                    {"name": "g_target", "type": "i32"},
                    {"name": "g_output", "type": "i32"},
                    {"name": "g_integral", "type": "i32"},
                ],
                "ok": True,
            }, f)
        with open(self.src_p, "w", encoding="utf-8") as f:
            f.write(self._SRC)
        self.adir = os.path.join(self.d, "a")
        self.gdir = os.path.join(self.d, "g")

    def test_end_to_end_body_passes_verify(self):
        rc = _run(ast_to_plan, [
            "ast_to_plan.py", self.extract_p, "--period-us", "1000",
            "--exec-us", '{"stage_sample":10,"stage_pid":5,"stage_clamp":3}',
            "--task-name", "demo", "--out-dir", self.adir])
        self.assertEqual(rc, 0)
        rc2 = _run(emit_mod, [
            "emit.py", "--plan", os.path.join(self.adir, "plan.json"),
            "--dag", os.path.join(self.adir, "dag.json"),
            "--contract", os.path.join(self.adir, "contract.json"),
            "--source", self.src_p, "--out-dir", self.gdir,
            "--target", "linux-kernel", "--name", "demo"])
        self.assertEqual(rc2, 0, "emit 应通过验生成物门禁")
        body = open(os.path.join(self.gdir, "ecos_fused_body.c"),
                    encoding="utf-8").read()
        plan = json.load(open(os.path.join(self.adir, "plan.json"), encoding="utf-8"))
        rep = verify_generated.verify(body, plan=plan)
        self.assertTrue(rep["ok"], "生成的 body 应过独立验生成物：%s" % rep["errors"])
        self.assertEqual(rep["found"],
                         ["stage_sample", "stage_pid", "stage_clamp"])

    def test_tampered_body_caught(self):
        rc = _run(ast_to_plan, [
            "ast_to_plan.py", self.extract_p, "--period-us", "1000",
            "--exec-us", '{"stage_sample":10,"stage_pid":5,"stage_clamp":3}',
            "--task-name", "demo", "--out-dir", self.adir])
        self.assertEqual(rc, 0)
        body = open(os.path.join(self.gdir, "ecos_fused_body.c"),
                    encoding="utf-8").read() if os.path.exists(
            os.path.join(self.gdir, "ecos_fused_body.c")) else None
        # 先正常生成一次以得到参照 body
        _run(emit_mod, [
            "emit.py", "--plan", os.path.join(self.adir, "plan.json"),
            "--dag", os.path.join(self.adir, "dag.json"),
            "--contract", os.path.join(self.adir, "contract.json"),
            "--source", self.src_p, "--out-dir", self.gdir,
            "--target", "linux-kernel", "--name", "demo"])
        body = open(os.path.join(self.gdir, "ecos_fused_body.c"),
                    encoding="utf-8").read()
        plan = json.load(open(os.path.join(self.adir, "plan.json"), encoding="utf-8"))
        # 篡改：交换 stage_sample 与 stage_pid 两行的顺序
        tampered = body.replace(
            "\tstage_sample();\t/* stage_sample，相位 0 µs */", "@@S@@").replace(
            "\tstage_pid();\t/* stage_pid，相位 10 µs */",
            "\tstage_sample();\t/* stage_sample，相位 10 µs */").replace(
            "@@S@@", "\tstage_pid();\t/* stage_pid，相位 0 µs */")
        rep = verify_generated.verify(tampered, plan=plan)
        self.assertFalse(rep["ok"], "篡改顺序必须被验生成物门禁抓出")
        self.assertTrue(any("顺序" in e for e in rep["errors"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
