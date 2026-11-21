# -*- coding: utf-8 -*-
"""D 组宿主：计算就绪门禁 + 硬中断锁类型的生成端回归测试。

背景（评审第 1、2 条）：
  · 旧实现用单个 bool `kf_let_ready`，任意组体跑完都置真，output_commit 只看这个布尔，
    无法区分「谁算的 / 哪一拍 / 算完多久」——会把上一拍遗留或别的组的结果当本拍结果发布。
  · `hrtimer_hard=1` 时整只定时器在硬中断上下文触发（含 group 释放槽），而队列锁是
    `spinlock_t`；PREEMPT_RT 上它是可睡眠的 rtmutex，在硬中断里取属于非法路径。

这些断言只检查生成的 C 文本，不需要 clang/kbuild，可以在无板上跑。
"""

import os
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import linux_kernel_single_worker as sw  # noqa: E402


def _shared():
    """render() 需要的占位符；与 linux_kernel_emitter.emit 的产出形状一致。"""
    return {
        "ngroups": 2,
        "nstages": 3,
        "first_group": 0,
        "last_group": 1,
        "period": 1000,
        "deadline": 1000,
        "rtprio": 80,
        "cpu": 3,
        "use_fifo": 1,
        "late_slack_ns": 50000,
        "evidence": "unit-test",
        "layout": "g0@0[prod] | g1@700[act]",
        "body_cases": "\tcase 0:\n\t\tbody0();\n\t\tbreak;\n"
                      "\tcase 1:\n\t\tbody1();\n\t\tbreak;",
        "digest_decls": "extern int32_t g_out;\n"
                        "static int32_t ecos_obs_0;\n"
                        "module_param_named(g_out, ecos_obs_0, int, 0444);",
        "digest_update": "\tecos_obs_0 = g_out;",
        "probe_includes": "",
        "probe_decls": "",
        "probe_before_sw": "",
        "probe_after_sw": "",
        "rsf_probe": "",
        "stage_profile": "",
        "xdom_init": "",
        "xdom_include": "",
        "xdom_report": "",
        "xdom_anchor": "",
        "xdom_shift_decls": "",
    }


def _ir(phases, let_events):
    groups = [types.SimpleNamespace(phase_us=ph) for ph in phases]
    return types.SimpleNamespace(period_us=1000, groups=groups,
                                 let_events=let_events)


class ReadyGateHostUnit(unittest.TestCase):
    def _render(self, phases, let_events):
        self.files = {}
        return sw.render(_ir(phases, let_events), "kf_task", _shared(),
                         out_files=self.files)

    def test_no_bare_bool_gate(self):
        """不得再出现单个 bool 就绪标记。"""
        c = self._render([0.0, 700.0], [{
            "kind": "output_commit", "stage": "prod", "at_us": 400.0,
            "compute_at_us": 0.0, "path": "irq", "on_timeout": "skip_commit",
            "vars": ["g_cmd", "g_cmd_ver"],
            "publish": [{"dst": "g_cmd", "src": "g_cmd_pending"},
                        {"dst": "g_cmd_ver", "op": "inc"}],
        }])
        self.assertNotIn("kf_let_ready", c)
        self.assertIn("struct kf_stage_mark", c)
        self.assertIn("KF_STAGE_STALE", c)

    def test_commit_uses_triple_pairing(self):
        """commit 必须按 (周期, 生产者组, 完成时刻) 取用，且带时效预算。"""
        c = self._render([0.0, 700.0], [{
            "kind": "output_commit", "stage": "prod", "at_us": 400.0,
            "compute_at_us": 0.0, "path": "irq", "on_timeout": "skip_commit",
            "vars": ["g_cmd", "g_cmd_ver"],
            "publish": [{"dst": "g_cmd", "src": "g_cmd_pending"},
                        {"dst": "g_cmd_ver", "op": "inc"}],
        }])
        self.assertIn("kf_let_gate_take(cycle_id, 0u, &age_ns)", c)
        self.assertIn("let_result_age_budget_ns", c)
        self.assertIn("#include \"kf_let_host.h\"", c)
        self.assertIn("#define KF_PROD_GID 0", self.files["kf_let_host.h"])

    def test_producer_derived_from_compute_at(self):
        """compute_at_us 指向哪一组，生产者就是哪一组。"""
        c = self._render([0.0, 400.0, 700.0], [{
            "kind": "output_commit", "stage": "prod", "at_us": 500.0,
            "compute_at_us": 400.0, "path": "irq", "on_timeout": "skip_commit",
            "vars": ["g_cmd"], "publish": [{"dst": "g_cmd", "src": "g_p"}],
        }])
        self.assertIn("kf_let_gate_take(cycle_id, 1u, &age_ns)", c)

    def test_producer_falls_back_to_earlier_group(self):
        """缺 compute_at_us 时退化为「相位早于 at_us 的最后一组」。"""
        c = self._render([0.0, 300.0, 700.0], [{
            "kind": "output_commit", "stage": "prod", "at_us": 500.0,
            "path": "irq", "on_timeout": "skip_commit",
            "vars": ["g_cmd"], "publish": [{"dst": "g_cmd", "src": "g_p"}],
        }])
        self.assertIn("kf_let_gate_take(cycle_id, 1u, &age_ns)", c)

    def test_producer_unknown_degrades_to_cycle_only(self):
        """反查不到生产者时用 KF_PROD_ANY（只按周期配对），不静默否定。"""
        c = self._render([700.0], [{
            "kind": "output_commit", "stage": "prod", "at_us": 100.0,
            "path": "irq", "on_timeout": "skip_commit",
            "vars": ["g_cmd"], "publish": [{"dst": "g_cmd", "src": "g_p"}],
        }])
        self.assertIn("kf_let_gate_take(cycle_id, KF_PROD_ANY", c)
        self.assertIn("#define KF_PROD_GID -1", self.files["kf_let_host.h"])

    def test_irq_safe_locks(self):
        """队列锁与标记锁必须是 raw_spinlock_t（PREEMPT_RT 硬中断安全）。"""
        c = self._render([0.0, 700.0], [])
        self.assertIn("DEFINE_RAW_SPINLOCK(kf_lock)", c)
        self.assertIn("DEFINE_RAW_SPINLOCK(kf_mark_lock)", c)
        self.assertNotIn("DEFINE_SPINLOCK(kf_lock)", c)
        # 注意不能直接 assertNotIn("spin_lock_irqsave")，那是 raw_ 版本的后缀
        self.assertNotIn("\n\tspin_lock_irqsave(&kf_lock", c)
        self.assertNotIn("\n\t\t\tspin_lock_irqsave(&kf_lock", c)

    def test_helpers_defined_before_use(self):
        """kf_stage_take 的定义必须早于 let_run（否则 -Werror=implicit-declaration）。"""
        c = self._render([0.0, 700.0], [{
            "kind": "output_commit", "stage": "prod", "at_us": 400.0,
            "compute_at_us": 0.0, "path": "irq", "on_timeout": "skip_commit",
            "vars": ["g_cmd"], "publish": [{"dst": "g_cmd", "src": "g_p"}],
        }])
        self.assertLess(c.index("static int kf_stage_take("),
                        c.index("static int let_run("))

    def test_timeout_policies_preserved(self):
        """三种 on_timeout 策略的返回码语义不变。"""
        base = {
            "kind": "output_commit", "stage": "prod", "at_us": 400.0,
            "compute_at_us": 0.0, "path": "irq",
            "vars": ["g_cmd"], "publish": [{"dst": "g_cmd", "src": "g_p"}],
        }
        hold = self._render([0.0], [dict(base, on_timeout="hold_last")])
        self.assertIn("KF_LET_ST_HOLD", hold)
        self.assertIn("let_hold_last++", hold)
        fault = self._render([0.0], [dict(base, on_timeout="fault")])
        self.assertIn("WRITE_ONCE(frozen, true)", fault)
        skip = self._render([0.0], [dict(base, on_timeout="skip_commit")])
        self.assertIn("let_skip_commit++", skip)


class SeamSplitUnit(unittest.TestCase):
    """KF_LET_STUB_SEAM=1：读写实现切到独立编译单元（C/D 单变量对照的前提）。"""

    EV = {
        "kind": "output_commit", "stage": "prod", "at_us": 400.0,
        "compute_at_us": 0.0, "path": "irq", "on_timeout": "skip_commit",
        "vars": ["g_cmd"], "publish": [{"dst": "g_cmd", "src": "g_p"}],
    }

    def _render(self, seam):
        os.environ["KF_LET_STUB_SEAM"] = "1" if seam else "0"
        try:
            files = {}
            host = sw.render(_ir([0.0, 700.0], [self.EV]), "kf_task",
                             _shared(), out_files=files)
        finally:
            os.environ.pop("KF_LET_STUB_SEAM", None)
        return host, files

    def test_seam_moves_let_run_out_of_host(self):
        host, files = self._render(True)
        self.assertIn("let_impl.c", files)
        self.assertNotIn("static int let_run", host)
        self.assertNotIn("let_stub_0", host)
        self.assertIn("int let_run(u64 cycle_id, u32 id, u32 op, u32 *ver_out)", files["let_impl.c"])
        self.assertIn("kf_let_gate_take", files["let_impl.c"])

    def test_seam_off_keeps_single_tu(self):
        host, files = self._render(False)
        self.assertEqual(sorted(files), ["kf_let_host.h"])  # 只有接口头，无独立实现
        self.assertIn("static int let_run", host)
        self.assertIn("let_stub_0", host)

    def test_both_modes_share_gate_interface(self):
        lit, _ = self._render(False)
        host, files = self._render(True)
        impl = files["let_impl.c"]
        for text in (lit, host, impl):
            self.assertIn('kf_let_gate_take(', text)
        # 门禁与计数只在宿主里实现，实现方只有调用
        self.assertIn("int kf_let_gate_take(", host)
        self.assertNotIn("int kf_let_gate_take(", impl)


if __name__ == "__main__":
    unittest.main()
