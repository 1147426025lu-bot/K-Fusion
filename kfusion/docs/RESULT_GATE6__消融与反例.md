# Gate 6 消融与反例（经验结论，不是 WCET）

生成：`python3 kfusion/scripts/paper/gate6_ablation__消融反例.py`  
机器可读：`kfusion/analysis/build/gate6/report.json`

本页只记录**编译器选择与拒绝是否符合契约**，不把观测最大值写成最坏情况。

| 反例 | 期望 | 实现 |
|---|---|---|
| PID 输出本拍未读 | 不得删除（积分 self_carry） | TEIR `must_preserve` |
| 别名未证 | 保留依赖或拒绝重排 | `keep_deps` |
| 跨拍先写后读 | 拒绝 | `E_PREV_JOB_BECAME_SAME` |
| 年龄改成 k-2 | 拒绝 | `E_AGE` |
| 漏掉 PID | 拒绝 | `E_DROP_STATE` |
| MMIO 进 linux_kernel | 拒绝 | `E_DOMAIN` |
| 全融合提前输出提交 | 拒绝 | `E_PIN_EARLY_COMMIT` |
| 周期 1000→1500 | 严格周期拒绝 | `E_PERIOD_STRETCH` |
| 两条相位窗交集为空 | 整份拒绝 | `E_WINDOW_EMPTY` |
| malloc 周期体 | 不生成内核产物 | `E_KC0_FORBIDDEN_CALL` |
| 内核附加税更大 | 选用户态融合 B | A/B/C/D |
| LET 事件更贵 | 不用 LET，选钉扎分组 | `let_decide` |

消融：always-kernel、always-LET 在上述反例上劣于联合选择。三类程序覆盖无状态流水线、五阶段闭环、多约束窗口。
