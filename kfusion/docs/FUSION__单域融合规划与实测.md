# 单域融合规划与实测（ECoS PR 4 v1）

> 状态：**已实现并实测**（2026-10-23）· Pi4B root cell（HV 开）
> 入口：`kfusion/scripts/fusion_plan__融合规划.py`（规划）+ `fusion_codegen__生成融合代码.py`（生成）
> 测量：`crtos/scripts/pi4/fusion_report__融合对比.py`（对比与归因）
> 自测：`kfusion/scripts/test/fusion_selftest__自测.sh` → **15/15**
> 原始日志：`crtos/results/raw/fusion/run_{plan,flat}.log`
> 上游：`DAG__任务依赖图生成.md`（依赖图）、`PROBE_CONTRACT__统一测量契约.md`（r/s/f）

---

## 1. 为什么 v1 只做单域、只做拍内

两处刻意收窄，都不是偷懒：

1. **单域**：跨域要处理 IPC、共享内存映射、两个域的时钟与调度，v1 先把"同域内该不该合"定死，
   否则失败时分不清是融合算法错还是跨域机制错。
2. **拍内**：DAG 目前**没有跨周期依赖**（`DAG` 文档 §7 已列为缺口）。`pid_step → sensor_prepare`
   这种反馈环在图上根本看不见，如果让规划器跨拍合并，它会基于"图上没有环"这个假前提做决策。
   v1 只合并同一拍的相邻阶段，反馈环留到跨周期建模之后。

---

## 2. 合法性：交点是"不可分"，不是"相邻"

融合的实质是**把几段代码放进同一次释放里连续执行**，也就是不允许别的阶段插在中间。
所以合法条件是：

> 组内任意两个成员之间，不能存在一个**组外**阶段，它在所有合法顺序里都必须排在中间。

三条检查：

| # | 检查 | 不过的例子 |
|---|---|---|
| 1 | **不可分性**：组外节点不得被夹在组内两成员之间 | 组 `{sample, clamp}` 跳过 `fir` → 拒绝，并指出"fir 必须位于其间" |
| 2 | **域合法**：效果并集被平台接受；组的上下文 = 成员交集 | `sensor_prepare` 含 mmio → 内核域拒绝 |
| 3 | **期限**（可选）：组内执行和 ≤ 预算 | `--budget-us` |

### 一个自我纠正

写完才发现：对**连续段**（从拓扑序切出来的区间），检查 1 **恒成立**——任何夹在两成员之间的节点
必然也落在这一区间里。所以对 DP 用到的段，这条是死代码。

它的实际用途在 `--verify`：手工写的方案可能是**非连续**的（`{sample, clamp}` 跳过 `fir`、`pid`），
那种方案才需要它。这条已写成自测用例（第 4 项），否则我会一直以为它"在防守"。

---

## 3. 代价模型：三个量全是实测值，不是拟合参数

串行链上，末段的端到端响应约等于

```
R̂ = G · c + Σ E_g
```

- `G`：组数（= 每周期释放次数）
- `c`：**每次释放的唤醒代价**，用 `--release-cost-us` 传入（root cell ≈21，NuttX ≈6）
- `E_g`：组内执行时间之和，`--exec-us` 传入

`G` 由规划器算，`c` 与 `E` 由测量给。**没有一个是拟合出来的**——
这一点在第 5.4 节用实测数据闭合验证。

---

## 4. 流水线

```
C 源码
  │ clang -O1 -fno-inline          ← 分析链必须禁内联（否则函数被并成几个巨型函数）
  ▼
IR ──plc-effect-summary──▶ summary.json（reads/writes/effects/callsites/thread_entries）
  │
  ▼ dag_build --stages stages.json
dag.json（边 + 拓扑序 + 不可分性所需的可达关系）
  │
  ▼ fusion_plan --platforms caps --release-cost-us 21
plan.json（组划分 + R̂ + 被拒段及原因）
  │
  ▼ fusion_codegen
driver.c（plan：G 次释放/周期；flat：N 次释放/周期；同一份阶段代码）
  │ gcc -O2
  ▼
板上实测 60 s × 2 → probe 契约日志 → fusion_report 归因
```

---

## 5. 实测（四阶段纯计算链，1 kHz，60 s，root cell CPU3 + FIFO 99）

工作负载：`examples/ecos/four_stage_chain__四阶段示例.c`（sample → fir → pid → clamp）。
用它的原因：plc_chain 的 `sensor_prepare` 碰 MMIO，在 `linux_kernel` 域会被门禁正确拒绝，
而同域融合实验需要一个**在该域合法**的负载。

### 5.1 DAG 先过关

编译器从 IR 恢复出三条边，与人工标注**逐条一致**（P/R/F1 = 1.000）：

```
stage_sample -> stage_fir -> stage_pid -> stage_clamp
```

### 5.2 两种释放结构

| 指标 | plan（按方案：1 次/周期） | flat（每阶段：4 次/周期） |
|---|---|---|
| `start_lat` p50 / p99 / max | **20 / 23 / 42.9** | **20 / 23 / 45.8** |
| 单次唤醒 avg | 21.04 | 20.54 |
| **每周期唤醒税** | **21.04 µs** | **82.18 µs** |
| 工作量 `exec` avg | 1.82 | 2.26 |
| 跨度 `span` avg | 1.82 | 599.83 |
| **响应 R** p50 / p99 / max | **22 / 25 / 46.9** | **620 / 621 / 641.9** |
| deadline miss | 0/60000 | 0/60000 |

### 5.3 R 的差不能整笔记在融合头上

flat 的 R 里绝大部分是**相位等待**——我们把第 j 个阶段排到 `r + j·200 µs`，那是布局选择：

```
相位等待 599.8 + 单次唤醒 20.5 + 工作量 2.3 = 622.6  （实测 620.9，差 0.3%）
```

换 STEP 就变的是相位等待；**不随布局变的是唤醒税**：

> 4 次释放 → 1 次，每周期省 3 × 20.54 ≈ **61.1 µs**，占 1 ms 周期的 **6.1%**。

这部分是确定性的（不是统计意义上的），因为每次释放都要走完整的唤醒路径。
注意 `start_lat` 的分布两种模式**几乎相同**（p50 都是 20）——
融合并没有让唤醒更快，只是让你**少付几次**。这个区分很重要，写论文时容易被误述成"降低了唤醒延迟"。

### 5.4 代价模型的闭合验证

把实测值代回 `R̂ = G·c + ΣE`：`1 × 21.04 + 1.82 = 22.86 µs`，实测 R 均值 **22.86 µs**——一致到小数点后两位。
规划器若用 `c=20`（文档里的默认值）则给出 21.8，低 4.6%：**偏差全部来自 c 的取值，不是模型形式错**。

### 5.5 一个意外的次要现象

逐阶段执行时间（µs）：

| 阶段 | plan | flat | 差 |
|---|---|---|---|
| stage_sample | 0.597 | 0.579 | −0.018 |
| stage_fir | 0.667 | 0.663 | −0.004 |
| stage_pid | 0.282 | **0.529** | **+0.247** |
| stage_clamp | 0.275 | **0.493** | **+0.218** |

后两个阶段在 flat 模式下慢了近一倍。最可能的解释是每次释放之间的睡眠把 i-cache/数据缓存冲掉了
（plan 模式四段背靠背，缓存是热的）。这条**只是观察**，没有单独做实验证实，
但值得记下：**同一份工作在不同释放结构下的执行时间也不是常数**。
它没有影响主结论（61 µs 的税远大于 0.46 µs 的缓存效应）。

---

## 6. 三个坑（都会让实验静默失效）

### 坑 1：输出不可观测 → 整段被编译器删掉

第一版示例把状态写成 `static`，结果 `stage_clamp` 的 IR 是 **`ret void`**，`@g_act`/`@g_out`
两个全局直接消失——因为没人读最终输出，整条尾链是死代码。于是"性能实验"在测一个空函数。

典型症状：DAG 少一条边、`stage_clamp` 被判成 `pure`。**分析器没错**，它如实报告了"这段没有效果"。
修法：模块级状态改为**外部链接**（可被其他单元观察），示例里已加注释说明原因。
同一个问题也让 `plc_chain` 的 `g_actuator` 被删过，一并修了。

### 坑 2：直方图桶顶太小 → p50 变成假值

`flat` 模式的 R ≈ 620 µs，而探针默认桶只到 199 µs，全部样本落进溢出桶，
`p50` 直接输出 **199**——那不是数据，是夹具的错。修法：`PLC_PROBE_BUCKETS` 改为可覆盖，
驱动里 `#define PLC_PROBE_BUCKETS 1024` 后再 include 探针头。

### 坑 3：`exec` 口径被"空等"污染

最初把 `exec` 记成 `f_last − s_first`。在 flat 模式下这会把 3 段睡眠（600 µs）算成执行时间，
得出"融合让执行时间从 600 降到 1.8 µs"的荒谬结论。修法：拆成三个量——

| 序列 | 定义 | 用途 |
|---|---|---|
| `exec` | 各阶段执行时间之**和** | 真实工作量（两模式应对得上）|
| `span` | 首尾跨度（含阶段间空等） | 看得见空等 |
| `response` | 末次完成 − 首次标称释放 | 端到端 R |

---

## 7. 明确不主张

1. **不主张"融合把 R 降低了 27×"**。27× 里 599.8 µs 是相位等待，是布局选择；
   可归因于融合的是 61.1 µs（6.1%）。
2. **不主张 CPU 占用下降**。工作量没变（1.82 vs 2.26 µs，flat 反而略高）。
   融合省的是唤醒路径，不是计算。
3. **不主张可扩展到任意任务数**。v1 只做单域拍内的连续段合并，未处理跨周期反馈。
4. **`c` 是外部输入**。规划器不自己测唤醒代价；换域（NuttX、不同内核）必须重新给 `c`。
5. **两个模式不是"部署形态"**。flat 用同一线程里的多次释放代表"每阶段一个任务"，
   隔离了释放代价，但**没有**建模多任务之间的 IPC、优先级和并发；真实多任务部署的差异会更大。
6. **不主张"融合总是更好"**。融合牺牲的是重排自由度与隔离性；当某阶段有独立期限、
   或需要跑在别的核上时，不该合并——v1 的合法性检查只覆盖"能不能合"，没覆盖"该不该合"。

---

## 8. 复现

```bash
# 1) 规划 + 生成（Pi4，先跑过工程图那一套）
python3 scripts/fusion_plan__融合规划.py /root/pr4/dag.json \
    --platforms contracts/examples/platform_caps.json --platform linux_kernel \
    --release-cost-us 21 -o /root/pr4/plan.json

python3 scripts/fusion_codegen__生成融合代码.py /root/pr4/plan.json -o /root/pr4/driver.c \
    --stage-source "$PWD/examples/ecos/four_stage_chain__四阶段示例.c" \
    --call "stage_pid=stage_pid(1000)" --period-us 1000 --step-us 200 --cycles 60000

gcc -O2 -I ../crtos/jailhouse_overlay_pi4/nuttx/apps/benchmarks/bm2pid \
    -o /root/pr4/driver /root/pr4/driver.c -lm

# 2) 两种结构各跑 60 s（root cell，绑核 + FIFO）
chrt -f 99 taskset -c 3 /root/pr4/driver plan 60000 > /root/pr4/run_plan.log
chrt -f 99 taskset -c 3 /root/pr4/driver flat 60000 > /root/pr4/run_flat.log

# 3) 归因（本地）
python crtos/scripts/pi4/fusion_report__融合对比.py run_plan.log run_flat.log
```

自测：`bash kfusion/scripts/test/fusion_selftest__自测.sh python3`（15 项）。

---

## 9. 下一步

- **跨周期依赖**：没有它，"反馈环能不能合并"无从判断，v1 只能做拍内。这是最大的一块。
- **多任务真部署**：把 flat 模式换成真正的 N 个线程（含 IPC），看融合省下的
  是否真的等于 61 µs，还是被 IPC 抵掉/放大。
- **该不该合**：现在只判"能不能合"。加一条收益判断（例如某阶段 deadline 很紧就不并入长组）。
- **NuttX 侧同实验**：`c≈6 µs` 时税只有 3×6=18 µs，可用来验证"融合收益与 c 成正比"这一预测。
