# Gate 0 — 冻结实验基线（2026-10-27）

本文件记录下一阶段研究（选择性内核化编译器，TEIR）启动前的**冻结基线**：
版本、测试环境、四套自测的实际结果与完整日志。所有测试实际通过后才冻结；
后续开发不得再以"扩展通信层/LET 重同步"为主目标。

## 1. 版本

| 项 | 值 |
|----|----|
| 分支 | `feat/ecos-temporal-semantics` |
| HEAD | `a9c8ccc349712b0c1aa3ebb5da0cd0a3dd58f137`（PR12-B） |
| 相对 main | 领先 27 次提交 |
| 工作区 | 干净（仅本冻结目录为新增） |

本分支保留的时间分析组件（**不推倒重写**，作为后续 IR/验证的基础）：
`kfusion/scripts/ecos/resync_synth.py`、`test_resync_synth.py`、`test_xdom_joint.py`、
`xdom_timeline.py`、`plan_verify.py`。

## 2. 测试环境

- 本机：Windows 开发机（Git Bash），**无 gcc/clang/make、无内核树**。
- Python：3.13.14。
- 因此本机只能做**结构与逻辑回归**（生成物是否具备可编译/可运行的必要结构、规划/验证是否正确拒绝），
  **不做**"编译通过"或板上运行——那些必须在目标侧（Pi4，内核 5.15.86）完成。
- 说明：`fusion_selftest` 是**融合规划器 + 代码生成器**的 Python 回归（132 项），
  不是 LLVM 编译测试；LLVM 内核化链路的编译测试需在 Pi4 上另做。

## 3. 自测结果（全部通过）

| 套件 | 命令 | 结果 | 日志 |
|------|------|------|------|
| 融合规划器回归 | `bash kfusion/scripts/test/fusion_selftest__自测.sh` | **132 通过 / 0 失败**（rc=0） | `fusion_selftest.log` |
| 双后端合成回归 | `bash kfusion/scripts/test/ecos_synth_selftest__后端自测.sh` | **175 通过 / 0 失败**（rc=0） | `ecos_synth_selftest.log` |
| PR11-E 联合可行性 | `python3 kfusion/scripts/ecos/test_xdom_joint.py` | **PASSED**（rc=0） | `test_xdom_joint.log` |
| PR12-A 重同步综合 | `python3 kfusion/scripts/ecos/test_resync_synth.py` | **PASSED**（rc=0） | `test_resync_synth.log` |

合计 **307 项断言 + 2 套 Python 逻辑检查，全过**。

## 4. 冻结约定

1. **停止**继续扩展通信层/LET 重同步功能（它们已是可用的时间分析组件，不再作为主目标）。
2. 后续研究按 Gate 1→Gate 6 推进，最高优先级是 **Gate 1：真正的同源 C → 用户态/内核态生成**
   （Clang Tooling 计算区域提取，`kfusion/frontend/stage_extract/`）。
3. 任何会改变上述基线行为的改动必须新开分支/单独提交，并重跑本目录四套自测后再合并。
4. 已存在的实验产物（`results/paper/multitask/` 等板上数据）保留，不在此次冻结中重跑；
   多程序板上实验归入 Gate 6。

## 5. 备注

- `git fetch` 后远端出现新分支 `origin/codex/proposal-draft-20261025`（非本基线范围）。
- 本目录日志为冻结快照，不随日常开发更新。
