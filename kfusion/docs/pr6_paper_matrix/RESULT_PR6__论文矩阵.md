# PR-6 选择性内核化矩阵

经验值，不是 WCET。不得从短重叠 R 写内核更快。

| 负载 | chosen | 域 | plan_pass | board_pass | end_to_end |
|---|---|---|---|---|---|
| W1_stateless_uku | STKP:L[stage_sample]|L[stage_add+stage_shift]|L[stage_sink] | mixed | True | True | True |
| W2_stateful_pid | STKP:L[stage_sample]|L[stage_pid]|L[stage_sink] | mixed | True | False | False |
| W3_actuator_window | STKP:L[s1+s2]|L[s3+s4] | linux_userspace | True | False | False |

- **W1_stateless_uku**：无状态 UKU；digest 一致不是内核更快；非 WCET
- **W2_stateful_pid**：单 int32 积分会话：STKP 可生成 UKU；非任意 persistent（规划证据）
- **W3_actuator_window**：s3 窗口 [380,520]；STKP 两段用户态；无 LET 全融合产物（规划证据）
