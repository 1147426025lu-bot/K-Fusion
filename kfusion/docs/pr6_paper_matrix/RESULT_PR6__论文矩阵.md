# PR-6 选择性内核化矩阵

经验值，不是 WCET。不得从短重叠 R 写内核更快。

| 负载 | chosen | 域 | adapter | ok |
|---|---|---|---|---|
| W1_stateless_uku | STKP:L[stage_sample]|L[stage_add+stage_shift]|L[stage_sink] | mixed | uku_ioctl | True |
| W2_stateful_pid | B | linux_userspace | None | True |
| W3_actuator_window | STKP:L[s1+s2]|L[s3+s4] | linux_userspace | None | True |

- **W1_stateless_uku**：无状态 UKU；digest 一致不是内核更快；非 WCET
- **W2_stateful_pid**：积分跨拍：UKU v1 拒绝，不把状态内核化
- **W3_actuator_window**：s3 窗口 [380,520]；合法两段用户态，不得 UKU/全融合
