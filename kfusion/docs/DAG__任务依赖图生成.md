# 任务 DAG 生成（ECoS PR 3）

> 状态：**已实现并实测**（2026-10-23）· 平台 Pi4B + LLVM 16.0.6
> 入口：`kfusion/scripts/dag_build__任务DAG生成.py`
> 上游：`EffectSummaryAnalysis`（`plc-effect-summary`，输出 v22 含 `interface_bindings`）
> 自测：`kfusion/scripts/test/dag_selftest__自测.sh` → **16/16**
> 相关：`ANALYSIS__效果摘要分析.md`（函数级摘要）、`CONTRACT__任务契约与校验.md`（输入门禁）

---

## 1. 为什么需要这一步

`EffectSummaryAnalysis` 回答的是**函数级**问题：「这个函数会读/写哪些区域、会不会睡、会不会碰 MMIO」。
但 ECoS 的优化对象是**任务级**的：哪个阶段必须在哪个之前、哪些可以并行或融合。

中间缺的这一层，就是**任务 DAG**。而且它比前两步更危险：

> 效果摘要错一条，是"某个阶段被判成能放/不能放"；
> 依赖图错一条边，是"求解器把有依赖的两段判成可重排"——**那是正确性问题，不是精度问题**。

所以本文档的重点不是"我们连出了多少条边"，而是**每条边的语义与可证性**。

---

## 2. 边的四类语义（方向性不同，绝不能混）

| kind | 条件 | 约束 | 可证性 |
|---|---|---|---|
| `call` | A（传递地）调用 B | A 在 B 之前 | 可证 |
| `data` | A 写 r，B 读 r（RAW） | A 在 B 之前 | 可证（r 须为模块级具体区域） |
| `anti` | A 读 r，B 写 r（WAR） | A 在 B 之前 | 可证；**已有反向 RAW 时不再重复** |
| `conflict` | A 写 r，B 也写 r（WAW） | **无方向**：只禁止重排/融合 | 可证 |
| `candidate` | 区域只到 `arg:N`（多绑定） | 不详 | **未证明**，不得当依赖 |

`conflict` 单独一类是关键：两条有向边同时画 a→b 与 b→a，读图的人会以为存在环，而实际是"谁先谁后自由，但两者不能合并、不能调换"。

---

## 3. 三个必须处理的陷阱（都有反证）

### 陷阱 1：WAR 与反向 RAW 是同一个约束

`fir_filter` 读 `@g_sensor_raw`、`sensor_prepare` 写它。按集合硬算会同时得到：

```
sensor_prepare → fir_filter   [RAW]   @g_sensor_raw
fir_filter     → sensor_prepare [WAR] @g_sensor_raw     ← 假环
```

但在"每周期顺序执行"的语义下，那个读**就是**消费这次写，不是第二条独立约束。
第一次跑示例时就出了这两条边（`data 2 / anti 2`），拓扑序虽然还对，但图上是个环。

**规范**：已有反向 RAW 时不出 WAR。

### 陷阱 2：WAW 无方向

两个阶段都写同一区域 → 不能重排、不能融合，但**谁先谁后是自由的**。
若按有向边同时画 a→b 与 b→a，必然报假环。

**规范**：WAW 出**一条无向 `conflict`**，不参与拓扑排序（DOT 里用 `dir=none`）。

### 陷阱 3：调用者/被调者之间不生成数据边

摘要里调用者的 reads/writes **包含**它所有被调函数的效果（跨过程传播的结果）。
于是 `chain_main` 会"写" `@g_sensor_raw`（因为 `sensor_prepare` 写它），
而 `sensor_prepare` 又"读"它 —— 按区域名硬连边必然双向成环。

**规范**：若 A 传递调用 B，A/B 之间只保留 `call` 边，数据重叠记 `explained_by_call`。

### 反证：`--naive` 关掉全部抑制

只说"我们做了三条抑制"没有说服力，得证明它们**不是空转**：

```
$ dag_build … caller_trap.summary.json --naive --fail-on-cycle
  callee -> caller  [anti] @g_x
  callee -> caller  [data] @g_x
  caller -> callee  [anti] @g_x
  caller -> callee  [call] callee()
  caller -> callee  [data] @g_x
拓扑序:（有环，不可用）      环: callee -> caller -> callee      rc=4
```

同一份数据，正常模式只有 1 条 `call` 边、无环；`--naive` 立刻成环。
这条反证写进了自测（第 3 项），以后改代码若把抑制改坏，用例会红。

---

## 4. 区域名的三级具体化（`arg:N` 不能跨函数硬比）

`arg:N` 是**函数局部**名字：`fir_filter` 的 `arg:0` 与 `pid_step` 的 `arg:0` 是两个不相干的对象。
直接按名字配对会造出大量假边。为此 pass v22 新增 `interface_bindings`：

```json
"interface_bindings": {
  "stage_a": { "0": ["@buf_x"] },              // 唯一调用点、唯一区域
  "stage_b": { "0": ["@buf_x", "@buf_y"] },    // 两个调用点绑到不同对象
  "parse_args": { "1": ["arg:1"] }             // 绑定本身还是相对名
}
```

DAG 生成器据此分三级：

| 级别 | 条件 | 处理 |
|---|---|---|
| **proven** | 唯一调用点、绑到唯一具体区域 | 落地成真名，可连 `data` |
| **alternatives** | 多个调用点绑到不同区域 | 真信息但不确定这一拍走哪条 → `candidate` |
| **unresolved** | 未被调用 / 绑定仍是相对名 / `heap:?` | **零信息**，不造边，节点标 `interface_unresolved` |

第三级最容易搞错。两个函数都带未解析的 `arg:0`，**同名不代表任何别名关系**——
为它造边是纯噪声；但"不造边"也不能被读成"没有依赖"。
所以必须在图上显式标出"接口未闭合"，让读图的人知道**这里的信息是缺失的，不是零**。

> 实现时踩过一个坑：备选区域内部存成 `alt:@buf_x` 以便区分，但比较前忘了剥前缀，
> 结果与已证名永远配不上，候选边被**静默丢弃**。静默丢边比多一条噪声边危险得多。

---

## 5. 实测

### 5.1 示例 `plc_chain`：与人工标注逐条一致

```
$ bash kfusion/scripts/ecos_demo__效果摘要端到端.sh      # 第 6 步
== 边 ==
  sensor_prepare -> fir_filter   [data] @g_sensor_raw
  fir_filter     -> pid_step     [data] @g_fir_out
== 拓扑序 ==
  sensor_prepare -> fir_filter -> pid_step
== 与人工图对照 ==
  人工 2 条 / 编译器 2 条 / 命中 2
  precision 1.000  recall 1.000  f1 1.000
  ✅ 与人工拓扑完全一致
```

![plc_chain DAG](figures/dag/plc_chain_dag.png)

> 图：`kfusion/docs/figures/dag/plc_chain_dag.png`
> 数据：`scripts/test/dag/plc_chain.summary.json`（板上 Pass 的真实输出，v22）
> 画图：`scripts/dag_plot__DAG绘图.py`（纯 matplotlib，不需要 graphviz）

人工图来自 `contracts/examples/plc_chain.stages.json` 的 `depends_on`
（`sensor_prepare → fir_filter → pid_step`）——**编译器是从 IR 把同一张图推出来的**，
不是读那份 json 抄的（`depends_on` 字段只用于对照，不参与构图）。

### 5.2 真实产物 `cyclictest`（13 函数，`-O1 -fno-inline`）

| 指标 | 值 |
|---|---|
| 节点 | 13 |
| 边 | 17（call 11 / data 5 / conflict 1） |
| 候选 | 0 |
| 环 | 无 |
| 区域：唯一绑定 / 多绑定 / 未解析 | 5 / 0 / 8 |
| `interface_bindings` 覆盖的被调函数 | 7 |

拓扑序：

```
main → init_thread_param → parse_args → arg_decimal → check_args_logic
     → print_help → print_hist → print_stat → testthread
     → timediff_us → timediff_us_timer → timerdev_getstatus → tsnorm
```

![cyclictest DAG](figures/dag/cyclictest_dag.png)

> 图：`kfusion/docs/figures/dag/cyclictest_dag.png`（橙色框 = 接口未闭合的节点）
> 数据：`figures/dag/cyc_dag_out.json`　画图：`dag_plot__DAG绘图.py`

**手审两例**（对着源码看，不是只看统计）：

1. `parse_args → check_args_logic [data] @config.11,.12,.13,.7,.9`
   且 `check_args_logic ↔ parse_args [conflict] @config.7,.9`
   —— `parse_args` 解析命令行写 `config`，`check_args_logic` 读它并**回写**部分字段。
   两条边落在**不同字段**上（RAW 在 .11/.12/.13，WAW 在 .7/.9），可以并存。
   这正是"同一对节点可以有多种约束"的例子。

2. `main → testthread [data] @config.*`
   —— `testthread` 不在 `main.calls` 里（它是 `pthread_create` 的函数指针参数，
   **调用图看不见线程入口**）。这条依赖是数据边补回来的：
   `main` 写好 `config` 才创建线程，线程读 `config`。
   这说明**只靠调用图会漏掉跨线程依赖**——也说明数据边不是调用图的冗余装饰。

---

## 6. 环的处理

- `true_cycle`（p 写 `@x` 读 `@y`；q 写 `@y` 读 `@x`）→ 报 `p -> q -> p`，`--fail-on-cycle` 返回 **4**。
- 环**不自动打破**。当前版本只负责"如实报出来"，因为打破环需要语义信息
  （跨周期依赖 / 双缓冲 / 迭代展开），那是下一阶段的事，不能靠图算法猜。

拓扑序在有环时输出"（有环，不可用）"，不给出任何看似可用的顺序。

---

## 7. 明确不主张

1. **区域名只在同一次分析内可比**。`heap:malloc#N` 的 `N` 是模块级单调计数，
   换编译选项、换 module 都会变；跨 module 合并摘要时 heap 名会**假重叠**（尚未处理）。
2. **数据边含传播冗余**。A、B 都调用同一个写者时，会得到 A↔B 的边——方向正确
   （由 A→writer→reader→B 传递可得），但不是最简图。只影响图的可读性，不影响拓扑序。
3. **非流敏感**。只区分"这个函数可能读/写 r"，不区分条件分支；
   一个只在 `if (rare)` 里写的区域，照样算写。
4. **不区分跨周期**。第 k 拍与第 k+1 拍之间的依赖（真正的反馈回路）没有建模，
   所以看起来的"环"未必是同一拍内的环。
5. **`conflict` 只表达"不可重排"**，拓扑序里这两种节点的先后是任意的，
   不能当成执行建议。
6. **未闭合的接口是真缺口**。`cyclictest` 有 5 个节点带 `interface_unresolved`
   （`heap:?` / `arg:0`），它们的跨阶段依赖**未判定**，不是"没有依赖"。

---

## 8. 复现

```bash
# 单跑（Pi4 上）
cd /root/K-Fusion/kfusion
python3 scripts/dag_build__任务DAG生成.py \
    analysis/build/ecos_demo/effect_summary.json \
    --stages contracts/examples/plc_chain.stages.json \
    --expect "sensor_prepare->fir_filter,fir_filter->pid_step" \
    --fail-on-mismatch --dot /tmp/plc_chain.dot

# 全流程（C → IR → 摘要 → 契约 → 门禁 → DAG → 对照）
bash scripts/ecos_demo__效果摘要端到端.sh

# 回归（Windows 下传解释器绝对路径）
bash scripts/test/dag_selftest__自测.sh python3
```

自测覆盖：示例对照、陷阱 1/2/3、`--naive` 反证、真环检出、三级具体化、
退出码契约（3=与人工图不符 / 4=有环）、DOT 产物。

---

## 9. 下一步（PR 4 的输入）

DAG 已经能给出"谁在谁之前"和"谁不能和谁合并"，够 `FusionPlanner` 开始工作了。
单域融合的第一版只需要三个输入：

1. 本文件的 `edges`（顺序约束）+ `conflict`（禁止合并）；
2. 每个节点的 `effects`（能否进目标域）；
3. 每个节点的 `exec` 实测（融合的收益估计）。

仍缺的两块：**跨周期依赖**（决定反馈环能不能合并）与**接口闭合**
（把 `heap:?` / 相对 `arg:N` 变成具体区域，需要调用点敏感的指针分析）。
