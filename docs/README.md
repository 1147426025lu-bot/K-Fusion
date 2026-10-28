# K-Fusion 文档入口

建议按“状态 → 设计 → 证据 → 复现”阅读，避免从单个实验数字反推系统结论。

## 先读

- [项目状态与证据边界](PROJECT_STATUS__项目状态.md)
- [仓库结构](../REPO_LAYOUT.md)
- [开发指南](DEV__开发指南.md)
- [论文全貌与实验设计](PAPER_WALKTHROUGH__论文全貌与实验设计.md)

## 主研究线

- [阶段提取、K-C0、TEIR 与从 C 编译](../kfusion/frontend/stage_extract/README.md)
- [Gate 6 消融与反例](../kfusion/docs/RESULT_GATE6__消融与反例.md)
- [选择性内核化 A/B/C/D 因子短测](../kfusion/docs/RESULT_PR3__选择性内核化因子短测.md)
- [STKP 实机闭环](../kfusion/docs/pr4c_stkp_board/RESULT_PR4C__STKP实机闭环.md)

## 证据解释规则

1. 先确认文档对应的 commit、平台、内核、调度和周期。
2. 区分“静态约束验证”“生成物验证”和“板上经验测量”。
3. `max` 只是采样最大值，除非另有证明，不称为 WCET。
4. 单个示例的最优方案只在其成本与约束假设下成立。
5. 设计文档、结果说明和机器可读 JSON 应相互对应；若冲突，以可复现产物和当前代码为准。
