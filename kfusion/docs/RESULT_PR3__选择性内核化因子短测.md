# PR-3 板上因子短测（经验 R，不是 WCET）

同一 Linux 5.15.86 aarch64，CPU0，`SCHED_FIFO 80`，周期 1000 µs。
五阶段闭环；digest A=B = `5d4cda7d`。R = 末组完成 − 首组名义释放。

| 格 | 部署 | R p50 (µs) | miss |
|---|---|---:|---:|
| A | 用户态、不融合 | 811 | 0 |
| B | 用户态、融合 | 12 | 0 |
| C | 内核、不融合 | 811 | 0 |
| D | 内核、融合 | 13 | 0 |

自动选择（`candidate_generate`，本管线 `c_user=c_kernel=21`）：**B**。
同分不进内核。经验上 B 与 D 同量级，这个拒绝解释得通。
`c_kernel=10, extra=0` 时公式选 **D**（自测已覆盖生成物翻转）。

原始日志：`kfusion/docs/pr3_abcd_matrix/logs/`，表：`report.json`。
