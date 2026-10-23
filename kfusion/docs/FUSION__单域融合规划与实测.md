# 单域融合规划与实测（ECoS PR 4 v1 / v2 跨周期）

> 状态：v1 **已实测**（2026-10-23，Pi4B root cell，HV 开）· v2 跨周期**已实现、待板上实测**
> 入口：`kfusion/scripts/fusion_plan__融合规划.py`（规划）+ `fusion_codegen__生成融合代码.py`（生成）
> 测量：`crtos/scripts/pi4/fusion_report__融合对比.py`（对比与归因）
> 自测：`kfusion/scripts/test/fusion_selftest__自测.sh` → **35/35**
> 原始日志：`crtos/results/raw/fusion/run_{plan,flat}.log`
> 上游：`DAG__任务依赖图生成.md`（依赖图）、`PROBE_CONTRACT__统一测量契约.md`（r/s/f）

---

## 1. 为什么 v1 只做单域、只做拍内（v2 已解掉"拍内"这一半）

两处刻意收窄，都不是偷懒：

1. **单域**：跨域要处理 IPC、共享内存映射、两个域的时钟与调度，v1 先把"同域内该不该合"定死，
   否则失败时分不清是融合算法错还是跨域机制错。**仍然单域。**
2. **拍内**：v1 时 DAG 没有跨周期依赖，`pid_step → sensor_prepare` 这种反馈环在图上根本看不见，
   让规划器跨拍合并会基于"图上没有环"这个假前提做决策。**v2 已补**（§2.2 + `DAG` 文档 §6）：
   相位反向的边被识别为 z⁻¹，反馈环从此可见、可判、可合。

---

## 2. 合法性：交点是"不可分"，不是"相邻"

融合的实质是**把几段代码放进同一次释放里连续执行**，也就是不允许别的阶段插在中间。
所以合法条件是：

> 组内任意两个成员之间，不能存在一个**组外**阶段，它在所有合法顺序里都必须排在中间。

四条检查：

| # | 检查 | 不过的例子 |
|---|---|---|
| 1 | **不可分性**：组外节点不得被夹在组内两成员之间 | 组 `{sample, clamp}` 跳过 `fir` → 拒绝，并指出"fir 必须位于其间" |
| 2 | **域合法**：效果并集被平台接受；组的上下文 = 成员交集 | `sensor_prepare` 含 mmio → 内核域拒绝 |
| 3 | **相位**：读外部输入（`mmio`）的阶段不能被前拉 | `b`@200µs 读 MMIO，并入 `a`@0 的头组 → 拒绝："等于提前采样" |
| 4 | **期限 / 单拍容量**：组内执行和 ≤ 预算，且 ≤ 周期 | `--budget-us`；超过周期 = 永远跑不完 |

### 2.1 一个自我纠正

写完才发现：对**连续段**（从拓扑序切出来的区间），检查 1 **恒成立**——任何夹在两成员之间的节点
必然也落在这一区间里。所以对 DP 用到的段，这条是死代码。

它的实际用途在 `--verify`：手工写的方案可能是**非连续**的（`{sample, clamp}` 跳过 `fir`、`pid`），
那种方案才需要它。这条已写成自测用例（第 4 项），否则我会一直以为它"在防守"。

### 2.2 反馈环：从"一律拒绝"到"看闭合边是不是相位反向"

环的合法性不取决于"有没有环"，只取决于**闭合边跨不跨拍**：

| | 判据 | 结论 |
|---|---|---|
| 延迟反馈环 | 环内存在边 `u→v` 且相位 `u` > 相位 `v` | 该边一拍内不可能满足 ⇒ 读上一拍 ⇒ 可实现，**延迟由调度强制** |
| 代数环 | 环内每条边相位都不晚于消费者 | 一拍内首尾相接 ⇒ 无合法执行顺序 ⇒ **拒绝**（rc=2） |
| 相位未知 | 函数视图 / 骨架没给相位 | 按代数环处理（fail-closed） |

对强连通分量，检查 1 自动把整个环锁成一组：**环只能整体成组，不能切开**。
这不是额外规则，是不可分性在 SCC 上的推论——也正是真实系统的做法（一个控制回路 = 一个周期体）。

### 2.3 融合对反馈环做了什么：采样序列不变，死区时间变了

这是本轮最要紧的一条，也是最容易说错的一条。

`five_stage_loop`（sample@0 → fir@200 → pid@400 → clamp@600 → plant@800 → 下一拍 sample）：
`plant` 在 800 µs 写 `g_plant`，`sample` 在下一拍 0 µs 读它，未融合时间隔 **200 µs**。

五阶段并成一组后整组在 0 µs 释放，`plant` 被前拉到组尾执行
（`c=21` + 前四段执行 ≈22.8 µs），闭合边间隔变成 **998 µs（×4.99）**。
读到的值**仍然是 `plant` 上一拍的同一个值**——所以采样序列**逐位不变**（代码生成里用摘要校验，
见 §4.2）；但环的**物理死区时间从 200 µs 变成一整拍**。

对慢回路这通常无害（甚至相当于缩短了执行延迟），对接近采样极限的回路会直接改变相位裕度。
所以规划器把这一项**单独报出来**（`recurrences[].closing_edges[].inflation`），
并给一个门禁开关：`--max-delay-inflation 2 --fail-on-warning`。这就是"该不该合"的第一条量化判据。

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
dag.json（边 + 相位 + 拓扑序/相位序 + recurrences / algebraic_loops）
  │
  ▼ fusion_plan --platforms caps --release-cost-us 21
plan.json（组划分 + R̂ + 反馈环落位与死区变化 + 被拒段及原因）
  │
  ▼ fusion_codegen
driver.c（plan：G 次释放/周期；flat：N 次释放/周期；同一份阶段代码）
  │ gcc -O2
  ▼
板上实测 60 s × 2 → probe 契约日志 → fusion_report 归因
```

### 4.1 代码生成的两道守卫（都会静默毁掉实验）

| 守卫 | 不放行的情况 | 为什么必须拦 |
|---|---|---|
| `--expect-order` | plan 展平后的执行顺序 ≠ 给的相位序 | 顺序一旦变了，环闭合边间隔不再 ≥ 一拍，**采样序列会静默地变** |
| `N-1 × step ≤ period` | 相位铺不下 | flat 模式的末次释放跨到下一拍，与下一拍首次释放撞车，`start_lat` 不再是释放代价 |

### 4.2 跨周期等价的判据：摘要

`--digest-var g_out --digest-var g_plant` 让驱动每周期结束把这些全局量揉进 FNV-1a，
打印 `# digest: %08x`。两模式的阶段代码、初始值、执行顺序完全相同，唯一差别是释放次数，
所以**摘要必须逐位相同**；不同就说明融合改变了数据流，而不只是改变了唤醒路径。
这比"看起来对"强：它是可机检的等价性证据。

> 板侧实测入口已就绪，但本轮**未跑**（Pi 离线）——见 §9 的待办。

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
3. **不主张"融合对反馈环没有影响"**。采样序列确实逐位不变，但环闭合边的间隔会从
   `period − (phase_prod − phase_cons)` 被拉到约一整拍（实测口径见 §2.3 的 ×4.99）。
   这是**死区时间的变化**，对控制律的连续域含义是真变化。
4. **不主张可扩展到任意任务数**。v2 已能处理反馈环，但仍是单域、仍是"一个周期体一条串行链"；
   分支/汇合图上只能保守地拒绝（不可分性）。
5. **`c` 是外部输入**。规划器不自己测唤醒代价；换域（NuttX、不同内核）必须重新给 `c`。
6. **两个模式不是"部署形态"**。flat 用同一线程里的多次释放代表"每阶段一个任务"，
   隔离了释放代价，但**没有**建模多任务之间的 IPC、优先级和并发；真实多任务部署的差异会更大。
7. **不主张"融合总是更好"**。v2 让步了一步：现在能报"能合"，也能量化两条"不该合"的理由
   （相位钉住的阶段被前拉 = 提前采样；死区放大超过给定倍数），但仍**没有**收益/代价的
   统一货币——释放税省了多少 µs 是硬的，死区变化值不值是控制工程师的判断。

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

自测：`bash kfusion/scripts/test/fusion_selftest__自测.sh python3`（35 项）。

### 8.1 闭环（跨周期）待跑清单

```bash
# a) 摘要：用 EffectSummaryAnalysis 分析五阶段示例（Pi 上）
clang -O1 -fno-inline -emit-llvm -c examples/ecos/five_stage_loop__闭环五阶段示例.c -o /root/pr5/loop.ll
opt -load .../EffectSummary.so -effect-summary /root/pr5/loop.ll -o /dev/null 2> /root/pr5/summary.json

# b) DAG：必须报出 1 个延迟反馈环（plant→sample，间隔 200 µs）
python3 scripts/dag_build__任务DAG生成.py /root/pr5/summary.json \
    --stages contracts/examples/five_stage_loop.stages.json --fail-on-algebraic \
    --dot /root/pr5/loop.dot -o /root/pr5/dag.json

# c) 规划 + 生成（带摘要，跨周期等价的判据）
python3 scripts/fusion_plan__融合规划.py /root/pr5/dag.json \
    --platforms contracts/examples/platform_caps.json --platform linux_kernel \
    --release-cost-us 21 --exec-us /root/pr5/exec.json \
    --max-delay-inflation 8 -o /root/pr5/plan.json

python3 scripts/fusion_codegen__生成融合代码.py /root/pr5/plan.json -o /root/pr5/driver.c \
    --stage-source "$PWD/examples/ecos/five_stage_loop__闭环五阶段示例.c" \
    --call "stage_pid=stage_pid(1000)" --period-us 1000 --step-us 200 --cycles 60000 \
    --digest-var g_out --digest-var g_plant \
    --expect-order "stage_sample,stage_fir,stage_pid,stage_clamp,stage_plant"

# d) 两模式各 60 s；**先比 digest，再比时间**
chrt -f 99 taskset -c 3 /root/pr5/driver plan 60000 > /root/pr5/run_plan.log
chrt -f 99 taskset -c 3 /root/pr5/driver flat 60000 > /root/pr5/run_flat.log
grep '# digest' /root/pr5/run_plan.log /root/pr5/run_flat.log   # 必须相同
```

【待填实测值】闭环 plan/flat 的 `start_lat`、`R` 分位数与唤醒税——
预期 wake tax 为 `1×c` vs `5×c`（省 4c ≈ 84 µs），但 **R 的比较必须扣除相位等待**（同 §5.3 口径）。

---

## 9. 下一步

- ~~**跨周期依赖**~~：**已做**（`DAG` 文档 §6 + 本文 §2.2/§2.3）。判据是相位反向边，
  不猜；代数环仍 fail-closed。剩下的实测见 §8.1。
- **多任务真部署**：把 flat 模式换成真正的 N 个线程（含 IPC），看融合省下的
  是否真的等于 61 µs，还是被 IPC 抵掉/放大。
- **该不该合（收益侧）**：现在能拒（相位钉住 / 死区放大），还不能**算**"值不值"。
  需要把死区变化换成控制代价（例如相位裕度损失），这是跨学科的一步。
- **NuttX 侧同实验**：`c≈6 µs` 时税只有 3×6=18 µs，可用来验证"融合收益与 c 成正比"这一预测。
