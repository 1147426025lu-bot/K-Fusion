# 单域融合规划与实测（ECoS PR 4 v1 / v2 跨周期）

> 状态：v1（拍内）与 v2（跨周期）**均已实测**；§5.7 NuttX 侧、§5.8 多任务真部署 **已实测**
> （2026-10-23 / 10-24）· 四轮全部 1 kHz × 60 s × 2，root cell 均 CPU3+FIFO99、**wlan0 down**；
> NuttX 侧跑在非根 cell（CPU1），经 GPIO14/15 串口采集
> 入口：`kfusion/scripts/fusion_plan__融合规划.py`（规划）+ `fusion_codegen__生成融合代码.py`（生成）
> 测量：`crtos/scripts/pi4/fusion_report__融合对比.py`（对比与归因）
> 自测：`kfusion/scripts/test/fusion_selftest__自测.sh` → **52/52**（原 37 + PR6/7/8 新增 15）
> 原始日志：`crtos/results/raw/fusion/`（四阶段）、`fusion_loop/`（闭环五阶段，root cell）、
> `fusion_loop_nuttx/`（NuttX cell，串口）、`fusion_multitask/`（plan/flat/multi 三方）
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

> 板上核对（§5.6）：模型说 200 → 998 µs，实测派生值 199.3 → 997.9 µs（×5.01），差 0.7 µs 内。

---

## 3. 代价模型：两个量全是实测值，不是拟合参数

规划器现在区分**两件不同的事**（PR7 修正此前只建模唤醒税的缺口）：

- **唤醒税累计** `= G·c`：每周期释放 `G` 次、每次付唤醒代价 `c` 的累计开销。
  这是融合能砍掉的部分——`(N−1)·c`。
- **端到端响应 `R`**：从第一组释放相位到最后一组完成。融合把分散在各相位的阶段
  拉进同一次释放，相位跨度被坍缩，于是 `R` 大幅缩短。

串行单资源下，每组在「组内头相位」一次性释放、组内连续执行；组释放时刻
`= max(头相位, 上一组完成)`。端到端

```
R = 最后一组完成 − 第一组释放相位
```

当所有相位相等（头相位全 0）时退化为旧模型 `R̂ = G·c + ΣE`；当相位不同
（如闭环五阶段 0/200/400/600/800）时，`R` 才如实显出相位跨度——
全融合 `R≈23 µs`，不融合 `R≈821 µs`，**差的就是 798 µs 的相位坍缩**，而旧的
`G·c+ΣE` 只会算成 107 µs，漏掉了这部分。

参数：`G` 由规划器算；`c`（每次释放唤醒代价，`--release-cost-us`，root cell≈21、
NuttX≈6）与 `E_g`（组内执行，`--exec-us`）由测量给。**没有一个是拟合出来的**——
这一点在第 5.4 节用实测数据闭合验证。

> 规划器上报时把二者都打出来：`释放税(累计) G×c` 与 `R̂≈R（端到端）`，
> 并显式拆出 `相位跨度 ＋ 末组唤醒 ＋ 末组执行`，避免再把两种收益混成一种。

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

实测：5 s 冒烟 `f82b5f12` = `f82b5f12`；60 s 正式跑 `b6eb20b8` = `b6eb20b8`。
（两次摘要不同是正常的——周期数不同，摘要覆盖的样本序列长度就不同；**同一轮的两模式**必须相同。）

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

### 5.6 闭环五阶段（跨周期，2026-10-23 补测）

前面 5.1–5.5 是**无环**的四阶段链。这一节把环闭上（`plant` 写 `g_plant`，`sample` 下一拍读），
检验 v2 的跨周期部分在板上是否真的成立。

条件：Pi4B root cell（HV 开）、1 kHz、60 s × 2、CPU3 + FIFO 99、**wlan0 down**（eth0 = 192.168.1.3）。
日志：`crtos/results/raw/fusion_loop/run_{plan,flat}.log`。

**第一关：DAG 由真实 IR 摘要恢复**（不是手写固件）

```text
clang-16 -S -emit-llvm -O2 -fno-inline -fno-inline-functions
opt-16 -load-pass-plugin PLCEffectSummary.so -passes=plc-effect-summary
→ dag_build --stages five_stage_loop.stages.json --fail-on-algebraic
```

5 条边与人工标注**逐条一致**（P/R/F1 = 1.000），并报出 1 个延迟反馈环：
`stage_plant→stage_sample`，相位 800→0，闭合间隔 200 µs。**代数环 0 个。**

| 指标 | plan（1 次释放/周期） | flat（5 次释放/周期） |
|---|---|---|
| `start_lat` p50 / p99 / p99.99 / max | 20 / 23 / 37 / 42.5 | 20 / 23 / 37 / 44.6 |
| 单次唤醒 avg | 20.79 | 20.64 |
| **每周期唤醒税** | **20.79 µs** | **103.20 µs** |
| 工作量 `exec` avg | 2.14 | 3.08 |
| 跨度 `span` avg | 2.14 | 800.69 |
| **响应 R** p50 / avg / p99 / max | 22 / **22.93** / 25 / 45.6 | 821 / **821.12** / 821 / 839.7 |
| deadline miss | 0/60000 | 0/60000 |
| **采样序列摘要** | `b6eb20b8` | `b6eb20b8` |

三条结论，全部可机检：

1. **数据流没变**：两个模式的 FNV-1a 摘要**逐位相同**（`b6eb20b8`）。
   阶段代码、初值、执行顺序都相同，唯一差别是释放次数——摘要相同即采样序列相同。
   这比"看起来对"强，它是跨周期语义保持的证据而不是论证。
2. **省 (N−1)·c 精确成立**：唤醒税 103.20 → 20.79，**省 82.42 µs/周期（占 8.2% 周期）**；
   `(5−1)×20.64 = 82.56`，差 0.14 µs。
3. **代价模型闭合**：`R̂ = 1×20.79 + 2.14 = 22.93` = 实测 R 均值 **22.929**（差 0.001 µs）。
   四阶段那次是 22.86，两次都闭合到 0.01 µs 以内——说明"释放税 + 工作量"这个两参数
   模型在本负载上够用，多出来的不是拟合项。

**死区时间的变化也被数据证实了**（派生量，不是直接探针）：
flat 的 `span` avg = 800.69 µs 说明 `plant` 确实在 800 µs 才执行，故下一拍 `sample` 读到它时
间隔 ≈ `1000 − 800.69 = 199.3 µs`；plan 的 `span` = 2.14 µs，间隔 ≈ `1000 − 2.14 = 997.9 µs`。
**199.3 → 997.9 µs（×5.01）**，与模型预测的 200 → 998 在 0.7 µs 内一致。

次要现象沿用 §5.5 的解释：flat 的逐阶段执行时间普遍偏高（0.36→0.59、0.32→0.62、0.29→0.57），
总量 2.14 → 3.08（+44%）。每次释放带来的冷启动把后段拖慢了——**这是融合的附带收益，不是代价**，
但它说明"工作量"本身也随释放结构变，引用 exec 时要带上释放结构。

---

---

### 5.7 NuttX 侧同实验：收益 ∝ c（2026-10-24）

预测：「融合收益 = (N−1)·c」⇒ 换到 `c≈6 µs` 的 NuttX 域，省的应该是 `4×6 ≈ 24 µs`
（Linux root cell 是 82.42）。这一节就是在 NuttX 非根 cell（CPU1）里跑同一份闭环负载。

**产物链**：`fusion_codegen --entry fiveloop_main` → 编成 NuttX builtin
（`apps/benchmarks/fiveloop/`，Kconfig + Make.defs + Makefile 三件套，_probe 头与 bm2pid 同源）
→ nuttx.bin **376832 → 454656 B** → Jailhouse cell 加载 → NSH 串口跑。
图像里 `strings` 可见 `fiveloop`。结果经 GPIO14/15 串口（115200 8N1）采集。

| 指标 | plan（1 次/周期） | flat（5 次/周期） |
|---|---|---|
| `start_lat` avg / max | **5.583 / 6.556** | 5.636 / 10.203（n=300000） |
| 工作量 `exec` avg | 1.792 | 3.149 |
| 响应 R avg / p50 / max | **7.374 / 7 / 9.167** | 806.203 / 806 / 806.870 |
| deadline miss | 0/60000 | 0/60000 |
| 唤醒税 | 5.58 | 5×5.64 = **28.18** |
| 采样摘要（全新内存） | `b6eb20b8` | `b6eb20b8` |

- **收益 ∝ c 成立**：省 `28.18 − 5.58 = 22.60 µs`，预测 `(N−1)·c = 4×5.636 = 22.54`；
  收益比 82.42/22.60 = **3.65**，`c` 比 20.64/5.636 = **3.66**——两个域的融合收益
  只差唤醒代价本身，不多不少。
- **代价模型第三次闭合**：`R̂ = 5.583 + 1.792 = 7.375` vs 实测 7.374。
- **摘要跨域一致**：Linux plan/flat 与 NuttX plan/flat 四个"全新起点"的摘要**全部是
  `b6eb20b8`**——同一份数据流，两个 OS、两种释放结构，逐位相同。
- 抖动量级与 BM2 一致：plan 的 R 只有 3 个非零桶（7/8/9 µs）。

**⚠ 方法论坑（会伪造"摘要不一致"）**：NuttX builtin app 在**同一次开机内共享全局状态**——
第二次调用继承上一次的 `g_seed`/`g_plant`/`g_integ`/`g_hist`。实测：无论第二跑是哪个模式，
摘要都是 `47649d0c`（收敛轨道相同 ⇒ 续流相同）；第一跑（全新内存）才是 `b6eb20b8`。
所以 NuttX 上做摘要等价检查，**必须每种模式重建 cell**（`cell destroy` + 重部署 = 重载镜像
= 全新 .data），或者固定"只看第一跑"。Linux 无此问题（每次运行是新进程）。

### 5.8 多任务真部署（multi）：融合收益在真线程下保持（2026-10-24）

§5.2–5.6 的 flat 是**同一线程里的多次释放**——它隔离了释放代价，但没建模并发。
`--threads` 生成第三种模式：**每阶段一个真线程**（pthread），按各自相位
`clock_nanosleep(ABSTIME)` 释放，FIFO 优先级按相位递减（早相 = 高优先，90−idx），
进程用 `taskset -c 3` 启动 ⇒ 五个线程同核，隔离的是"线程化 + 调度器参与"而非核数。

| 指标 | plan（1 次释放） | flat（1 线程 5 次释放） | multi（5 真线程） |
|---|---|---|---|
| 每周期唤醒税 | 21.21 | 102.85 | **104.55** |
| 响应 R avg / max | 23.29 / 62.9 | 820.89 / 880.5 | 821.24 / 845.1 |
| deadline miss | 0 | 0 | 0 |
| 采样摘要 | `b6eb20b8` | `b6eb20b8` | `b6eb20b8` |

- **真线程的唤醒代价 ≈ 同线程睡眠**：104.55 vs 102.85（+1.7%）。
  五个 FIFO 线程各自挂自己的绝对时限定时器，唤醒路径与同线程反复睡/醒没有本质差别。
- **融合收益在真部署下保持**：multi 相对 plan 省 `104.55 − 21.21 = 83.34 µs/周期`
  （flat 口径 81.64），与 (N−1)·c 一致——**"省下的 82 µs 会被调度器/IPC 抵掉"这个担忧
  在同核、共享地址空间的部署形态下没有发生**。
- **摘要三模式逐位相同**：融合/线程化只改变了"谁在什么时刻被唤醒"，
  数据流一个比特都没动。
- 诚实的边界：这里的"IPC"是**同进程共享全局**（`extern` 变量），没有消息传递、没有锁、
  没有跨核。跨核或跨域部署要重新测（那正是 ECoS 的下一步）。

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

自测：`bash kfusion/scripts/test/fusion_selftest__自测.sh python3`（52 项）。

### 8.1 闭环（跨周期）实测：已于 2026-10-23 18:58 跑完

结果见 §5.6，原始日志 `crtos/results/raw/fusion_loop/run_{plan,flat}.log`。
可复跑的命令如下（Pi4，eth0 = 192.168.1.3，先 `ip link set wlan0 down`）：

```bash
# a) 摘要：效果摘要由编译器从 IR 推出（禁内联，否则函数被合并）
clang-16 -S -emit-llvm -O2 -fno-inline -fno-inline-functions -fno-builtin \
    kfusion/examples/ecos/five_stage_loop__闭环五阶段示例.c -o /root/pr5/loop.ll
PLC_EFFECT_SUMMARY_OUT=/root/pr5/summary.json \
    opt-16 -load-pass-plugin kfusion/analysis/build/PLCEffectSummary.so \
    -passes=plc-effect-summary /root/pr5/loop.ll -S -o /dev/null

# b) DAG：必须报出 1 个延迟反馈环（plant→sample，间隔 200 µs），代数环 0 个
python3 kfusion/scripts/dag_build__任务DAG生成.py /root/pr5/summary.json \
    --stages kfusion/contracts/examples/five_stage_loop.stages.json \
    --fail-on-algebraic --dot /root/pr5/loop.dot -o /root/pr5/dag.json

# c) 规划 + 生成（摘要 + 顺序守卫）
python3 kfusion/scripts/fusion_plan__融合规划.py /root/pr5/dag.json \
    --platforms kfusion/contracts/examples/platform_caps.json --platform linux_kernel \
    --release-cost-us 21 --exec-us /root/pr5/exec.json \
    --max-delay-inflation 8 -o /root/pr5/plan.json

python3 kfusion/scripts/fusion_codegen__生成融合代码.py /root/pr5/plan.json -o /root/pr5/driver.c \
    --stage-source "$PWD/kfusion/examples/ecos/five_stage_loop__闭环五阶段示例.c" \
    --call "stage_pid=stage_pid(1000)" --period-us 1000 --step-us 200 --cycles 60000 \
    --digest-var g_out --digest-var g_plant \
    --expect-order "stage_sample,stage_fir,stage_pid,stage_clamp,stage_plant"

gcc -O2 -Wall -o /root/pr5/driver /root/pr5/driver.c -lm

# d) 两模式各 60 s；**先比 digest，再比时间**
chrt -f 99 taskset -c 3 /root/pr5/driver plan 60000 > /root/pr5/run_plan.log
chrt -f 99 taskset -c 3 /root/pr5/driver flat 60000 > /root/pr5/run_flat.log
grep '# digest' /root/pr5/run_plan.log /root/pr5/run_flat.log   # 必须相同

# e) 归因（本地）
python crtos/scripts/pi4/fusion_report__融合对比.py run_plan.log run_flat.log
```

---

## 5.9 规划器升级：从"可运行实验规划器"到"可相信的约束优化器"（PR6/7/8）

针对外部审查的三个 P0，本版把单域规划器补齐为约束优化器（自测 #15–#18 覆盖）：

### PR6 — DAG 不确定性门禁（fail-closed）
规划器过去完全忽略 DAG 里的 `conflict`（WAW 无向）、`candidate`（未证明依赖）、
`interface_unresolved`（接口未闭合）。现在 `group_legal` 把它们纳入合法性：
- 段内含 `interface_unresolved` 节点 → 域合法性不可信，**直接拒绝**（效果/上下文可能不完整）。
- 段内含 `conflict` 或 `candidate` 且其两端**无已证明可达顺序** → 顺序不确定，
  **拒绝**（保序融合不能保证 happens-before）。两端已有已证明顺序时则放行（顺序已定）。
- `uncertainty_report` 把这三类的存在与"是否真的挡住了某段"写进 plan.json。
- 关键性质：规划器本就只做拓扑序的连续段融合、从不重排，所以"禁止重排"天然满足；
  PR6 补的是"未证明但真实的依赖被违反时，至少报错/拒绝，而不是静默放过"。

### PR7 — 释放相位端到端响应模型
旧模型 `R̂ = G·c + ΣE` 只能建模唤醒税，**看不见**相位跨度坍缩（闭环五阶段里那 798 µs）。
新模型（§3）按"每组在头相位一次性释放、组内连续执行、组间按相位等待"算真正端到端 `R`；
相位未知时退化为 `G·c+ΣE`。DP 目标函数改为最小化 `R`，相位相同时与旧结果一致
（五阶段仍省 82.42 µs），相位不同时如实给出 821 µs 那部分收益。

### PR8 — 控制语义硬约束（默认硬，不靠 --fail-on-warning）
- 新增 `--max-feedback-delay-us`（绝对死区上限）：超过则**直接拒绝方案**，不是 warning。
  ≤ period 时每条反馈闭合边都必须跨组（否则死区≈一整拍超限）。
- 新增每阶段 `sample_window_us` / `output_window_us`：采样/输出被拉出允许窗 → 拒绝。
  与既有的 `phase_pinned`（禁止提前采样）一起，把"采样瞬间/输出生效"作为正约束守住。
- 反馈硬约束使"端到端可行"成了全局耦合条件，前缀 DP 表达不了，故在该模式下退化为
  小规模穷举连续划分（N 小，精确），保证找到真正可行的边界。

## 5.10 反例 benchmark：融合得越多不一定越好

审查要求构造一个"不能全融合"的控制任务，对比 全融合 / 不融合 / 贪心 / ECoS。
本版给出两类证据：

1. **闭环五阶段 + 反馈死区上限 500 µs**（`loop_fb_limit.dag.json`）：全融合会把死区拉到
   ≈998 µs 超限，ECoS **拒绝全融合**，退回到不融合（5 组各自释放，R≈821 µs，反馈死区保持
   200 µs）。注意：紧周期环是 SCC，"不可分性"规则本就只允许全融合或不融合二选一，这里表现为
   all-or-nothing 地"拒绝非法全融合"。
2. **非环 4 段链、中间段相位钉住**（`chain_pin.dag.json`）：全融合把 s3 提前 400 µs 采样
   （= 提前采样，破坏物理对齐），被拒；ECoS **自动选 2 组**（`s1+s2 | s3+s4`），R≈421.8 µs，
   而非无脑全融合或全拆——这正是"算法自动选对有价值的融合边界"。

> 边界选择要真正有区分度，需要任务含可分离的子结构（多环 / 并行分支）。紧周期单环只能
> all-or-nothing，这一点已如实说明，列为后续 benchmark 的扩展方向。

---

## 5.11 第二轮审查的三个 P0：全部复现、全部修掉（2026-10-24 晚）

第二轮外部审查（针对 `95293b8`）提了三个 P0。**三个都先复现再改**，没有按文字描述直接改。

### P0-1：`--verify` 不是全局验证器（真漏洞，已复现）

把 `four_stage` 的拓扑序**完全倒过来**写成 4 个单节点组：

```
拓扑序：stage_sample → stage_fir → stage_pid → stage_clamp
倒序方案：stage_clamp | stage_pid | stage_fir | stage_sample
```

旧 `--verify` 输出 **4 个 ✅、exit=0**——先算执行器输出、最后才采样的程序被判"完全合法"。
原因：它只做「逐组 `group_legal()` + 成员覆盖」，而 `group_legal` 是**局部谓词**，
天生看不见 A 组与 B 组谁先谁后。加重情节：`fusion_codegen` 直接吃 `plan.json`，
grep 不到任何验证调用——漏洞可以一路走到产物。

修复（PR9-A）：

- 新增 `kfusion/scripts/ecos/plan_verify.py`：`PlanVerifier` **不调用** `group_legal()`，
  自己从 DAG 重建拓扑序、同拍边、跨周期闭合边、冲突、未证依赖、域能力，再对
  **整份方案**做四类校验（完整性 / 执行顺序 / 合法性 / 时间语义）。
- 跨周期边（`recurrences[].closing_edges`，z⁻¹）显式排除在同拍顺序之外，否则
  `plant@800 → sample@0` 会被误判越序。
- 验证器同时检查**不可分性**（组内两人之间不得夹着组外节点）与**组间越序**，
  两个都报，不互相掩盖。
- 求解器自己的输出也必须过同一个验证器，结果写进 `plan.verification`；
  不过就 `rc=3` 并说明"求解器与验证器语义已分叉"。
- `fusion_codegen` 新增 `--dag`（生成前现场复检）与 `--require-verified`；
  拒绝时不留下任何产物文件。

修复后同一份倒序方案：

```
❌ [EDGE_ORDER] 依赖越序：stage_pid→stage_clamp（stage_pid@第1位 未早于 stage_clamp@第0位），证据 @g_act
❌ [EDGE_ORDER] 依赖越序：stage_sample→stage_fir（…），证据 @g_raw
rc=3
```

### P0-2：控制窗口是"相对漂移"，不是绝对时刻（真缺口）

旧实现对每个成员算 `off = c + Σ前序执行`，判 `off > sample_window_us`。
`off` 是**相对组释放**的偏移，所以：

- 只能发现"被推后"；
- **发现不了"被前拉"**——而融合把后段前拉到头相位，主要语义风险恰恰是前拉（提前采样）。

修复（PR9-B，时间模型 v4）：新增 `kfusion/scripts/ecos/temporal_model.py`，统一为

```
inst(m) = head_phase(g) + c + Σ_{k∈g, k 早于 m} C_k
earliest_us ≤ inst(m) ≤ latest_us        # 绝对周期内时刻
```

规划器与验证器**共用同一个 `TemporalModel`**，避免"能算出来的"和"能验出来的"两套语义。
旧 `sample_window_us` 保留为 deprecated 相对漂移通道。

反例（`chain_window.dag.json` + `chain_window.contract.json`，无 `phase_pinned`）：

| 划分 | s3 实际输出时刻 | 窗口 [380,520] | 结论 |
|---|---|---|---|
| 全融合 | 22.3 µs | 早于下界 **357.7 µs** | 拒（旧实现会放行） |
| `s1+s2 \| s3+s4` | 421.0 µs | 在窗内 | **选中**（R≈421.8 µs） |
| 全拆 | 421.0 µs | 在窗内 | 可行但多付 2 次唤醒 |

这是比 `phase_pinned` 更强的证据：边界由**数值窗口**选出，而不是由一个布尔开关挡住。

**期限不可行**（`chain_window_tight.contract.json`：s3 必须 ≥380 µs 输出，而期限只有 300 µs）：
规划器 `rc=3` 并明确报"**时间不可行**"，不输出一个"最优但违约"的方案。
同时修正了旧的误导性文案（曾把时间不可行归为"输入层 / 域不允许"）。

### P0-3：严格门禁没贯穿工具链（成立）

`plc_kernelize__内核化.sh` 找不到契约时是"警告并继续"，只有 `FUSE_REQUIRE_CONTRACT=1` 才拒。
对 legacy cyclictest 链合理，对 ECoS 论文链不合理。

修复：**不动 legacy 默认行为**，新增独立严格入口 `kfusion/scripts/ecos_build.sh`：

```
契约校验 → 规划 → 对落盘 plan.json 再独立复检 → 生成（--dag + --require-verified）
```

退出码语义：`2` 参数/环境问题、`3` 契约或方案不合法、`4` 后端未实现。
配套 `ecos/contract_validate.py` 专门挡一类静默失效：**契约里 stage 名写错**
（窗口查不到 → 等于没约束 → 全融合被放行）。这类错误在图上和自测里都看不出来。

`--target linux-kernel` 目前**明确 `rc=4` 并说明缺什么**（融合期体拆 ring-0 + 宿主
hrtimer 释放 + 复用 LLVM 内核化 Pass 产 `.ko`），不假装能出 `.ko`——静默降级是这类工具最糟的行为。

### 回归

融合自测 **52 → 68**（#19 组间越序、#20 绝对窗口选边界、#21 期限不可行、
#22 生成器拒绝未验证方案、#23 严格入口四条路径），全绿；DAG 自测 35/35 不受影响。

> 诚实备注：`PlanVerifier` 独立于求解器的**判定过程**，但与规划器共享
> `temporal_model` 的**语义定义**——这是刻意的，语义必须唯一，否则两边会各说各话。

---

## 9. 下一步

- ~~**跨周期依赖**~~：**已做并已实测**（§5.6）：闭环省 82.42 µs/周期、摘要逐位相同。
- ~~**NuttX 侧同实验**~~：**已做并已实测**（§5.7）：省 22.60 µs，收益比 3.65 ≈ `c` 比 3.66，
  **"收益 ∝ c" 成立**。副产品：NuttX builtin 跨调用继承全局状态的坑（摘要检查必须每次重建 cell）。
- ~~**多任务真部署**~~：**已做并已实测**（§5.8）：5 真线程的唤醒税 104.55 ≈ 同线程 5 次释放
  的 102.85（+1.7%），融合收益 83.34 µs 在真部署下**保持**。边界：同核 + 共享地址空间，
  无消息传递/锁/跨核。
- ~~**DAG 不确定性门禁 / 相位端到端模型 / 控制语义硬约束（PR6/7/8）**~~：**已做**（§5.9 + §5.10，
  自测 #15–#18）：规划器从"可运行实验规划器"升级为"可相信的约束优化器"，融合得越多不一定越好
  有了反例证据。
- **该不该合（收益侧，跨学科一步）**：现在能拒（相位钉住 / 死区绝对上限 / 采样窗），
  但还需把死区变化（200 → 998 µs）换成控制代价（相位裕度损失），让优化目标直接吃控制指标。
- **跨核 / 跨域的真部署**：§5.8 的线程都在同一核上；把不同相位的任务铺到不同核
  （或不同 cell），唤醒代价与"共享全局"的正确性都要重测——那正是 ECoS 的主场，且目前
  已有 Jailhouse 跨域（root cell + NuttX cell）的板上基础可复用。
- ~~**可信方案验证 / 绝对时间窗口 / 严格入口（P0-1/2/3，PR9-A/B）**~~：**已做**（§5.11，
  自测 #19–#23）：倒序方案再也不能通过复检；采样/输出的判断从"相对漂移"改成"绝对时刻"；
  严格入口缺契约即停。
- **PR9-C 的剩余部分（下一个真正的工程里程碑）**：`ecos_build --target linux-kernel` 目前
  如实 `rc=4`。要补齐的是把融合期体拆成 ring-0 代码 + 宿主 hrtimer 释放（生成物不能自带
  `clock_nanosleep()` / `main()`），并复用现有 LLVM 内核化 Pass 与 Kbuild 产出 `.ko`。
- **L2（把编译器搬进内核/cell）暂缓**：见 `PR9_文献调研_编译器内核化.md` §6 的修订理由。
