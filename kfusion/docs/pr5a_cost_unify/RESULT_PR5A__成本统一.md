# PR-5A 成本与时间验证统一（经验值，不是 WCET）

日志 `/root/K-Fusion/kfusion/docs/pr4c_bd_paired/logs/B_0.log`。Ĉ 用 avg；内核逐阶段 missing。

| 阶段 | 用户态 avg (µs) | 内核 |
|---|---:|---|
| stage_sample | 0.378 | missing |
| stage_fir | 0.467 | missing |
| stage_pid | 0.302 | missing |
| stage_clamp | 0.302 | missing |
| stage_plant | 0.262 | missing |

可追溯回填：chosen=B claim=empirical_model_not_wcet final=True

伪造 measured：claim=explore_uncertain（不得 empirical_model_not_wcet）

FIR=400µs 覆盖后 schedule.exec_us.stage_fir=400.0，与 Ĉ 同源：True
