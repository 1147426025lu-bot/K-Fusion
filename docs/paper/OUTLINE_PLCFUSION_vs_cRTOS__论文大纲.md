# 论文大纲（草案）

**工作标题（中）**：编译器内核化与组合实时系统在 COTS 平台上的可预测执行：以树莓派为实验平台的对照研究  

**Working title (EN)**：*Predictable Real-Time Execution on COTS: Compiler-Guided Kernelization versus Compounded RTOS with a Partitioning Hypervisor — A Raspberry Pi Case Study*

**结构参照**：
- Timed C（RTAS 2018）：语言/编译器动机 → 可移植实现 → 案例研究  
- Timed C 端到端 toolchain（RTSS 2019）：Figure 1 方法论 → 测量 → 分析 → 敏感性  
- cRTOS（VEE 2020）：系统架构 → 与 PREEMPT_RT / Xenomai 对照 → 定时精度与中断延迟  

**对照对象（Baseline A，文献）**：Yang & Shinjo, *Obtaining hard real-time performance and rich Linux features in a compounded real-time operating system by a partitioning hypervisor*, VEE 2020.  
（Jailhouse 分区 hypervisor + NuttX sRTOS + Linux GPOS + remote syscall）

**本文方法（Baseline B / 贡献）**：PLCFusion — LLVM 引导的用户态 C **分裂式内核化**（`kernel.o` + 宿主 `.ko`），在 **PREEMPT_RT Linux** 上单域运行。

**实验平台**：以 **Raspberry Pi（aarch64，PREEMPT_RT）** 为主；全部对照实验在 **本机（Pi）** 执行。cRTOS 原论文为 x86-64 + Jailhouse，**不在 Pi 上复现完整 cRTOS**；对其指标采用 **文献值 + 方法论对照 + Pi 上可类比基线**（见 §8、§10）。

---

## 摘要（Abstract）— 撰写要点（150–250 词）

1. **问题**：在 COTS 嵌入式平台上同时获得「硬实时可预测性」与「Linux 生态」仍是开放问题。  
2. **两条路线**：  
   - **组合系统（cRTOS）**：空间隔离 — hypervisor 分域，sRTOS 跑实时、Linux 跑富功能；  
   - **编译器内核化（PLCFusion）**：时间/控制路径优化 — 保留算法 IR，POSIX→`plc_*`，链入内核模块。  
3. **方法**：端到端 toolchain（融合 → 验证 → `.ko` → 浸泡/加压测量），平台为 Pi。  
4. **实验**：与 cRTOS 论文 **同维度指标**（周期抖动、中断/唤醒延迟、可界定上界）+ Timed C 式 **测量–报告–对照** 流程。  
5. **结论（占位，实验后填）**：在 Pi 上，编译器内核化在 **部署复杂度 / 硬件依赖 / 抖动上界** 上与组合系统 trade-off 不同；何种负载下哪条路线更优。

**关键词**：COTS；PREEMPT_RT；hypervisor；split kernelization；LLVM；cyclictest；WCET；Raspberry Pi；Timed C；cRTOS

---

## §1 Introduction（引言）

### 1.1 动机与背景
- COTS（Pi、通用 ARM SoC）在工业边缘控制中普及；硬实时与 Linux 特性并存的需求。  
- 不可预测性来源：cache/总线、timer 干扰、hrtimer、背景负载、驱动栈深度。  
- 两条互补哲学：  
  - **改系统结构**（分域、双 OS、remote syscall）— cRTOS；  
  - **改应用交付形态**（编译期映射 + 内核内执行热路径）— PLCFusion。

### 1.2 研究问题（Research Questions）
- **RQ1（架构）**：在不引入 hypervisor 分域的前提下，编译器内核化能否在 Pi 上达到与文献中 cRTOS **同量级**的周期抖动上界？  
- **RQ2（成本）**：相对 cRTOS，PLCFusion 在 **开发/部署/迁移** 上的复杂度如何（manifest、Pass、单 `.ko` vs 双 realm + NuttX 移植）？  
- **RQ3（生态）**：Linux 富功能（网络、文件系统、GUI）与硬实时控制 **能否同域共存**，还是必须 spatial split？  
- **RQ4（方法论）**：Timed C 式端到端「源码 → 测量 → 可报告边界」是否适用于内核化路径？

### 1.3 贡献（Contributions）— 定稿前需与实验对齐
- **C1**：形式化对比 **Compounded RTOS（cRTOS）** 与 **Compiler-Guided Split Kernelization（PLCFusion）** 的设计空间（表格 + 架构图）。  
- **C2**：在 Pi 上实现可复现的 **PLCFusion 端到端 toolchain 实验协议**（含 CI 门禁指标与 paper 脚本）。  
- **C3**：与 cRTOS **同维度**测量：周期定时精度（cyclictest / 1 ms loop）、唤醒/响应延迟分布、加压下 worst-case。  
- **C4（可选）**：WCET-oriented Pass 组合（`wcet-benchmark` / autotune）作为「编译–运行协同」子研究。  
- **C5（可选）**：第二应用（signaltest / plc_multitask）证明非 cyclictest 专用。

### 1.4 论文结构（Paper organization）
- 标准路线图 §2–§11。

---

## §2 Background（背景）

### 2.1 COTS 实时 Linux 栈
- PREEMPT_RT：线程化中断、优先级继承、FIFO。  
- 用户态周期任务：`clock_nanosleep` / cyclictest；timer interference（可引用 TimerShield RTAS 2017 作背景，非主对照）。  
- 内核模块 / kthread / hrtimer 宿主模型。

### 2.2 Compounded RTOS（cRTOS）架构摘要 — 对照基准 A
- **定义**：Linux（normal realm）+ sRTOS（NuttX，hard RT realm）。  
- **Jailhouse**：静态分区；CPU/内存/设备隔离。  
- **Remote syscall**：sRTOS 进程调用 Linux 富功能；overlay rootfs。  
- **声称优势**：不改 Linux 内核；~4 µs jitter；有界中断延迟；可跑 Linux GUI 二进制。  
- **代价**：hypervisor 依赖、x86 硬件特性（VT-x 等）、双 OS 维护、跨域调用延迟。

### 2.3 Compiler-Guided Split Kernelization（PLCFusion）架构摘要 — 本文方法
- 流水线：C → IR → Pass（remap/DCE/定点/low-jitter）→ `kernel.o` → 宿主 `.ko`。  
- **Split**：算法在 `_kernel.o`，启动/ABI/卸载在宿主；非整进程 translation。  
- Manifest 驱动：13 类应用、12 类 CI ko 门禁。  
- Pi 上运行：**单 PREEMPT_RT 域**，无 Jailhouse。

### 2.4 Timed C 与端到端 toolchain 启示 — 结构模板
- Timed C：timing primitives + KTC 源码到源码编译。  
- RTSS 2019 toolchain：编译 → **测量 instrumentation** → schedulability / sensitivity。  
- **本文借用**：Figure 1 式「端到端框图」+ 测量协议章节，而非借用 Timed C 语言语义。

---

## §3 Related Work（相关工作）

### 3.1 实时 Linux 扩展
- PREEMPT_RT、Xenomai 3（cRTOS 论文对照对象）。  
- RTL、IRQ 线程化、affinity/isolation 调优（本仓库 `scripts/deploy/`、`scripts/tune/`）。

### 3.2 虚拟化与分区实时
- cRTOS / Fixstars cRTOS 后续工作（IEEE 期刊扩展，若有）。  
- Jailhouse、Xen/ACRN 分区 RT、AMP 异构（简要）。

### 3.3 编译器与 WCET
- PREM（RTAS 2011）：predictable interval + LLVM。  
- WCC、TACLeBench、本仓库 WCET sweep/autotune。  
- eBPF / LKL / unikernel — **边界定位**（表：保留源码 vs 受限运行时）。

### 3.4 语言级实时扩展
- Timed C、Ada、Giotto — 「显式时间语义」vs 「后验 Pass 优化」。  
- 与 PLCFusion 关系：**可并存**（Timed C 写周期壳 + PLCFusion 内核化热函数）。

### 3.5 对比表（Related Work Summary Table）— 必做
| 工作 | 隔离方式 | Linux 修改 | 硬件假设 | 主要指标 | 平台 |
|------|----------|------------|----------|----------|------|
| cRTOS | Hypervisor 分域 | 无 | VT-x, x86 | jitter, IRQ latency | x86 |
| PREEMPT_RT | 内核补丁 | 有 | 通用 | cyclictest | 多平台 |
| PLCFusion（本文） | 编译期+单域 RT Linux | 无（模块） | Pi/ARM | soak/stress jitter | **Pi** |
| Timed C | 语言+RTOS API | 视目标 | POSIX/FreeRTOS | WCET+sched | Pi 等 |

---

## §4 Problem Statement and Design Space（问题陈述与设计空间）

### 4.1 形式化目标
- **硬实时**在本文中的操作定义：在固定隔离配置下，周期任务 jitter / abs_max 的 **可重复上界**（非形式化 WCET 证明）。  
- **Rich Linux features**：文件系统、网络、动态加载、多进程 — 分级讨论（哪些 PLCFusion 仍依赖 Linux 域）。

### 4.2 设计轴（Design axes）
1. **Spatial isolation**（cRTOS）vs **In-place acceleration**（PLCFusion）。  
2. **Dual OS** vs **Dual artifact**（`.ko` + 用户态工具链）。  
3. **Remote syscall** vs **Remap to `plc_*` stubs**。  
4. **Hypervisor schedule** vs **Compiler DCE + hrtimer 宿主 + CPU isolation**。  
5. **Binary compatibility with Linux**（cRTOS 强）vs **Source-level fusion**（PLCFusion 强）。

### 4.3 假设与范围（Scope）
- **在 Pi 上**：不实现 Jailhouse cRTOS；cRTOS 作为 **架构对照 + 文献数据**。  
- **应用类**：周期控制（cyclictest 主线）、多线程 RT test、PLC 风格 `plc_cycle`、可选 multitask demo。  
- **不在范围**：完整 GPOS GUI 栈、驱动生态 parity、多 `.ko` 并行加载。

### 4.4 公平性原则（Fair comparison doctrine）
- 相同 **PREEMPT_RT 内核版本**（记录 `uname -r`）。  
- 相同 **CPU 隔离 profile**（`scripts/deploy/profiles/`）。  
- 相同 **测量时长**（≥15 min soak 正式数据）。  
- cRTOS 数字 **标注来源**（ reproduced / cited），避免混为一谈。

---

## §5 Method I: Compounded RTOS Baseline（对照方法 — cRTOS）

> 本章以 **文献复述 + 实验维度映射** 为主；若未来有 x86 复现可增 §5.5。

### 5.1 架构与执行模型
- 双 realm 图；remote syscall 路径；RT bridge / 中断策略（据原文）。  

### 5.2 cRTOS 实验协议（VEE 2020）摘要
- 对比对象：PREEMPT_RT Linux、Xenomai 3。  
- 指标：timing accuracy、interrupt latency、GUI 可执行性。  
- 结果要点：~4 µs jitter、有界 max latency、RT 设备最佳响应。

### 5.3 映射到 Pi 实验的「类比基线」
| cRTOS 论文概念 | Pi 上类比实现 |
|----------------|---------------|
| sRTOS 硬实时域 | PLCFusion `.ko` 内 kthread + hrtimer |
| Linux 富功能域 | 同内核用户态 + 可选 background load |
| Hypervisor 隔离 | **CPU affinity + cgroup + soak profile**（弱类比，§10 声明） |
| Remote syscall | **不类比**（PLCFusion 用 stubs，非跨 VM） |

### 5.4 限制说明（Limitation of cRTOS on Pi）
- Jailhouse on ARM/Pi 与原文 x86 栈不同；**不复现 cRTOS 数字**，仅 **引用 + 讨论**。

---

## §6 Method II: PLCFusion End-to-End Toolchain（本文方法）

> **仿 Timed C RTSS 2019 §II–§V 结构**：总览图 → 编译 → 测量 → 验证 → 敏感性。

### 6.1 Toolchain 总览（Figure 1 — 必画）
```
Manifest + C sources
    → [plc_ast / preflight]     ← 可行性（Timed C: task model）
    → Clang → IR (multi-TU link)
    → PLCFusionPass (+ fixed-point, DCE, low-jitter, WCET tail)
    → kernel.o + validate.json
    → ignite_fused (host + stubs) → .ko
    → deploy (soak / stress / insmod smoke)
    → results/ + paper_summarize
```
- 标注与 cRTOS 流水线 **正交**：无 hypervisor 层；编译器位于 **build time**。

### 6.2 分裂式内核化（Split kernelization）
- `_kernel.o` vs 宿主组件表（`plc_fused_host`, `hrtimer`, `pthread`）。  
- DCE / hotpath / `FUSE_GLOBALIZE_SYMBOLS`。  
- Q-only 定点策略与 validate 门禁。

### 6.3 Manifest 与自动化
- 13 manifest / 6 大类；`plc_fuse_add` 交互接入。  
- AST 预检 vs cRTOS「同二进制跑两 OS」迁移路径对比。

### 6.4 运行时模型
- `insmod` → kthread → `main` / `timerthread` / `plc_main`。  
- pthread 映射；单域 **同时仅一个 fused 模块** 约束。

### 6.5 测量与验证层（Timed C toolchain 对应）
| Timed C toolchain 阶段 | PLCFusion 对应 |
|--------------------------|----------------|
| KTC 编译到 POSIX/FreeRTOS | `plc_fuse` + `ignite_fused` |
| Measurement-based WCET | `wcet_sweep` / autotune / cyclictest histogram |
| Schedulability test | **不声称**（可选：周期任务集 rough util 上界） |
| Sensitivity analysis | Pass 消融 + profile 消融 + `optimization_continue.json` |

---

## §7 Implementation（实现）

### 7.1 硬件与软件环境
- **主平台**：Raspberry Pi 5 / 4（写明型号、RAM、SD/eMMC）。  
- 内核：`6.12.62+rpt-rpi-v8-rt`（或实验时实际版本）。  
- LLVM 17/19；`PLC_PLATFORM=rpi5`。  
- 隔离：`profile_soak_l2_best`、`profile_stress_l2` 参数表。

### 7.2 PLCFusion 实现要点
- Pass 插件、pipeline policy、artifact 路径（`test/*.validate.json`）。  
- 代码规模：Pass LOC、manifest 数、CI 时间（可复现性）。

### 7.3 实验工件（Artifact）
- 脚本索引：`scripts/paper/run_paper_*`。  
- 固定 seed / `PAPER_RUNS` / 原始 log 目录规范。  
- Docker / 内核版本 pin（Discussion 中承诺）。

### 7.4 cRTOS 实现（本文）
- **无** — 引用 VEE 2020；可选附录列原文实验配置。

---

## §8 Experimental Methodology（实验方法）

> **仿 cRTOS §Evaluation + Timed C §V Case Study**：先 setup，再 metrics，再 workloads。

### 8.1 研究设计类型
- **Primary**：Pi 上 **within-subject** 对比（userspace vs baseline_ko vs fused）。  
- **Secondary**：与 cRTOS / PREEMPT_RT / Xenomai **cross-study** 对照（文献表格）。  
- **Exploratory**：第二应用、multitask demo、WCET autotune。

### 8.2 工作负载（Workloads）
| ID | 负载 | 角色 |
|----|------|------|
| W1 | official cyclictest（融合主线） | 主周期抖动 |
| W2 | 用户态 cyclictest（同参数、同 CPU） | 用户态 RT 基线 |
| W3 | 手写 `baseline_ko` hrtimer | 「手工内核模块」基线 |
| W4 | signaltest / rt_periodic | 泛化 |
| W5 | plc_multitask（可选） | 多任务+定点+pthread |
| W6 | stress 背景（hackbench） | 最坏情况压力 |

### 8.3 指标（Metrics）— 与 cRTOS 对齐
| 指标 | 定义 | cRTOS 对应 | 采集 |
|------|------|------------|------|
| **Period jitter** | \|actual_period − nominal\| | timing accuracy | soak/stress PNG + raw log |
| **abs_max / p99** | 周期偏差最大值/分位 | bounded latency | `fused_stats` / histogram |
| **Wake-to-run latency** | timer 到期 → 用户代码继续 | interrupt / response time | ftrace（可选） |
| **Build feasibility** | fuse+ko PASS 比例 | （无直接对应） | feasibility CSV |
| **Deployment steps** | 人工步骤数 / 依赖 | dual-OS 复杂度 | 定性表 |
| **Overhead** | insmod 构建时间、`.o` 大小 | （类比 timer 开销） | validate.json |

### 8.4 实验流程（Protocol — 本机 Pi 执行）
1. `sudo -v`；确认无残留 `*_mod`；记录 `lsmod`。  
2. **Feasibility scan**：`run_paper_feasibility__论文可行性扫描.sh`。  
3. **Baseline matrix**（主实验）：  
   `PAPER_RUNS=5 DURATION_MIN=15 bash scripts/paper/run_paper_baseline_matrix__论文基线矩阵.sh`  
4. **Ablation**：`run_paper_ablation_matrix__论文消融矩阵.sh`。  
5. **Userspace 对照**（cRTOS 论文中的 PREEMPT_RT 用户态类比）：  
   `run_paper_userspace__论文用户态.sh`。  
6. **汇总**：`paper_summarize_results__论文结果汇总.py`。  
7. **填表**：Table 2–4 + 与 cRTOS Table 对照表（§9.4）。

### 8.5 统计方法
- 每配置 ≥5 次独立 run（reboot 或 clean rmmod 策略写清）。  
- 报告 mean、std、95% CI 或 percentile；说明 outlier 处理。  
- 非参数检验（若比较 fused vs userspace 分布）。

---

## §9 Evaluation（评估与结果）

> **章节结构仿 Timed C：先 RQ，再分实验，再汇总。**

### 9.1 RQ1：Pi 上 jitter 与上界（主结果）
- **Figure 2**：三基线 soak CDF（userspace / baseline_ko / fused）。  
- **Figure 3**：stress 下 abs_max 对比。  
- **Table 2**：mean±std、n、隔离 profile。  
- **文字**：相对 userspace 的改善%；相对 baseline_ko 的 Pass 价值（自动化 vs 手写）。

### 9.2 RQ2：与 cRTOS 文献的 cross-study 对照
- **Table 3**：cRTOS (x86, cited) vs PLCFusion fused (Pi, measured) — **同指标不同平台，分栏标注**。  
- 讨论：Pi 无 hypervisor 时 jitter 数量级；cRTOS 4 µs 与 Pi fused 目标对比。  
- **不得**声称「击败 cRTOS」，应写 **trade-off under different isolation assumptions**。

### 9.3 RQ3：Linux 富功能 vs 单域内核化
- 定性：PLCFusion 模块内 **无** 完整 Linux VFS/网络；富功能仍靠 **同域 Linux 用户态**。  
- 与 cRTOS remote syscall **功能分解**对比表。  
- 案例：温控/ GPIO shadow / insmod 短测 — 证明控制路径在内核、IO 可桩化或委托 Linux。

### 9.4 RQ4：端到端 toolchain 可报告性
- validate.json 字段；CI 12/12 ko；WCET sweep 一行结果。  
- **Figure 4**：Timed C 式 pipeline 图 + 一次 fuse 的 artifact 时间线。  
- 复现：`run_ci__CI门禁.sh` 与 paper 脚本关系。

### 9.5 消融（Ablation）
- Table 4：hist / wake / ring / isolation 组件（现有 paper 脚本）。  
- Pass 消融（可选）：generic vs wcet-hotpath vs autotune winner。

### 9.6 第二应用与泛化
- signaltest insmod 短测；plc_multitask 多 TU（可选一节）。  

### 9.7 开销（Overhead）
- fuse+ignite 时间；`.ko` 大小；context-switch 不测 TimerShield 级（除非加实验）。

---

## §10 Discussion（讨论）

### 10.1 设计空间总结
- **何时选 cRTOS 路线**：需硬分区、Linux 二进制 GUI、x86 已部署 Jailhouse、可维护双 OS。  
- **何时选 PLCFusion**：已有 C 源码、Pi/ARM、希望 **单 PREEMPT_RT 镜像**、周期算法内核化、LLVM 优化链。  
- **互补**：Linux 域跑 PLCFusion 工具链 + 非 RT 服务；RT 控制进 `.ko` — 与 cRTOS 双域 **目标相似、机制不同**。

### 10.2 Threats to validity（效度威胁）
- **构造效度**：cyclictest 非真实 PLC 负载；补充 W4/W5。  
- **外部效度**：Pi ≠ 工业 x86；cRTOS 数字非 Pi 实测。  
- **内部效度**：隔离/profile 漂移；模块残留；D 状态死锁（README 故障节）。  
- **结论效度**：样本 run 数、单次 board 方差。

### 10.3 与 Timed C 路线关系
- Timed C 解决 **语言层时间语义 + 调度分析**；PLCFusion 解决 **COTS Linux 上控制路径下沉**。  
- 未来：Timed C 周期壳 + PLCFusion 内核化 `fdelay` 体 — 一句话 future work。

### 10.4 与 PREM / TimerShield
- PREM：cache/外设 coschedule — 编译 interval；可与 Pass 协同（future）。  
- TimerShield：hrtimer 优先级 — Pi 上背景 timer 仍影响 userspace 基线；fused 部分缓解路径不同。

---

## §11 Conclusion and Future Work（结论与未来工作）

### 11.1 结论（模板句，实验后替换数字）
- 在 Raspberry Pi PREEMPT_RT 上，PLCFusion 提供 **可复现的编译–内核化–测量** 闭环；在周期抖动指标上 **相对用户态基线** ……  
- 与 cRTOS 相比，**无需 hypervisor 分域** 但 **不自动获得** spatial isolation 与 Linux 二进制透明性；二者面向不同部署约束。  

### 11.2 Future work
- ARM Jailhouse / AMP 与 PLCFusion 共存评估；  
- 多 `.ko` 命名空间与 symbol 私有化；  
- TimerShield 类 timer-aware 宿主；  
- 形式化 WCET 接口；  
- x86_64 manifest 与 cRTOS 同板对照（若硬件可得）。

---

## 附录（Appendices）

### Appendix A — Manifest 与 workload 完整列表  
### Appendix B — 隔离 profile 参数与环境脚本  
### Appendix C — validate.json schema 与 fusion_report 示例  
### Appendix D — cRTOS VEE 2020 实验配置摘要（引用用）  
### Appendix E — Timed C Primitives 与 PLCFusion manifest 域对照（可选）  
### Appendix F — 复现清单（Artifact appendix checklist）

---

## 图表清单（Figures & Tables — 写作前预注册）

| 编号 | 类型 | 内容 |
|------|------|------|
| Fig. 1 | 架构 | cRTOS 双域 vs PLCFusion 单域 split kernelization |
| Fig. 2 | 架构 | PLCFusion 端到端 toolchain（仿 Timed C Fig.1） |
| Fig. 3 | CDF | Soak jitter：userspace / baseline_ko / fused |
| Fig. 4 | Bar/CDF | Stress abs_max |
| Fig. 5 | 消融 | Ablation delta ns |
| Table 1 | 对比 | Related work + design axes |
| Table 2 | 数据 | Pi 主结果（三基线 × soak/stress） |
| Table 3 | 数据 | Cross-study：cRTOS (cited) vs PLCFusion (Pi) |
| Table 4 | 数据 | Feasibility 13 manifests |
| Table 5 | 定性 | 部署复杂度：cRTOS vs PLCFusion |

---

## 目标 venue 与篇幅（建议）

| 档位 | 会议/期刊 | 篇幅 | 说明 |
|------|-----------|------|------|
| A | RTAS / RTSS（短文） | 8–10 pp | 需强化 RQ1 数字 + threat 节 |
| B | EMSOFT / ECRTS | 12–16 pp | 加 WCET/Pass 形式化子节 |
| C | 中文核心 / JCST | 8000–10000 字 | 可扩 §5 文献复述 |
| D | Workshop（RTLinux, OSR） | 6 pp | 当前仓库 artifact 最接近 |

---

## 写作顺序建议（非论文正文，供执行）

1. §4 设计空间表 + Fig.1/2 架构图（定调）  
2. §7–§8 实验协议（先跑 `run_paper_baseline_matrix` 填 Table 2）  
3. §6 PLCFusion 方法（与仓库 README 对齐）  
4. §5 cRTOS 摘要 + Table 3 cross-study  
5. §3 Related Work  
6. §1 Introduction + Abstract（最后润色）  
7. §9–§11 根据真实数据写结论  

---

## 参考文献（必引 starter list）

1. Yang & Shinjo, VEE 2020 — cRTOS / Jailhouse / NuttX.  
2. Natarajan & Broman, RTAS 2018 — Timed C.  
3. Natarajan et al., RTSS 2019 — Timed C end-to-end toolchain.  
4. Pellizzoni et al., RTAS 2011 — PREM.  
5. Patel et al., RTAS 2017 — TimerShield.  
6. Linux PREEMPT_RT / cyclictest documentation.  
7. Gleixner & Niehaus — Linux hrtimers OLS 2006.  
8. （本工作）PLCFusion 仓库 README / validate 门禁说明.

---

*文档版本：2026-06-25 · 路径：`docs/paper/OUTLINE_PLCFUSION_vs_cRTOS__论文大纲.md`*
