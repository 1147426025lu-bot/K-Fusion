# 论文详细大纲

> 本大纲是「论文初稿」的骨架部分。每个小节分两层给出：**【写什么】**（该小节承担的任务与边界）+ **【逐段要点】**（每一段应写的内容、逻辑作用与引用位置）。  
> 硬性约束贯穿全篇：**不编造实验数据、不编造文献、不编造指标**；凡缺数据处一律标 `【此处需要实验结果】`；凡需引用而暂无可靠出处处标 `【待补文献：主题】`。  
> 数据与事实来源：`kfusion/docs/` 下的项目总览报告、`kfusion/docs/paper/*`、仓库脚本与产物。所有数值仅在实验完成后填入。

---

## 0. 题目、目标期刊与篇幅

**工作题目（EN）**：*K-Fusion: Compiler-Guided Split Kernelization for Predictable Control on a Single PREEMPT_RT Domain — A Raspberry Pi Study*

**工作题目（ZH）**：*K-Fusion：面向单一 PREEMPT_RT 域的编译引导分裂式内核化——一项树莓派平台的对照研究*

**目标期刊（Top 1 区候选，任选其一并在投稿时定版）**：

- IEEE Transactions on Industrial Informatics（TII，工业边缘控制契合度最高）
- IEEE Transactions on Computers（TC）/ IEEE TPDS
- IEEE/ACM Transactions on Embedded Computing Systems（TECS）
- Elsevier Journal of Systems Architecture（JSA）

**版式与篇幅**：IEEE 双栏，正文 ~12–14 页；图表 ≤ 8 图 + ≤ 6 表；摘要 150–250 词。

**【逐段要点】**（本节不写正文，属元信息）

- 题目避免"击败/超越"措辞，突出"机制 + 单一 RT 域 + 平台"。
- 明确本文是**对照研究（comparative study）**&#x800C;非单纯工具报告。

---

## 1. Abstract（摘要）— 150–250 词

**【写什么】** 一段式摘要，按「问题 → 两条路线 → 本文方法 → 评估方式 → 结论（占位）」五步走。

**【逐段要点】**

1. **问题**：在 COTS aarch64 平台上同时获得**硬实时可预测性**与**完整 Linux 生态**是开放问题；不可预测性来源需点到 cache/总线、timer 干扰、背景负载。**不得**在此断言具体抖动数字。
2. **两条路线**：一句话各点明——(i) **空间隔离**（分区 hypervisor + sRTOS，cRTOS）[1]；(ii) **编译期交付形态变换**（本文 K-Fusion）。
3. **本文方法**：LLVM 引导的**分裂式内核化**——保留算法 IR，将 POSIX 调用重映射为内核侧 `plc_*`，经 DCE/定点/热路径约束后生成 freestanding `kernel.o`，链接为可加载 `.ko`，在**单一 PREEMPT_RT 域**内以 kthread + hrtimer 执行。
4. **评估方式**：在 Raspberry Pi（aarch64, PREEMPT_RT）上，与用户态基线、手写内核模块基线对照，指标与 cRTOS 主维度对齐（周期抖动、最大偏差）；cRTOS 采用**文献值 + 方法论对照**，不声称击败。
5. **结论（占位）**：`【此处需要实验结果：在何种负载下、相对用户态基线的量级改善/代价】`。写明 trade-off 而非单赢。

**关键词**：COTS；PREEMPT_RT；split kernelization；LLVM；WCET-oriented pass；cyclictest；Raspberry Pi；partitioning hypervisor；cRTOS；Timed C。

---

## 2. §1 Introduction（引言）— 完整正文见 `DRAFT_B__引言正文.md`

**【写什么】** 用漏斗结构把读者从"边缘控制在 COTS 上普及"引到"本文要解决的精确缺口"，再给出 RQ 与贡献。

**【逐段要点】**（详见 `DRAFT_B`）

1. 引入背景与张力；2. 说明不可预测性根源；3. 归纳两条已有哲学并各举代表工作；4. 明确研究缺口；5. 提出 K-Fusion；6. 列 RQ1–RQ4；7. 列贡献 C1–C5；8. 方法学与公平性声明；9. 论文结构。

---

## 3. §2 Background（背景）

**【写什么】** 只提供理解本文所需的**最小技术背景**，不展开综述（综述放 §3）。

### 2.1 COTS 实时 Linux 栈

**【逐段要点】**

- 段1：PREEMPT_RT 的机制（线程化中断、优先级继承、FIFO 调度）与它在"降低最坏延迟"上的定位 [PREEMPT_RT 文档]。
- 段2：用户态周期任务的执行模型（`clock_nanosleep`/cyclictest），说明用户态路径上抖动来源（系统调用、调度延迟、timer 迟滞）。
- 段3：内核模块/kthread/hrtimer 作为"内核内周期执行"的宿主模型——本文方法的运行时基座。

### 2.2 Compounded RTOS（cRTOS）— 基线 A 摘要

**【逐段要点】**

- 段1：定义（Linux normal realm + 硬实时 sRTOS realm）与 Jailhouse 静态分区 [1]。
- 段2：remote syscall 机制（sRTOS 进程经共享内存调用 Linux 富功能）[1]。
- 段3：原始声明结果（约 4 µs jitter、有界最大延迟）[1]——**必须标注为原文 x86-64 平台数字**。
- 段4：代价（hypervisor 依赖、x86 硬件特性、双 OS 维护、跨域调用开销）[1]。

### 2.3 Compiler-Guided Split Kernelization（K-Fusion）— 本文方法摘要

**【逐段要点】**

- 段1：流水线总览（C → IR → Pass → `kernel.o` → `.ko`）。
- 段2：**Split** 的含义——算法在 `_kernel.o`，启动/ABI/定时在可替换宿主；非整进程 translation。
- 段3：manifest 驱动的可行性分类。

### 2.4 Timed C 与端到端 toolchain 启示（结构模板）

**【逐段要点】**

- 段1：Timed C 的语言级时间原语与可分析性 [2]；端到端 toolchain 的"编译→测量→分析"结构 [3 需核实完整出处 RTSS 2019]。
- 段2：**本文借用的是测量协议/框图结构，而非语言语义**——明确边界，避免误读为"K-Fusion 是 Timed C 的替代"。

---

## 4. §3 Related Work（相关工作）

**【写什么】** 分四条支线各一段（或一段+一小表），末尾用**对比表 Table 1**收束，把本文放进坐标系。

**【逐段要点】**

- 3.1 实时 Linux 扩展：PREEMPT_RT、Xenomai 3（cRTOS 的对照对象 [1]）、IRQ 线程化与隔离调优。**引用位置**：\[1\]\[PREEMPT_RT]。
- 3.2 虚拟化与分区实时：Jailhouse [1]、Xen/ACRN 分区 RT、AMP 异构。**引用位置**：Jailhouse 出处 `【待补文献：Jailhouse 原始论文/文档】`。
- 3.3 编译器与 WCET：PREM（可预测区间 + LLVM 改造）[3]、WCET 工具链与基准；eBPF/LKL/unikernel 的**边界定位**（保留源码 vs 受限运行时）。**引用位置**：[3] + `【待补文献：LKL/eBPF 内核执行】`。
- 3.4 语言级实时扩展：Timed C [2]、Ada、Giotto；与 K-Fusion **可并存**（Timed C 写周期壳 + K-Fusion 内核化热函数）。
- **Table 1（Related work / design axes）**：列 工作 | 核心机制 | 隔离/执行域 | Linux 内核修改 | 典型平台。行：cRTOS [1]、PREEMPT_RT、Timed C [2]、K-Fusion（本文）。

---

## 5. §4 Problem Statement, Design Space and Assumptions（问题陈述、设计空间与假设）

**【写什么】** 把"要解决什么"形式化到可操作定义，并**显式列出全部假设**（对应写作硬性约束"所有模型假设需明确列出"）。

### 4.1 目标的操作化定义

**【逐段要点】**

- 段1：**硬实时**在本文的操作定义 = 在固定隔离配置下，周期任务抖动的**可重复上界**（measurement-based），**非**形式化 WCET 证明。**必须显式声明该弱化**。
- 段2：**Rich Linux features** 的分级定义（文件系统/网络/动态加载/多进程），并说明 K-Fusion 下哪些仍依赖同域 Linux 用户态。

### 4.2 设计轴（Design axes）

**【逐段要点】** 以编号列表给出 5 条（空间隔离 vs 就地加速；双 OS vs 双产物；remote syscall vs `plc_*` 重映射；hypervisor 调度 vs 编译 DCE + hrtimer 宿主；Linux 二进制兼容 vs 源码级融合）。

### 4.3 假设与范围（**显式假设清单**）

**【逐段要点】** 逐条列出（完整版见 `DRAFT_B` 第 3 节）：

- A1 平台假设：实验在 Raspberry Pi（aarch64）+ PREEMPT_RT，单板单核隔离配置固定。
- A2 执行域假设：RT 控制路径在 `.ko` 内以 kthread + hrtimer 执行；不假设 spatial isolation。
- A3 负载假设：主负载为周期任务（cyclictest）；泛化由第二应用/多任务 demo 覆盖。
- A4 对比假设：cRTOS 数字来自**原文 x86-64**，为 cross-study 引用，非同板复现。
- A5 统计假设：每配置多次独立 run，报告分布/分位，非单次。
- A6 内核/工具链假设：固定 `uname -r` 与 LLVM 版本。`【实验时填：具体版本】`
- A7 未建模项：cache/总线争用、DMA、温度/频率 DVFS 不作为显式建模对象，仅通过隔离 profile 弱控制。

### 4.4 公平性原则（Fair comparison doctrine）

**【逐段要点】** 相同内核版本、相同 CPU 隔离 profile、相同时长（≥15 min）、cRTOS 数字**标注来源**（cited vs reproduced）。

---

## 6. §5 Baseline I: Compounded RTOS（cRTOS）— 对照方法

**【写什么】** 以**文献复述 + 实验维度映射**为主；不声称在 Pi 上复现 cRTOS。

**【逐段要点】**

- 5.1 架构与执行模型：双 realm、remote syscall 路径、中断策略（据原文 [1]，逐点标注出处）。
- 5.2 cRTOS 实验协议摘要：对照对象（PREEMPT_RT、Xenomai 3）、指标（timing accuracy、interrupt latency、GUI 可执行性）、结果要点（~4 µs jitter、有界最大延迟）[1]。
- 5.3 映射到 Pi 实验的"类比基线"表：sRTOS 硬实时域 ↔ `.ko` 内 kthread+hrtimer；hypervisor 隔离 ↔ CPU affinity+cgroup（**弱类比，须声明**）；remote syscall ↔ **不类比**。
- 5.4 限制说明：Jailhouse-on-ARM 与原文 x86 栈不同，故**不复现 cRTOS 数字**，仅引用 + 讨论。

---

## 7. §6 Method: K-Fusion End-to-End Toolchain（本文方法）

**【写什么】** 本文技术主体，仿 Timed C 端到端 toolchain 结构：总览 → 编译 → 运行 → 测量 → 验证。

### 6.1 Toolchain 总览（Figure 1 — 必画）

**【逐段要点】**

- 段1：给出流水线框图（manifest+源码 → AST 预检 → Clang IR → K-FusionPass → `kernel.o`+validate.json → ignite_fused → `.ko` → deploy/soak/stress → results）。
- 段2：标注与 cRTOS 流水线**正交**（无 hypervisor 层；编译器位于 **build time**）。

### 6.2 分裂式内核化（Split kernelization）

**【逐段要点】**

- 段1：`_kernel.o` vs 宿主组件分工表（host / hrtimer / pthread）。
- 段2：**八步内核化 Pass**：normalize → float-kill#1 → remap → DCE → float-kill#2 → export → wcet-mark → cleanup（据 `K-FusionPass` 实现，逐条一句话）。
- 段3：**间接调用解析**（GEP/常量表/全局函数指针表进 DCE）——这是 kernelization 易漏点，明确说明。
- 段4：Q 定点策略与 validate 门禁。

### 6.3 Manifest 与自动化

**【逐段要点】**

- 段1：13 manifest / 6 大类的分类逻辑。
- 段2：AST 预检 vs cRTOS"同二进制跑两 OS"迁移路径的对比。

### 6.4 运行时模型

**【逐段要点】**

- 段1：`insmod` → kthread → `main`/`timerthread`/`plc_main` 调用链（据 §4 运行时）。
- 段2：pthread 映射；**单域同时仅一个 fused 模块**的运行约束。

### 6.5 测量与验证层（Timed C toolchain 对应）

**【逐段要点】** 一张映射表：KTC 编译↔`plc_fuse`+`ignite_fused`；measurement-based WCET↔`wcet_sweep`/autotune；schedulability **不声称**；sensitivity↔Pass/剖面消融。

---

## 8. §7 Implementation（实现）

**【写什么】** 提供可复现所需的工程事实，不掺分析。

**【逐段要点】**

- 7.1 硬件与软件环境：Pi 型号/RAM/存储；内核 `uname -r`；LLVM 版本；`PLC_PLATFORM`；隔离 profile 参数表。`【实验时填具体版本】`
- 7.2 K-Fusion 实现要点：Pass 插件、pipeline policy、artifact 路径；代码规模（Pass LOC、manifest 数、CI 时间）。
- 7.3 实验工件（Artifact）：`scripts/paper/run_paper_*` 脚本索引；固定 seed/`PAPER_RUNS`/原始 log 目录规范；内核版本 pin 承诺。
- 7.4 cRTOS 实现（本文）：**无**——引用 [1]；附录列原文实验配置摘要。

---

## 9. §8 Experimental Methodology（实验方法）

**【写什么】** 先研究设计，再指标，再负载，再流程，再统计。

**【逐段要点】**

- 8.1 研究设计类型：Primary（Pi 上 within-subject：userspace vs baseline_ko vs fused）；Secondary（与 cRTOS/PREEMPT_RT/Xenomai cross-study）；Exploratory（第二应用、multitask、WCET autotune）。
- 8.2 工作负载 W1–W6 表（cyclictest 主线 / 用户态 / 手写 baseline_ko / signaltest / plc_multitask LET / stress 背景）。
- 8.3 指标表（与 cRTOS 对齐）：period jitter、abs_max/p99、wake-to-run latency、build feasibility、deployment steps、overhead。逐项给**操作定义**。
- 8.4 实验流程（本机 Pi 执行）：记录 `lsmod` → 可行性扫描 → 基线矩阵 `PAPER_RUNS=5 DURATION_MIN=15` → 消融矩阵 → 用户态对照 → 汇总 → 填表。**命令原样保留**以便复现。
- 8.5 统计方法：每配置 ≥5 次独立 run；报告 mean/std/95%CI 或分位；说明 outlier 处理；比较分布用非参数检验。**不得**在无数据时给期望值。

---

## 10. §9 Evaluation（评估与结果）

**【写什么】** 按 RQ 顺序给结果；所有数值均为 `【此处需要实验结果】` 占位。

**【逐段要点】**

- 9.1 **RQ1** 主结果：Figure 2（三基线 soak CDF）、Figure 3（stress abs_max）、Table 2（mean±std、n、profile）。文字：相对 userspace 的改善% `【此处需要实验结果】`；相对 baseline_ko 的 Pass 价值。
- 9.2 **RQ2** cross-study 对照：Table 3（cRTOS x86 cited vs K-Fusion Pi measured，**分栏标注**）；讨论量级与 trade-off，**不得**声称击败。
- 9.3 **RQ3** Linux 富功能 vs 单域内核化：定性对比 + 功能分解表；GLIBC/VFS/网络仍靠同域用户态。
- 9.4 **RQ4** 端到端可报告性：validate.json 字段、CI 通过率、WCET sweep 一行结果；Figure 4（pipeline + 一次 fuse 的 artifact 时间线）。
- 9.5 消融：Table 4（hist/wake/ring/isolation 组件贡献）；Pass 消融（generic vs wcet-hotpath vs autotune winner）。
- 9.6 第二应用与泛化：signaltest 短测；plc_multitask STRICT LET 多 TU（时间复用，勿称"真多线程"）。
- 9.7 开销：fuse+ignite 时间、`.ko` 大小、insmod 构建时间。

---

## 11. §10 Discussion（讨论）

**【逐段要点】**

- 10.1 设计空间总结：**何时选 cRTOS 路线**（需硬分区、Linux 二进制 GUI、x86 已部署 Jailhouse、可维护双 OS）；**何时选 K-Fusion**（已有 C 源码、Pi/ARM、希望单 RT 镜像、LLVM 优化链）；**互补**关系。
- 10.2 与 Timed C 路线的关系 [2]：语言层时间语义 vs 交付形态变换；未来可组合。
- 10.3 与 PREM [3] / TimerShield [4 待核实] 的关系：coschedule/interval vs 本文 remap+host timer；可协同方向。

---

## 12. §11 Threats to Validity（效度威胁）

**【逐段要点】**

- 构造效度：cyclictest 非真实 PLC 负载 → 用 W4/W5 补强。
- 外部效度：Pi ≠ 工业 x86；cRTOS 数字非 Pi 实测。
- 内部效度：隔离/profile 漂移、模块残留、D 状态死锁。
- 结论效度：样本 run 数、单板方差。

---

## 13. §12 Conclusion and Future Work（结论与未来工作）

**【逐段要点】**

- 段1：复述闭环（可复现的"编译–内核化–测量"）；数值结论 **占位** `【此处需要实验结果】`。
- 段2：与 cRTOS 的 trade-off 收束（无需 hypervisor 分域，但不自动获得 spatial isolation 与 Linux 二进制透明性）。
- 段3：Future work（ARM Jailhouse/AMP 共存、多 `.ko` 命名空间与符号私有化、TimerShield 类 timer-aware 宿主、形式化 WCET 接口、x86_64 同板对照）。

---

## 14. 附录（Appendices）

- A：manifest 与 workload 完整列表。
- B：隔离 profile 参数与环境脚本。
- C：validate.json schema 与 fusion_report 示例。
- D：cRTOS VEE 2020 实验配置摘要（引用用）。
- E：Timed C 原语 与 K-Fusion manifest 域对照（可选）。
- F：复现清单（Artifact appendix checklist）。

---

## 15. 图表清单（投稿前预注册）

| 编号      | 类型  | 内容                                           | 数据来源                  |
| ------- | --- | -------------------------------------------- | --------------------- |
| Fig. 1  | 架构  | cRTOS 双域 vs K-Fusion 单域 split kernelization | 绘图                    |
| Fig. 2  | 架构  | K-Fusion 端到端 toolchain                      | 绘图                    |
| Fig. 3  | CDF | Soak jitter：userspace / baseline_ko / fused  | `【此处需要实验结果】`          |
| Fig. 4  | Bar | Stress abs_max                               | `【此处需要实验结果】`          |
| Fig. 5  | 消融  | Ablation delta ns                            | `【此处需要实验结果】`          |
| Fig. 6  | 流水线 | 一次 fuse 的 artifact 时间线                       | 运行日志                  |
| Fig. 7  | 编译器 | KTC vs K-Fusion 双流水线                        | 绘图                    |
| Table 1 | 对比  | Related work + design axes                   | 文献                    |
| Table 2 | 数据  | Pi 主结果（三基线 × soak/stress）                    | `【此处需要实验结果】`          |
| Table 3 | 数据  | Cross-study：cRTOS(cited) vs K-Fusion(Pi)    | 文献 [1](≈4µs) + K-Fusion 3.391µs(仓库实测) + cRTOS-like 6µs(Pi4 本工作实测) |
| Table 4 | 数据  | Feasibility 13 manifests                     | 运行 CSV                |
| Table 5 | 定性  | 部署复杂度：cRTOS vs K-Fusion                     | 分析                    |

---

## 16. 写作顺序建议（执行态，不属论文正文）


1. §4 设计空间表 + Fig.1/2 定调；
2. §7–§8 实验协议先跑 `run_paper_baseline_matrix` 填 Table 2；
3. §6 方法（与仓库实现对齐）；
4. §5 cRTOS 摘要 + Table 3 cross-study；
5. §3 相关工作；
6. **§1 引言 + Abstract（最后润色）**；
7. §9–§12 按真实数据写结论。

---

*文档：`docs/paper/DRAFT_A__论文详细大纲.md` · 配套正文：`docs/paper/DRAFT_B__引言正文.md`*
