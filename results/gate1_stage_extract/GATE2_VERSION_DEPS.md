# Gate 2 后期：数据版本依赖建模（跨域 / 多速率）+ 独立复核

日期：2026-10-28　分支：`feat/ecos-temporal-semantics`　提交：`108e94b`

## 动机（来自 MEMORY.md 诚实边界）

> 需修 PlanVerifier：跨域边不能一律「下一拍可见」，应把通信代价与所需数据版本分离，
> 只有同拍消息确能在消费者读取前可用才允许同拍跨域。

Gate 2 v1 的 TEIR 把所有数据边都标成 `same_period`，把「读但无同周期生产者」lump 成
`cross_period_inputs`。这只在**单域同周期**成立；一旦出现**跨域**或**多速率**，就不能
默认同拍可见。本次把版本依赖显式建模，并在 IR 层落实上述 PlanVerifier 诚实边界。

## 新增 / 修改文件

- **新增 `kfusion/ir/version_deps.py`**（Gate 2 后期 + Gate 3 准备）
  - `compute_lag(Pa, Pb, xdom, phase_a, phase_b, proof)`：纯函数，从
    (生产者周期, 消费者周期, 是否跨域, 相位, 相位证明) 推导**必要最小延迟** lag_min。
  - `analyze(dag)` / `verify(dag)`：**与规划器分离**的独立复核。对任意数据边，从
    (域/周期/相位) 重新推导必要延迟，与 ast_to_plan 声明的 `version` 比对；
    声明比必要更乐观（over-claim）→ 判错（fail-closed）。
- **修改 `kfusion/frontend/stage_extract/ast_to_plan.py`**
  - `build_dag` 支持每阶段 `domain`（stage.domain / meta.domains）、每域周期
    `domain_period_us`（`--domain-periods`）、每节点 `period_us`（stage.period_us 覆盖）。
  - 数据边标注 `version`(same_period/prev_period) + `lag_periods` + `xdom`；
    **跨域边默认 `prev_period`**（自动提取程序无法证明跨域同拍可达，故 fail-closed 降级，
    杜绝 over-claim）。
  - `main()` 在规划器之后调用 `version_deps.verify(dag)`，over-claim 即 `verification.ok=False`。
- **修改 `kfusion/ir/temporal_effect_ir.py`**：TEIR 透传 `version/lag/xdom`，
  meta 新增 `version_model` 说明，诚实边界从「只单域同周期」升级为「跨域/多速率已显式建模」。
- **新增 `kfusion/frontend/stage_extract/test_version_deps.py`**：11 个用例全过。

## 版本语义（compute_lag）

令 R = P_生产者 / P_消费者：

| 情形 | 必要延迟 lag_min | 说明 |
|---|---|---|
| 同域、相位 A≤B、同周期 | 0（same_period） | 生产者在本周期 B 读之前完成 |
| 同域多速率 R>1（生产者更慢） | R−1（prev_period） | 生产者每 R 个消费者周期才跑一次 |
| 同域多速率 R≤1（生产者不慢） | 0（same_period） | 每消费者周期生产者至少跑一次 |
| 跨域、无相位证明（默认） | max(1, R−1)（prev_period） | **不默认同拍可见** |
| 跨域、有相位证明且 R≤1 且相位有序 | 0（same_period） | 证明跨域消息在消费者读前可达才放行 |

`cross_period_inputs`（读但无同周期生产者）本就是 `prev_period` 读取（外部/前周期状态）。

## 验证结果

- `python3 -m unittest test_version_deps`：**11/11 通过**（含单域回归、跨域默认 prev_period、
  多速率 2:1 跨域 prev_period、生产者更快 same_period、over-claim 检测、带证明跨域同拍放行、
  TEIR 透传版本）。
- `python3 -m unittest test_ast_to_plan`：**5/5 通过**（Gate 1 全链回归，含 emit 源码，
  未破坏既有断言）。
- 跨域 smoke（sample∈linux_kernel，pid/clamp∈nuttx）：ast_to_plan 正确把 sample→pid 标为
  跨域 `prev_period` lag 1；规划器按硬约束**正确拒绝跨域融合**（「一组只能有一个域」，
  end-to-end 1038µs > 1000µs 期限）；`version_deps.verify` 独立确认无 over-claim。

## 诚实边界（v2 仍保留）

- 仅解一层 GEP（指针别名/间接访问未覆盖）；循环内/递归 helper 的 use-def 未展开。
- K-C0 只查直接外部调用。
- 多域**部署**（两端内核模块 + xipc）由既有 `ecos/` 跨域管线负责；本 Gate 2 自动生成路径
  的本职是把**版本依赖正确建模并 flag「不可融合」**——已做到；Pi4 双域构建受 NuttX 工具链
  限制仍为环境阻塞（已在 MEMORY.md 记录），不在本次范围。
- `same_period_xdom_proof` 仅作为「调用方显式提供相位证明」的钩子；自动提取路径一律不声明，
  故跨域边保守降级为 prev_period。
