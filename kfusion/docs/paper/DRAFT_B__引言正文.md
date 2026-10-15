# 论文初稿·引言章节正文（含假设清单与参考文献）

> 对应大纲：`docs/paper/DRAFT_A__论文详细大纲.md` 的 §1。
> 语言：提供**英文版（期刊投稿用）**与**中文版（审阅用）**，内容一一对应。
> 引用标注：正文中以 `[n]` 标出需引用位置；`【此处需要实验结果】` 表示缺数据，禁止填入猜测值；`【待补文献：主题】` 表示该处需要补充真实文献出处。
> 硬性约束遵守情况见文末「合规自检」。

---

## Part 1 · Introduction（English, journal-ready）

### 1. Introduction

Industrial edge control is migrating onto commodity off-the-shelf (COTS) platforms—single-board computers such as the Raspberry Pi—driven by cost, availability, and the rich software ecosystem of Linux. These platforms increasingly host control loops whose correctness depends not only on the values they compute but also on *when* those values are produced. A control task that satisfies its logical specification but is released late by an unpredictable margin can violate a physical deadline just as surely as an arithmetic error can. Deploying hard real-time control on a general-purpose COTS Linux system is therefore attractive in principle yet difficult in practice.

The difficulty is intrinsic to the COTS execution model. A control loop running as an ordinary user-space thread contends for shared caches and interconnect buses, is exposed to timer and interrupt interference, and shares the processor with background workloads; each of these can inject latency that is hard to bound [3]. Even with the PREEMPT_RT patch, which threads interrupt handlers and adds priority inheritance [4], worst-case latency on commodity hardware remains sensitive to configuration and load, and a substantial engineering effort—CPU isolation, affinity control, and cgroup partitioning—is required to keep jitter within admissible limits.

Two broad philosophies have emerged to attack this problem, and they differ in *where* they intervene. The first restructures the **system**: a partitioning hypervisor carves the hardware into isolated realms, dedicating one realm to a small real-time operating system (RTOS) while Linux retains the rest [1], [5]. The representative *compounded real-time operating system* (cRTOS) [1] runs NuttX [6] as a "swift RTOS" (sRTOS) alongside Linux under Jailhouse [5], reports microsecond-scale jitter and well-bounded latency on x86-64, and lets real-time processes reach Linux services through remote system calls. The second philosophy restructures the **artifact**: rather than changing the system's spatial layout, it changes how the program is delivered, through language or compiler mechanisms. Timed C elevates timing to first-class language primitives compiled by a retargetable source-to-source toolchain [2], [8], whereas the Predictable Execution Model (PREM) re-factors tasks into memory and computation phases using an LLVM-based compiler to bound resource contention [3].

These directions leave a specific gap. Partitioning approaches deliver strong spatial isolation but impose a hypervisor, dual-OS maintenance, and platform assumptions such as virtualization extensions [1], [5]; they do not apply to a deployment that must remain a *single* Linux kernel image. Language- and interval-based compiler approaches achieve predictability by changing the *programming model* or the *code structure* [2], [3], but assume the program is authored in the new language or is amenable to refactoring, and typically target user-space or bare-metal execution. What is missing is a mechanism that takes *existing, unmodified POSIX C* and delivers its hot control path into *kernel context within an unmodified single PREEMPT_RT domain*—without a hypervisor and without requiring a new language—while preserving source-level semantics and yielding reproducible, reportable timing bounds.

We present **K-Fusion**, a compiler-guided *split kernelization* toolchain that targets exactly this gap. K-Fusion lowers existing C—for example, the `rt-tests` cyclictest source—to LLVM IR, remaps POSIX calls onto a kernel-oriented ABI (`malloc`→`plc_kmalloc`, `pthread_create`→`plc_pthread_create`, `clock_gettime`→`plc_ktime_get_ts`, and roughly eighty further mappings), eliminates code unreachable from declared entry roots (including callees reached only through global function-pointer tables and constant GEP chains), converts floating-point state to fixed-point, and marks hot cycle functions as optimization barriers so that their basic-block structure is preserved for timing analysis. The result is a freestanding `kernel.o` that is linked with a replaceable host module into a loadable `.ko`. After `insmod`, a kernel thread invokes the original `main()`/cycle function in kernel context, while the host supplies pinned `hrtimer` timing and thread substitutes. The system occupies a single PREEMPT_RT domain: there is no hypervisor, no second OS, and no whole-process translation—only a different *delivery form* for the same source code.

We structure the study around four research questions. **RQ1 (mechanism):** on a COTS aarch64 platform, can compiler-guided kernelization achieve period-jitter bounds of the same *order* as the cRTOS numbers reported in the literature, without hypervisor partitioning? **RQ2 (cost):** relative to cRTOS, what is the development, deployment, and migration cost of K-Fusion's manifest-and-single-module model compared with a dual-realm design? **RQ3 (ecosystem):** can rich Linux features and hard real-time control coexist within a *single* domain, or is spatial splitting necessary? **RQ4 (methodology):** does a Timed-C-style end-to-end "source → measurement → reportable bound" protocol [2], [8] apply to a kernelization path?

Our contributions are as follows. **(C1)** A formalized comparison of the design spaces of *compounded RTOS* (cRTOS) and *compiler-guided split kernelization* (K-Fusion), expressed as design axes and an architecture figure. **(C2)** A reproducible, end-to-end K-Fusion toolchain and experimental protocol with continuous-integration gate metrics and paper-generation scripts. **(C3)** Measurements on the Raspberry Pi along the same metric dimensions used by cRTOS—period jitter, maximum deviation, and behavior under background load. **(C4, optional)** A study of a WCET-oriented pass/autotune combination as a compile–run co-design. **(C5, optional)** A second-application validation (signaltest and a multi-task logical-execution-time workload) showing that the approach is not cyclictest-specific.

We deliberately frame this work as a **comparative study under different isolation assumptions**, not a contest. The cRTOS results were obtained on x86-64 with Jailhouse [1], whereas our measurements are on aarch64 PREEMPT_RT without a hypervisor. Cross-platform, cross-mechanism numbers are therefore reported side by side in separate columns and are never claimed to "beat" cRTOS; where the mechanisms differ—spatial isolation versus in-place acceleration—we state the trade-off explicitly. All experiments use a fixed kernel version, a shared CPU-isolation profile, and a common measurement duration, and the provenance of every baseline (cited versus reproduced) is labeled throughout. The headline quantitative results are reported in Section 9; as noted there, the values depend on the platform configuration and load profile and are given only after measurement.

The remainder of this paper is organized as follows. Section 2 provides background; Section 3 reviews related work; Section 4 formalizes the problem, the design space, and the assumptions; Section 5 summarizes the cRTOS baseline; Section 6 describes the K-Fusion toolchain; Section 7 details the implementation; Section 8 defines the experimental methodology; Section 9 reports results against RQ1–RQ4; Section 10 discusses design implications; Section 11 examines threats to validity; and Section 12 concludes.

---

## Part 2 · 引言（中文对照）

### 1. 引言

工业边缘控制正迁移到通用商用现货（COTS）平台——如树莓派这类单板计算机——其驱动力来自成本、可获得性以及 Linux 丰富的软件生态。这类平台越来越多地承载控制回路，而控制回路的正确性不仅取决于其计算结果，还取决于结果于**何时**产生。一个满足逻辑规格、却以不可预测的裕度被延迟释放的控制任务，其后果与算术错误同样严重。因此，在通用 COTS Linux 系统上部署硬实时控制在原理上具有吸引力，在实践中却十分困难。

这种困难源于 COTS 执行模型本身。作为普通用户态线程运行的控制回路会竞争共享的 cache 与互连总线，暴露于定时器与中断干扰之下，并与后台负载共享处理器；上述每一项都可能注入难以界定的延迟 [3]。即便采用 PREEMPT_RT 补丁（将中断处理线程化并引入优先级继承）[4]，商品化硬件上的最坏延迟仍对配置与负载高度敏感，必须投入大量工程手段——CPU 隔离、亲和性控制、cgroup 划分——才能把抖动约束在可接受范围内。

针对该问题已形成两种主要哲学，区别在于**干预的位置**。其一改造**系统**：由分区 hypervisor 把硬件切分为相互隔离的 realm，将一个 realm 专用于小型实时操作系统（RTOS），其余留给 Linux [1], [5]。代表性的**组合式实时操作系统**（cRTOS）[1] 在 Jailhouse [5] 之上让 NuttX [6] 作为"敏捷 RTOS"（sRTOS）与 Linux 并存，在 x86-64 上报告了微秒级抖动与有界延迟，并允许实时进程通过远程系统调用访问 Linux 服务。其二改造**产物**：不改变系统的空间布局，而是改变程序的**交付形态**，借助语言或编译器机制实现。Timed C 将时间行为提升为一等语言原语，由可重定目标的源到源工具链编译 [2], [8]；可预测执行模型（PREM）则借助基于 LLVM 的编译器把任务重构成内存相与计算相，以界定资源争用 [3]。

这两条方向留下了一个明确的缺口。分区类方法提供强空间隔离，却引入 hypervisor、双 OS 维护以及平台假设（如虚拟化扩展）[1], [5]，并且不适用于必须保持为**单一** Linux 内核镜像的部署。语言类与区间类编译器方法通过改变**编程模型**或**代码结构**获得可预测性 [2], [3]，但假定程序以新语言编写或可被重构，且通常面向用户态或裸机执行。真正缺失的，是一种能接收**既有的、未修改的 POSIX C**，并在**未修改的单一 PREEMPT_RT 域内**把其热控制路径送入**内核上下文**的机制——既不需要 hypervisor，也不需要新语言——同时保留源码级语义并给出可复现、可报告的时序上界。

为此我们提出 **K-Fusion**，一套面向该缺口的编译引导**分裂式内核化**工具链。K-Fusion 将既有 C（例如 `rt-tests` 的 cyclictest 源码）下降为 LLVM IR，将 POSIX 调用重映射到面向内核的 ABI（`malloc`→`plc_kmalloc`、`pthread_create`→`plc_pthread_create`、`clock_gettime`→`plc_ktime_get_ts`，另有约八十条映射），删除从声明入口根不可达的代码（包括仅通过全局函数指针表与常量 GEP 链可达的被调者），将浮点状态转换为定点，并把热周期函数标记为优化屏障，以保留其基本块结构供时序分析。产物是一个 freestanding 的 `kernel.o`，与可替换的宿主模块链接为可加载 `.ko`。`insmod` 之后，内核线程在内核上下文中调用原始的 `main()`/周期函数，宿主则提供 pinned `hrtimer` 定时与线程替身。整个系统位于**单一 PREEMPT_RT 域**：没有 hypervisor，没有第二个 OS，也没有整进程翻译——只是同一份源码的**另一种交付形态**。

我们围绕四个研究问题组织本文。**RQ1（机制）**：在 COTS aarch64 平台上，编译引导的内核化能否在不做 hypervisor 分区的前提下，达到与文献中 cRTOS 数字**同量级**的周期抖动上界？**RQ2（成本）**：相对 cRTOS，K-Fusion 的 manifest + 单模块模型相较双 realm 设计，在开发、部署与迁移上的成本如何？**RQ3（生态）**：Linux 富功能与硬实时控制能否在**同一域内**共存，还是必须做空间切分？**RQ4（方法论）**：Timed C 式的端到端"源码 → 测量 → 可报告上界"协议 [2], [8] 是否适用于内核化路径？

本文贡献如下。**（C1）** 对**组合式 RTOS**（cRTOS）与**编译引导分裂式内核化**（K-Fusion）的设计空间作形式化对比，以设计轴与架构图表达。**（C2）** 提供可复现的 K-Fusion 端到端工具链与实验协议，含持续集成门禁指标与论文生成脚本。**（C3）** 在树莓派上按与 cRTOS 相同的指标维度测量——周期抖动、最大偏差、以及背景负载下的行为。**（C4，可选）** 研究 WCET 导向的 Pass/autotune 组合作为"编译–运行协同"。**（C5，可选）** 第二应用验证（signaltest 与一个多任务 LET 负载），证明方法并非 cyclictest 专用。

我们刻意将本文定位为**不同隔离假设下的对照研究**，而非竞赛。cRTOS 的结果取自 x86-64 + Jailhouse [1]，而我们的测量取自无 hypervisor 的 aarch64 PREEMPT_RT。因此，跨平台、跨机制的数值分栏并列呈现，**绝不**声称"击败"cRTOS；当机制不同（空间隔离 vs 就地加速）时，我们明确陈述其权衡。所有实验采用固定内核版本、共享的 CPU 隔离 profile 与相同的测量时长，并全程标注每个基线的来源（引用 vs 复现）。核心量化结果见第 9 节；如该节所述，这些数值依赖平台配置与负载剖面，**仅在测量完成后给出**。

全文组织如下。第 2 节给出背景；第 3 节综述相关工作；第 4 节形式化问题、设计空间与假设；第 5 节概述 cRTOS 基线；第 6 节描述 K-Fusion 工具链；第 7 节详述实现；第 8 节定义实验方法；第 9 节按 RQ1–RQ4 报告结果；第 10 节讨论设计含义；第 11 节分析效度威胁；第 12 节总结。

---

## Part 3 · 模型与范围假设（显式清单）

> 写作硬性约束要求"所有模型假设需要明确列出"。以下假设贯穿引言与全文；若某条被放宽，须在对应章节声明。

- **A1 平台假设**：实验在 Raspberry Pi（aarch64）上、运行 PREEMPT_RT 内核；单板、单核隔离配置固定。具体型号/内存/存储 `【实验时填】`。
- **A2 执行域假设**：实时控制路径在可加载模块（`.ko`）内以 kthread + pinned hrtimer 执行；**不假设** spatial isolation，也不假设完全无干扰——仅主张在固定隔离配置下抖动的**可重复上界**。
- **A3 时序语义假设**：`manifest` 描述/Pass 标注**不构成**形式化 deadline 规格；时间行为由宿主定时器 + 调度保证，**非**静态可证明的 WCET。本文的"硬实时"是 measurement-based（见 §4.1）。
- **A4 对比假设**：cRTOS 数字来自原文 x86-64 平台，作为 **cross-study 引用**；本文**不**在 Pi 上复现完整 cRTOS，故数值对比为"同指标、不同平台/机制"。
- **A5 负载假设**：主负载为周期任务（cyclictest 主线）；泛化由第二应用（signaltest）与多任务 LET 负载覆盖；真实 PLC/工业负载不在本轮范围内。
- **A6 内核与工具链假设**：固定 `uname -r` 与 LLVM 版本；`PLC_PLATFORM` 固定。`【实验时填具体版本】`
- **A7 统计假设**：每配置多次独立 run（默认 `PAPER_RUNS=5`），报告分布/分位；**不**以单次运行下结论。
- **A8 未建模项**：cache/总线争用、DMA、DVFS/温度、驱动栈深度**不**作为显式建模对象，仅通过隔离 profile 弱控制；其残余影响列入效度威胁（§11）。
- **A9 迁移假设**：假设目标程序为**单进程、POSIX C、无 C++/fork/dlopen/socket 等预检 critical**（`plc_fusion_preflight` 规则）；不满足者不在范围内。
- **A10 运行约束假设**：单一 PREEMPT_RT 域内**同时仅加载一个 fused 控制模块**（受符号/宿主模型约束）。

---

## Part 4 · 参考文献（仅列真实文献，附核实状态）

> 原则：**不编造文献**。以下仅列出已核实或高置信的真实条目；未核实完整出处者以 `【待核实】` 标注，正文相应位置以 `【待补文献】` 提示。

| 编号 | 文献 | 核实状态 |
|------|------|----------|
| [1] | C.-F. Yang and Y. Shinjo, "Obtaining hard real-time performance and rich Linux features in a compounded real-time operating system by a partitioning hypervisor," in *Proc. 16th ACM SIGPLAN/SIGOPS Int. Conf. on Virtual Execution Environments (VEE '20)*, 2020, pp. 59–72. DOI: 10.1145/3381052.3381323. | ✅ 已核实 |
| [2] | S. Natarajan and D. Broman, "Timed C: An extension to the C programming language for real-time systems," in *Proc. IEEE Real-Time and Embedded Technology and Applications Symposium (RTAS)*, 2018, pp. 227–239. DOI: 10.1109/RTAS.2018.00031. | ✅ 已核实 |
| [3] | R. Pellizzoni, E. Betti, S. Bak, G. Yao, J. Criswell, M. Caccamo, and R. Kegley, "A predictable execution model for COTS-based embedded systems," in *Proc. 17th IEEE Real-Time and Embedded Technology and Applications Symposium (RTAS)*, 2011, pp. 269–279. DOI: 10.1109/RTAS.2011.33. | ✅ 已核实 |
| [4] | Linux PREEMPT_RT patch / real-time Linux documentation（项目文档）. | ✅ 实体真实（引用格式待定） |
| [5] | Jailhouse partitioning hypervisor（项目文档/仓库）. | ✅ 实体真实（引用原始论文 `【待补文献：Jailhouse 原始论文】`） |
| [6] | Apache NuttX（POSIX-compliant RTOS，项目文档）. | ✅ 实体真实 |
| [7] | cyclictest / rt-tests（工具文档）. | ✅ 实体真实 |
| [8] | S. Natarajan et al., Timed C end-to-end toolchain, *RTSS 2019*. | ⚠️ 主题真实（Timed C toolchain 存在于 RTSS 2019），**完整标题/页码待核实** |
| [9] | P. Patel et al., "TimerShield," *RTAS 2017*. | ⚠️ **待核实完整出处**（作者/标题/页码） |
| [10] | T. Gleixner and D. Niehaus, "Hrtimers and beyond," *OLS 2006*. | ⚠️ **待核实完整出处** |

**高置信待补文献（正文中出现 `【待补文献：…】` 者）**
- `【待补文献：Jailhouse 原始论文（如 Siemens, "Jailhouse"）】`
- `【待补文献：Xenomai 3 架构】`
- `【待补文献：LKL 或 eBPF 在内核中执行用户代码】`
- `【待补文献：Linux cgroup v2 / CPU 隔离（isolcpus）官方文档】`

---

## Part 5 · 合规自检（对照用户硬性约束）

| 约束 | 执行情况 |
|------|----------|
| 严禁编造实验数据 | 正文未出现任何具体抖动/延迟数值；结论数值以 `【此处需要实验结果】` 占位。 |
| 严禁编造不存在的文献 | 参考文献仅列已核实或高置信真实条目；不确定者标 `【待核实】`，正文对应处标 `【待补文献】`。 |
| 严禁编造不存在的指标 | 所有指标（period jitter、abs_max、p99、wake-to-run latency 等）均来自仓库既有协议定义，未新增杜撰指标。 |
| 缺数据处标注 | 通篇使用 `【此处需要实验结果】`，见 Abstract 结论句、§9 各表与图。 |
| 模型假设明确列出 | 见 Part 3（A1–A10）。 |
| 标出引用位置 | 正文用 `[n]` 标注；`[8]`–`[10]` 附核实状态。 |
| 学术书面语、逻辑递进 | 引言按"背景→困难→两条路线→缺口→本文方法→RQ→贡献→公平性→结构"漏斗递进。 |

---

*文档：`docs/paper/DRAFT_B__引言正文.md` · 配套大纲：`docs/paper/DRAFT_A__论文详细大纲.md`*
