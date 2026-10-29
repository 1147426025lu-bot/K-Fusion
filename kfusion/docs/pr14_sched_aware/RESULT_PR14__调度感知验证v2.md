# RESULT PR-14：调度感知的可行性验证 v2（三列分开，且发现一处口径错配）

**一句话**：验证器现在给三列——`compile_legal` / `model.feasible` / `deadline_verdict`，
**没有「可调度」这一档**。落地过程中查出一个比验证器更严重的问题：
PR-13 首版把 `gold`（单次释放）的 Q99 当成了候选 A（三次释放）的数，
两者差 **383 µs**。已修正，并把释放结构纳入形状键。

---

## 1. 为什么单一 R̂ 不够（审查 P1）

旧模型把响应压成一个标量 `R̂ = ΣC + ΣL + ΣH`，于是「验证通过」被读成「满足期限」。
实测反例（本轮仍在）：候选 B 的 `R̂_model = 36 µs`，远低于 deadline 1000 µs，
模型判可行；但它在 **CFS 下实测 5/50000 拍超期，最差 1000 µs 以上**。

v2 的做法是把三件事拆成三列，任何一列都不能替代另一列：

| 列 | 含义 | 谁给的 |
|---|---|---|
| `compile_legal` | 语义/结构合法 | verify（与时间无关） |
| `model.feasible` | 旧成本模型 R̂ ≤ deadline | 保留作对照，**不作为期限结论** |
| `deadline_verdict` | 只认实测，四档 | 实测画像 |

## 2. 四档结论（没有第五档「可调度」）

| 档 | 触发条件 | 输出 |
|---|---|---|
| `infeasible_observed` | 实测超期数 > 0 | 直接判不可行，附 n/N 与最差值 |
| `cannot_certify` | 0 超期，但 Wilson 上界 × 任务时长内拍数 ≥ 1 | 给出残余风险上界，不背书 |
| `empirically_feasible` | 0 超期且上界折成的次数 < 1 | 只对给定 horizon 成立 |
| `insufficient_evidence` | 形状/调度类/拍数/deadline 缺 | 不猜，直接拒绝 |

`R̂` 的分解只在**两项同源**时才给：`R̂ = L̂_release(p99) + Ĉ_exec(p99)`。
缺 work p99 时**不拿 Q99 反推 Q99**（那是自证），直接标 `reconstruction=null`。
边界项不重复加：Q99 已含跨界往返（PR-11 的 γ=0 规则）。

## 3. 实测结果（Pi4 5.15.86 PREEMPT，deadline 1000 µs，horizon 1 h）

| 候选 | 组/释放 | `model.feasible` | CFS | FIFO99 |
|---|---|---|---|---|
| B（全融合） | 1 / 1 | ✅ 36 µs | **✗ 实测 5/50000 超期** | △ 不能背书（上界 276 次/h） |
| split_sp_k | 2 / 2 | ✅ 57 µs | ✗ 实测 1/50000 | △ 不能背书 |
| split_s_pk | 2 / 2 | ✅ 57 µs | △ 不能背书 | △ 不能背书 |
| A（三组） | 3 / 3 | ✅ 78 µs | △ 不能背书 | △ 不能背书 |
| mixed | 3 / 3 | ✅ 31.05 µs | ? 证据不足 | ? 证据不足 |

统计：`infeasible_observed=2, cannot_certify=2, insufficient_evidence=1`。

读法：
- **4 个候选里 2 个在 CFS 下实测超期，而旧模型对它们全部判「可行」**——这正是
  审查说的「验证器通过 ≠ 期限满足」。模型没有错在算错数，错在只算一个标量。
- **FIFO 下没有候选能被背书**：5 万拍 0 超期，Wilson 上界 7.68e-5，折算到 1 小时
  （3.6e6 拍）是 276 次。要拿到 `empirically_feasible`，horizon 得缩到 ~13 s 以内。
  这不是保守过度——3.6e6 拍里 0 次超期本来就没被观测到。
- mixed 是 `insufficient_evidence`，原因见 §4，不是漏测。

## 4. 查出的口径错配（比验证器更严重）

PR-13 首版把 `user_only`（PR-10E 用 `driver gold` 测的）按「3 组不融合」匹配给
候选 A（`plan` 模式，三组各释放一次）。**组数一样，执行结构不一样**：

| 结构 | 探针头 | 实测 Q99（FIFO99） |
|---|---|---|
| `gold`：原程序，一个周期里三段顺序跑 | 无 `releases_per_period` 字段 | 29.47 |
| `plan` 3 组：0/200/400 µs 各释放一次 | `releases_per_period=3` | **412.00** |

差 **382.5 µs**，全部来自相位跨度——响应要等到最后一次释放才结束。

修正：
1. `releases_per_period` 从**原始日志的探针头**解析进画像（60 条已回填，没重测）；
2. 形状键从 `(组数, 融合)` 改为 `(组数, 释放数, 成员集合)`；
3. 释放数未知的画像（gold/mixed）**不得**与 plan 候选配对 → 候选 A 现在按
   新测的 plan 形状（462/412）参与，mixed 因为没有 plan 口径的测量而
   `insufficient_evidence`。

这条也修正了 PR-11/PR-13 的一句话：**B（15.00）不是比 A 快 14.47 µs，
而是比 A 的 plan 形状（412.00）快 397 µs**；14.47 µs 是相对 gold 的差。

## 5. 接口

```bash
python3 kfusion/scripts/pr14_sched_aware_verify__调度感知验证.py \
  --stkp .../candidates_pr10e_stkp.json \
  --profile .../sched_profile_v2.json \
  --deadline 1000 --horizon-s 3600 -o pr14_report.json
```

流水线里也有一步（`7c3`，仅当给了 `DEPLOY_OBJECTIVE`）：把入选候选的
`deadline_verdict` 写进 `deploy.json.deadline`，默认只报告；
`REQUIRE_DEADLINE_EVIDENCE=1` 时遇到 `infeasible_observed` 或
`insufficient_evidence` 直接退出 9，不生成代码。

## 6. 仍然不成立的说法

- 「验证器通过 = 可调度」——不成立，本轮反例就是 CFS 的 5/50000。
- 「FIFO 下没问题」——只能说 5 万拍没看到，1 小时内上界 276 次。
- 「Q99 更小就一定更好」——跨释放结构比较无意义（见 §4）。
- 「mixed 更差」——现在它连排名资格都没有，不能说好坏，只能说没测。

## 7. 复现与回归

- 单元测试 7 项（`test_sched_aware_cost.py`）：模型可行但实测超期必须同时表达、
  零超期在长时长内不能背书/短时长可行、同候选不同调度类结论不同、
  形状未测只给证据不足、无 horizon 不许给可行、分解只在同源时给、
  声明里没有「可调度」字样。
- 全量：`Ran 57 tests ... OK`（含 STKP 44 项，保证旧行为不变）。
