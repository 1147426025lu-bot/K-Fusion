# Gate 2 证据：IR 级数据依赖（use-def）与 TEIR（时序效应中间表示）

> 配套提交（在 feat/ecos-temporal-semantics 分支）：
> - `stage_extract__阶段提取.c`：新增 `collect_rw`（IR 级读/写分析）
> - `ast_to_plan.py`：`build_dag` 用真实数据依赖建边 + 产出 TEIR
> - `kfusion/ir/temporal_effect_ir.py`：新建 TEIR 模块
> - `test_ast_to_plan.py`：5/5（含数据边/TEIR 断言）

## 1. 动机

Gate 1 的 `ast_to_plan` 只能按**调用序**近似建图——`edges` 为空、`declared_deps` 退化为
"前一阶段"。这不反映真实数据流动，也就无法表达"功能等价 ≠ 时间正确"要钉死的那条约束：
生产者 A 在周期 k 写 `w_g(k)`，消费者 B 读 `r_g(k)`，A 必须在 B 之前（同拍内）。

Gate 2 让提取器给出每个阶段的读/写全局（IR use-def），据此建**真实数据依赖边**，并产出
**TEIR**——把计算事实/时间事实/执行事实分离、可独立检查的中间表示。

## 2. 提取器改动（IR 级 use-def）

`collect_rw` 遍历每个阶段函数的每条 IR 指令：
- `Load` 的操作数 0 指向全局 → 记 `reads`
- `Store` 的操作数 1 指向全局 → 记 `writes`
- 支持解一层 `getelementptr`（数组/结构体元素）
- 按全局去重；只记录 `collect_globals` 已登记的"用户状态"全局（编译器内部 `.str` 等已过滤）

输出进每个 stage 的 `reads`/`writes` 数组。

## 3. ast_to_plan 改动（真实数据边）

对阶段 B 的每个读 `g`，在调用序中 B 之前找**最后一个**写 `g` 的阶段 A，建边：
```json
{"from":"stage_sample","to":"stage_pid","kind":"data","proven":"g_output","var":"g_output","version":"same_period"}
```
- `kind:"data"` 满足规划器 `ORDER_KINDS`（顺序边，生产者必须先于消费者）；
- B 读但本周期无人写的 `g` → 记入 `cross_period_inputs`（外部输入 / 上一周期状态），不算内部边；
- 节点 `declared_deps` 反映真实数据入边（不再是"前一阶段"）。

## 4. TEIR（kfusion/ir/temporal_effect_ir.py）

三类事实：
- **计算事实**：每个节点 `reads`/`writes`（如实，来自 IR use-def）
- **时间事实**：`period_us`/`phase_us`，`data_edges[].version="same_period"`（r_i(k) 依赖 w_i(k)）
- **执行事实**：`kernelizable`（来自提取器 `k_c0.ok`）
- 辅助：`state_versions`（每全局 written_by/read_by 顺序）、`cross_period_inputs`

## 5. Pi4 实测（`ctl.c`，clang/llvm 16.0.6）

提取 JSON 的 stages 读写（与源码语义一致）：
```
stage_sample  reads=[g_target, g_output]   writes=[g_output]
stage_pid     reads=[g_output, g_integral] writes=[g_integral, g_output]
stage_clamp   reads=[g_output]             writes=[g_output]
globals=[g_target, g_output, g_integral]
```

整链 `auto_kernelize.sh /tmp/ctl.c out 1000 linux-kernel`：
```
verification.ok=True groups=1
生成 7 个文件（含 teir.json）
```

`teir.json` 关键片段（节选）：
```json
"data_edges": [
  {"from":"stage_sample","to":"stage_pid","kind":"data","proven":"g_output","var":"g_output","version":"same_period"},
  {"from":"stage_pid","to":"stage_clamp","kind":"data","proven":"g_output","var":"g_output","version":"same_period"}
],
"cross_period_inputs": [
  {"stage":"stage_sample","var":"g_target"},
  {"stage":"stage_sample","var":"g_output"},
  {"stage":"stage_pid","var":"g_integral"}
],
"kernelizable": true
```

`.ko` 构建+运行回归（Gate 2 改动未破坏 Gate 1 验收）：
```
LD [M] plc_cycle.ko
insmod rc=0  periods=2010  missed=0  rmmod rc=0
```

## 6. 本机测试（test_ast_to_plan.py 5/5）

新增断言：
- `dag.edges` 恰为 `sample→pid(g_output)`、`pid→clamp(g_output)`（2 条，非调用序近似）
- 节点 `declared_deps` 反映真实数据入边（clamp→[pid]、pid→[sample]、sample→[]）
- `teir.json`：`kernelizable=true`、2 条 `data_edges`、3 条 `cross_period_inputs`、
  `stage_pid` 的 `reads`/`writes` 如实

## 7. 诚实边界（v1）

- 单域同周期骨架；`exec_us` 仍是占位（需实测替换，不得进论文）
- 跨域 / 多速率版本依赖（r_i(k) 与 r_j(k-1)）属 Gate 2 后期 + Gate 3，TEIR 暂只给单域同周期
- 指针别名 / 间接访问（多级 GEP、通过指针参数传递全局地址）尚未覆盖——当前只解一层 GEP
- 循环内/递归 helper 的 use-def 未展开（K-C0 只查直接外部调用）
