#!/usr/bin/env python3
"""PR11-E 回归测试：联合跨域时间可行性 + 相对漂移判据。

覆盖：
  A. _periodic_intersection 引擎（不相交 / 相交 / 跨 lcm 周期铺开）
  B. cross_edges 不再按 (域A→域B) 去重（PR11-E.1 的 bug 修复）
  C. exec_us 被 check_edge / check_contract_coded 消费（PR11-E.3 的 dict 路径）
  D. xdom_cons_drift_ppm 接通（PR11-E.2：字段传递 + 多速率下改变可行性）
  E. 向后兼容：只声明单 xdom_drift_ppm（无 cons）不报错

注：XDOM_JOINT_INFEASIBLE 的端到端触发需要「两条同 (Pp,Pc) 边有矛盾的相位要求」，
而当前 safe_phase_window 把相对相位 ρ 当作全局协调变量、不区分边身份，因此同
(Pp,Pc) 的两条边安全窗恒相同、交集非空。引擎（_periodic_intersection）由 A 组单
元测试覆盖；边级相位差异化是 PR11-E 后续（按实际数据依赖保留每条边的相位基线）。
"""
import sys


def fail(msg):
    print("  FAIL:", msg)
    sys.exit(1)


import xdom_timeline as tl

# A. _periodic_intersection 引擎
if tl._periodic_intersection([[ (100.0, 300.0) ], [ (500.0, 700.0) ]],
                             [1000.0, 1000.0]) != []:
    fail("A1 disjoint -> empty")
print("  ok A1: disjoint -> empty")
if tl._periodic_intersection([[ (100.0, 300.0) ], [ (100.0, 600.0) ]],
                             [1000.0, 1000.0]) != [(100.0, 300.0)]:
    fail("A2 intersect -> [100,300]")
print("  ok A2: intersect -> [100,300]")
if tl._periodic_intersection([[ (0.0, 500.0) ], [ (1500.0, 2000.0) ]],
                             [1000.0, 2000.0]) != []:
    fail("A3 cross-lcm disjoint -> empty")
print("  ok A3: cross-lcm disjoint -> empty")
if tl._periodic_intersection([[ (0.0, 500.0) ], [ (400.0, 900.0) ]],
                             [1000.0, 2000.0]) != [(400.0, 500.0)]:
    fail("A4 cross-lcm intersect -> [400,500]")
print("  ok A4: cross-lcm intersect -> [400,500]")

# B. cross_edges 保留所有同方向边（去重 bug 修复）
dag = {
  "nodes": [
    {"name": "s1", "domain": "nuttx"}, {"name": "s2", "domain": "nuttx"},
    {"name": "c1", "domain": "root"}, {"name": "c2", "domain": "root"}],
  "edges": [
    {"from": "s1", "to": "c1"}, {"from": "s2", "to": "c2"},   # 两条 nuttx->root
    {"from": "c1", "to": "s1"}]                                # 一条 root->nuttx
}
edges = tl.cross_edges(dag)
dirs = [(d, c) for a, b, d, c in edges]
if dirs.count(("nuttx", "root")) != 2:
    fail("B: expected 2 nuttx->root, got %r" % dirs)
print("  ok B: cross_edges keeps both nuttx->root edges (no directional dedup)")

# C. exec 参数生效（多速率）：budget 卡在 exec0 最坏(~1000)/exec1900 最坏(~1100) 之间
#    1:1 + latest 下 exec 不显现在年龄分布里，必须用多速率暴露；
#    这同时是 PR11-E.3「exec_us 被 check_edge 消费」的底层保障。
contract_mr = {
  "task": {"period_us": 1000},
  "timing_constraints": {
    "domain_period_us": {"nuttx": 2000, "root": 1000},
    "xdom_max_age_ticks": 1,
    "xdom_drift_ppm": 0}}
dag_mr = {"nodes": [{"name": "s1", "domain": "nuttx"}, {"name": "c1", "domain": "root"}],
          "edges": [{"from": "s1", "to": "c1"}]}
r_e0 = tl.check_edge(2000, 1000, 1050, drift_ppm=0, cons_drift_ppm=0,
                     jitter_us=0, exec_us=0.0)
r_e1900 = tl.check_edge(2000, 1000, 1050, drift_ppm=0, cons_drift_ppm=0,
                        jitter_us=0, exec_us=1900.0)
if r_e0["frac"] == r_e1900["frac"] and r_e0["ranges"] == r_e1900["ranges"]:
    fail("C: exec should change safe-window shape; frac %.3f/%.3f identical"
         % (r_e0["frac"], r_e1900["frac"]))
# 冒烟：check_contract_coded 接受 exec_us dict（PR11-E.3 的 dict 路径）且不因类型报错
tl.check_contract_coded(dag_mr, contract_mr, exec_us={"s1": 0.0})
print("  ok C: exec changes feasible window shape + check_contract_coded accepts exec_us dict")

# D. cons_drift_ppm 接通：字段传递 + 多速率下改变可行性
r0 = tl.check_edge(1000, 1000, 1000, drift_ppm=0, cons_drift_ppm=0)
r15 = tl.check_edge(1000, 1000, 1000, drift_ppm=0, cons_drift_ppm=15)
if r0["cons_drift_ppm"] != 0 or r15["cons_drift_ppm"] != 15:
    fail("D: cons_drift_ppm field not passed through")
else:
    r_mr0 = tl.check_edge(2000, 1000, 1000, drift_ppm=0, cons_drift_ppm=0)
    r_mr15 = tl.check_edge(2000, 1000, 1000, drift_ppm=0, cons_drift_ppm=15)
    if r_mr0["frac"] == r_mr15["frac"] and r_mr0["ranges"] == r_mr15["ranges"]:
        fail("D: multi-rate cons_drift should change feasibility (0/0 vs 0/15)")
    else:
        print("  ok D: cons_drift_ppm passed + changes multi-rate feasibility")

# E. 向后兼容：只声明单 drift（无 cons_drift）不应报 XDOM_DRIFT_INVALID
errs2, _ = tl.check_contract_coded(dag_mr, contract_mr, exec_us=None)
if any(c == "XDOM_DRIFT_INVALID" for c, m in errs2):
    fail("E: backward-compat single drift raised XDOM_DRIFT_INVALID")
print("  ok E: backward-compat single drift (no cons_drift) works")

print("PR11-E LOGIC CHECKS PASSED")
