# PR-7C 实测成本标定（经验值，不是 WCET）

人工 STAGE_COST 只用于仪器构建。结题选型必须带来源日志。

| 项 | 值 |
|---|---|
| calib_pass | True |
| holdout_pass | True |
| end_to_end_pass | True |
| STKP chosen | B |
| domain | linux_userspace |
| predicted_R_us | 13.876 |
| holdout gold R avg | 15.746 |
| holdout mixed R avg | 21.59 |
| pred rel err | 0.11876032008129055 |
| mixed faster on holdout | False |

；holdout 未显示 mixed 更快
