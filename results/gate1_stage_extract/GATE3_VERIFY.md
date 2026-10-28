# Gate 3：与规划器分离的验证（验生成物 + 独立事件解释器）

日期：2026-10-28　分支：`feat/ecos-temporal-semantics`　提交：`a328a0d`

## 动机

Gate 1/2 的证据链是「提取 → 规划 → `plan_verify` 签署 → 生成代码」。但**签名发生在
生成之前**：`plan_verify` 保证的是"方案合法"，并不能保证"生成的 C 真的照方案写了"。
合成器（`body_emit` / `emit.py`）与规划器共用同一份 IR，一旦合成逻辑有 bug
（漏一个阶段、偷偷重排、计时钩子索引错位、跨域桥被静默丢弃），
**验证仍然会通过**——这正是「生成可独立检查的实际代码」要堵的口子。

Gate 3 因此加两道**与规划器解耦**的独立检查：

1. **验生成物**（静态）：把生成的 `ecos_fused_body.c` 当黑盒重新解析，核对它确实兑现
   方案契约。写在落盘**之前**，不符就整个产物集都不写。
2. **独立事件解释器**（动态）：给定 TEIR 的数据版本模型 + 执行 trace，独立反推并校验
   时序语义（同拍/前拍版本、期限）是否真的成立，不信任规划器。

## 新增文件

- **`kfusion/ir/verify_generated.py`** —— 验生成物
  - `parse_body(text)`：按 `void ecos_body_gN(void) {` 切块，从阶段调用行的行尾注释
    （`/* stage_sample，相位 0 µs */`）取阶段名，按块内出现顺序收集 `ecos_stage_enter/
    exit(k)` 并**按序与阶段配对**；同时统计 `ecos_xdom_sub` / `ecos_xdom_pub` 数量。
  - `expected_names_from_plan(plan)`：从 `plan.groups[*].members` 取有序阶段名
    （规划器的 `groups` 用 `members` 存有序成员，不是嵌套 `stages` 列表）。
  - `verify(body, plan=…)`：核对四件事，任一不成立即 `ok=False`
    | 检查项 | 判据 | 失败含义 |
    |---|---|---|
    | 覆盖 | 生成阶段集合 == 方案集合 | 缺阶段 / 夹带未声明阶段 |
    | 顺序 | 生成顺序 == `members` 序 | 合成器偷偷重排（会破坏版本依赖） |
    | 计时钩子 | 每阶段被 `enter(k)/exit(k)` 包裹，`k` 为 `0..N−1` 连续且成对 | `exec_probe` 实测会把时间错配到别的阶段 |
    | 跨域桥 | 生成体 sub/pub 数量 == IR 声明数 | 桥被静默丢弃（跨域读到的永远是旧值） |
  - 可直接当 CLI 用：`verify_generated.py <body.c> <plan.json> [--xdom-sub N --xdom-pub M]`，
    失败退出码 3。

- **`kfusion/ir/event_interpreter.py`** —— 独立事件解释器
  - `check_trace(teir, trace, deadline_us)`，trace 形如
    `[{k: 0, stages: {"stage_sample": (enter_us, exit_us), …}}, …]`。
  - `same_period` 边 A→B：要求 **A(k) 退出 ≤ B(k) 进入**（生产者先写完，消费者才读）。
  - `prev_period` 边（lag=L）：要求 **A(k−L) 退出 ≤ B(k) 进入**（否则 B 根本读不到那一版）。
  - 期限：每周期 `max(exit) − min(enter) ≤ deadline_us`。
  - 缺生产者记录只记 `warnings`（trace 不完整时不该误判为违例）。

## 修改文件

- **`kfusion/backend/synthesis/emit.py`**
  - 新增 `_load_ir_module(path, name)`，按路径加载 `kfusion/ir/` 下的校验模块，
    与合成逻辑解耦（不 import 包结构）。
  - 生成 `ecos_fused_body.c` 的文本**先过 `verify_generated.verify`**：
    `expected_xdom_subs/pubs` 由 IR 的 `stage.xdom_sub/xdom_pub` 汇总得到。
    失败 → 打印具体背离项 → `return 3`，**连 body 都不落盘**（避免一份不符契约的
    周期体躺在产物目录里被 `make` 编出来）。

## 验证

```
cd kfusion/frontend/stage_extract
python3 -m unittest test_gate3_verify   # 15 tests  OK
python3 -m unittest test_ast_to_plan    #  5 tests  OK（Gate 1 回归）
python3 -m unittest test_version_deps   # 11 tests  OK（Gate 2 回归）
```

`test_gate3_verify.py` 覆盖：

- **验生成物（单元）**：正常解析；缺阶段；顺序被交换；漏 enter/exit 钩子；
  钩子索引非连续（`enter(2)`→`enter(5)`）；声明 1 处订阅但生成体没有；
  订阅/发布数量吻合时放行。
- **独立事件解释器**：`same_period` 正常/违例（A 退出晚于 B 进入）；`prev_period lag=1`
  正常/违例（B(k=1) 进入早于 A(k=0) 退出）；期限违例。
- **emit 端到端**：真跑 `ast_to_plan` + `emit`，确认生成的 body 过门禁且阶段顺序
  与方案一致；再手工交换两行阶段调用，确认门禁能抓出「顺序」背离。

## 诚实边界（本 Gate 未覆盖）

- **验生成物是结构级的**，不是语义等价证明：它确认"阶段集合/顺序/计时钩子/跨域桥数量"
  与契约一致，**不**证明阶段函数体本身与源 C 语义等价（那属于前端提取的正确性，
  由 Gate 1 的 IR use-def + 人工核对承担）。
- 阶段识别依赖生成体行尾统一的 `/* <name>，相位 X µs */` 注释；若将来改了注释格式，
  `verify_generated` 会误报「缺阶段」而不是静默通过——这符合 fail-closed 取向。
- 事件解释器只校验**数据版本可达性 + 期限**，不建模 CPU 抢占、中断延迟、
  xipc 传输抖动；这些由既有 `ecos/` 的 simulate 与板上实测（PR11 系列）负责。
- trace 目前需由外部产生（板上 `exec_probe` 只有聚合值，尚无逐拍 enter/exit 日志）；
  逐拍 trace 的采集属于后续板上实验工作。
