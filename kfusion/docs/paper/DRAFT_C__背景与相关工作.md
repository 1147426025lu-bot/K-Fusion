# 论文初稿·§2 背景 + §3 相关工作（正文）

> 对应大纲：`docs/paper/DRAFT_A__论文详细大纲.md` 的 §2、§3。
> 语言：英文版（投稿用）+ 中文对照，内容一一对应。
> 引用标注：`[n]` 对应本文末尾参考文献表；`【此处需要实验结果】` 表示缺数据；`【待补文献：主题】` 表示需补真实出处。**不编造文献、数据、指标。**

---

# Part 1 · §2 Background（English）

## 2. Background

### 2.1 The COTS real-time Linux stack

The PREEMPT_RT patch is the principal mechanism for reducing worst-case latency in mainline Linux [4]. It converts most interrupt handlers into schedulable kernel threads with their own priorities, applies priority inheritance to sleeping and spin locks, and makes nearly the entire kernel preemptible. Its design objective is bounded worst-case response time rather than raw throughput, and it is now largely merged toward mainline. Critically, however, PREEMPT_RT bounds the *scheduler-visible* portion of latency; on commodity hardware the residual worst case still depends on shared-cache and interconnect behavior and on concurrent activity, both of which are difficult to bound from software alone [3], [9].

A periodic control loop in user space is conventionally implemented by arming a timer and blocking until it fires; the de-facto measurement tool cyclictest [7] follows exactly this pattern and reports the distribution of the deviation between the requested and the actual wake-up instants. The jitter measured this way is therefore a property of the kernel wake-up path—timer expiry, scheduler dispatch, and syscall return—and not of the application alone. This observation is central to our methodology: a user-space baseline and a kernel-resident implementation differ in *where* the hot path executes, and jitter differences must be attributed accordingly.

The alternative to user-space execution is to run the control code in kernel context: a kernel thread (kthread) scheduled with real-time FIFO policy and driven by a high-resolution timer (hrtimer) [10]. This removes user/kernel transitions from the periodic path but must itself be isolated from other kernel activity. K-Fusion adopts this execution model, as detailed below.

### 2.2 Compounded RTOS (cRTOS) — Baseline A

The compounded real-time operating system (cRTOS) [1] constructs a system from two cooperating realms: a *normal realm* running Linux, and a *hard real-time realm* running a small real-time operating system designated the "swift RTOS" (sRTOS). Isolation is provided by the Jailhouse partitioning hypervisor [5]. Jailhouse transforms a symmetric multiprocessing (SMP) system into an asymmetric multiprocessing (AMP) system by inserting "virtual barriers" into the system and I/O buses using hardware virtualization extensions; it deliberately provides neither a scheduler nor virtual CPUs, assigns devices directly to isolated domains ("cells"), and is enabled from within a fully booted Linux, which it lifts into a virtual machine. Because Jailhouse relies on virtualization extensions, its deployment is coupled to hardware support—x86 VT-x/AMD-V or ARM virtualization—and to a per-platform cell configuration [5].

In cRTOS, a rich real-time process executing in the real-time realm reaches Linux functionality through *remote system calls* over shared memory, allowing it to combine RTOS timing with Linux services without modifying the Linux kernel [1]. The authors report, on x86-64, jitter of approximately 4 µs together with well-bounded maximum latency, and demonstrate execution of Linux graphical executables inside the real-time realm [1]. These figures are, however, obtained on x86-64 under Jailhouse; they are not measurements on a COTS aarch64 single-board computer, and we treat them strictly as a *cited* baseline (see §4.4 and §9.2).

The cost of the approach is structural: a hypervisor dependency, a second operating system to maintain, hardware virtualization requirements, and the latency of cross-domain calls [1], [5]. These costs motivate the alternative studied in this paper, which is to keep a *single* PREEMPT_RT domain and change only how the program is delivered.

### 2.3 Compiler-guided split kernelization (K-Fusion) — this work

K-Fusion is a compiler-guided toolchain that lowers existing user-space C into a form that executes inside a loadable Linux kernel module, without hypervisor partitioning and without a second operating system. The pipeline proceeds from C through LLVM IR, applies a fixed sequence of transformations, and produces a freestanding object `kernel.o` that is linked with a replaceable host module into a loadable `.ko`. After `insmod`, a kthread invokes the program's cycle function (`main()` / `timerthread()` / `plc_main()`) in kernel context, while the host supplies pinned `hrtimer` timing and substitutes for POSIX threading primitives.

Two properties distinguish this from existing approaches. First, it is a *split* transformation rather than a whole-process translation: the algorithmic core lives in `kernel.o`, whereas startup, ABI shimming, and teardown live in the replaceable host component, so the same fused object can be paired with different hosts. Second, it is *driven by a manifest*: each application is described by a manifest that declares its entry roots, sources, and options, from which the toolchain derives feasibility, remaps POSIX calls onto a kernel-oriented ABI (`plc_*`), eliminates unreachable code, and emits validation artifacts. The full method is the subject of §6.

### 2.4 Timed C and the end-to-end toolchain — a structural template

Timed C extends the C language with a minimal set of timing primitives—timing points (`sdelay`, `fdelay`), tasks, and scheduling policies—compiled by a retargetable source-to-source compiler, thereby making timing behavior explicit and amenable to analysis [2]. A subsequent end-to-end toolchain couples compilation, measurement, and analysis into a single workflow [8]. We stress a deliberate scope boundary: we borrow from this line of work the *structure* of an end-to-end toolchain and the discipline of reporting measurement-based bounds, not the language semantics. K-Fusion does not introduce timing primitives into the source language; it changes the *delivery form* of unmodified C. The two are therefore complementary rather than competing, and §3.4 and §10.2 discuss how they might compose.

---

# Part 2 · §3 Related Work（English）

## 3. Related Work

### 3.1 Real-time extensions of Linux

The dominant single-kernel approach is PREEMPT_RT [4], which we summarized in §2.1. A second widely used approach is Xenomai, which can run either as a *dual-kernel* system—a small co-kernel (Cobalt) handling time-critical activity beside Linux—or in a *single-kernel* configuration (Mercury) over mainline, optionally with PREEMPT_RT [11]. Both cRTOS [1] and the present work are evaluated in relation to these designs: cRTOS compared against PREEMPT_RT and Xenomai 3 in its original study [1], and K-Fusion against user-space and hand-written kernel-module baselines under a shared PREEMPT_RT configuration. The distinguishing feature of K-Fusion is not a new kernel extension but a compiler that relocates existing C into the kernel without a co-kernel or a hypervisor.

### 3.2 Virtualization and partitioned real-time

Static partitioning hypervisors isolate workloads by assigning hardware resources directly to domains [5]. Jailhouse [5] is representative: it favors simplicity over features, provides no scheduler or virtual CPUs, and confines itself to isolation via virtualization extensions. cRTOS builds on Jailhouse to place a hard real-time realm beside Linux [1], and related mixed-criticality architectures follow the same static-partitioning philosophy; other partitioned designs such as XtratuM and PikeOS similarly manage resources on behalf of guests rather than granting direct access [5]. This family of approaches delivers strong *spatial* isolation, at the cost of a hypervisor layer, hardware virtualization support, and per-platform configuration. K-Fusion occupies a complementary cell in the design space: it seeks *temporal* predictability in a single, unpartitioned domain by intervening at compile time rather than at the system-structure level.

### 3.3 Compilers, WCET, and in-kernel execution

Bounding execution time is a prerequisite for hard real-time guarantees, and the difficulty arises from architectural features—caches, pipelines, branch prediction—that make instruction timing history-dependent; the field distinguishes measurement-based and static-analysis approaches, with the latter guaranteeing an upper bound at the cost of pessimism [9]. The Predictable Execution Model (PREM) attacks the problem at compile time by re-factoring tasks into memory and computation phases so that shared-resource access is confined to known intervals, implemented through modifications to the LLVM infrastructure [3]. K-Fusion shares PREM's conviction that the compiler is a legitimate instrument of predictability, but applies it to a different lever: rather than re-phasing computation, it remaps a program's runtime dependencies onto an in-kernel ABI and prunes unreachable code, so that an existing control loop can execute in kernel context. A related, orthogonal family delivers user code inside the kernel through sandboxed bytecode (eBPF) or a re-hosted kernel library (LKL), or eliminates the general-purpose OS altogether (unikernels) `【待补文献：eBPF 内核执行 / LKL / unikernel MirageOS】`. These approaches trade access to the source-level C program for a restricted runtime; K-Fusion instead preserves the original program's IR and makes its kernelization reproducible through manifests.

### 3.4 Language-level real-time extensions

A separate line raises timing to the level of the *language*: Timed C provides timing points, tasks, and scheduling policies with a retargetable source-to-source compiler [2], and its end-to-end toolchain integrates compilation, measurement, and analysis [8]; languages in the wider family, including Ada with the Ravenscar profile and time-triggered languages such as Giotto, likewise make temporal structure explicit `【待补文献：Ada/Ravenscar 与 Giotto】`. These approaches make timing constraints readable and statically analyzable, but require programs to be authored in the corresponding dialect. K-Fusion is complementary: where a Timed C-style shell specifies *when* and *with what policy* a task runs, K-Fusion determines *how* an existing C body is executed inside the kernel (see §10.2). Our experiments deliberately keep the baselines separate so that jitter differences are not conflated with the change of programming model.

### 3.5 Summary

Table 1 positions K-Fusion against the closest systems along the axes of core mechanism, isolation/execution domain, required Linux-kernel modification, and typical platform. The table makes explicit the two ways in which K-Fusion differs from cRTOS: it requires no hypervisor and no dual-OS maintenance, but correspondingly does not by itself provide spatial isolation or transparent Linux-binary compatibility.

**Table 1. Positioning of K-Fusion against representative systems.**
| Work | Core mechanism | Isolation / execution domain | Linux-kernel modification | Typical platform |
|------|----------------|------------------------------|---------------------------|------------------|
| cRTOS [1] | Partitioning hypervisor + sRTOS | Spatial dual realm | None (partitioned) | x86 + Jailhouse [5] |
| PREEMPT_RT [4] | Kernel preemption + IRQ threading | Single domain, scheduling isolation | Yes (RT patch) | Many |
| Xenomai 3 [11] | Dual-kernel co-kernel / native | Single or dual kernel | Co-kernel build | Many |
| Timed C / KTC [2], [8] | Language + source-to-source transform | User-space single domain | None | POSIX / FreeRTOS |
| **K-Fusion (this work)** | LLVM split kernelization | **Kernel module, single domain** | None (loadable module) | **Pi / aarch64** |

---

# Part 3 · §2 背景（中文对照）

## 2. 背景

### 2.1 COTS 实时 Linux 栈

PREEMPT_RT 补丁是降低主线 Linux 最坏延迟的主要机制 [4]。它把大多数中断处理程序转换为具备独立优先级的可调度内核线程，对睡眠锁与自旋锁施加优先级继承，并使几乎整个内核可被抢占。其设计目标是**有界的最坏响应时间**而非原始吞吐，如今已大体并入主线。关键在于，PREEMPT_RT 约束的是**调度器可见**的那部分延迟；在商品化硬件上，残余的最坏情形仍取决于共享 cache 与互连行为以及并发活动，而这些难以仅靠软件界定 [3], [9]。

用户态周期控制回路通常通过装载定时器并阻塞等待其到期来实现；事实标准测量工具 cyclictest [7] 正是此模式，它报告"请求唤醒时刻"与"实际唤醒时刻"之差的分布。由此测得的抖动因此是**内核唤醒路径**（定时器到期、调度分派、系统调用返回）的属性，而非仅应用自身的属性。这一点对本文方法论至关重要：用户态基线与内核内实现在**热路径执行位置**上不同，抖动差异必须据此归因。

用户态执行的替代方案，是让控制代码在内核上下文中运行：一个以实时 FIFO 策略调度的内核线程（kthread），由高精度定时器（hrtimer）驱动 [10]。这从周期路径中移除了用户/内核态切换，但其自身必须与其他内核活动隔离。K-Fusion 采用这一执行模型，详见下文。

### 2.2 组合式实时操作系统（cRTOS）— 基线 A

组合式实时操作系统（cRTOS）[1] 由两个协作 realm 构成：运行 Linux 的*普通 realm*，以及运行一个小型实时操作系统（即"敏捷 RTOS"，sRTOS）的*硬实时 realm*。隔离由 Jailhouse 分区 hypervisor 提供 [5]。Jailhouse 借助硬件虚拟化扩展，在系统总线与 I/O 总线上插入"虚拟屏障"，把对称多处理（SMP）系统转换为非对称多处理（AMP）系统；它刻意不提供调度器与虚拟 CPU，将设备直接分配给相互隔离的域（"cell"），并从已完整启动的 Linux 内部启用，同时把该 Linux 提升为虚拟机。由于依赖虚拟化扩展，其部署与硬件支持（x86 VT-x/AMD-V 或 ARM 虚拟化）以及逐平台的 cell 配置相耦合 [5]。

在 cRTOS 中，运行于实时 realm 的富实时进程通过共享内存上的**远程系统调用**访问 Linux 功能，从而在不修改 Linux 内核的前提下兼顾 RTOS 时序与 Linux 服务 [1]。作者在 x86-64 上报告了约 4 µs 的抖动与有界最大延迟，并演示了在实时 realm 内执行 Linux 图形可执行程序 [1]。但请注意，这些数字是在 x86-64 + Jailhouse 下获得的，并非在 COTS aarch64 单板计算机上的测量；我们严格将其作为**引用**基线处理（见 §4.4 与 §9.2）。

该方法的代价是结构性的：hypervisor 依赖、需维护第二个操作系统、硬件虚拟化要求，以及跨域调用的延迟 [1], [5]。正因如此，本文研究另一条路线——保持**单一** PREEMPT_RT 域，只改变程序的交付方式。

### 2.3 编译引导的分裂式内核化（K-Fusion）— 本文工作

K-Fusion 是一套编译引导的工具链，把既有的用户态 C 下降为可在可加载 Linux 内核模块内执行的形式，无需 hypervisor 分区、也无需第二个操作系统。流水线从 C 经 LLVM IR，施加固定的变换序列，产出 freestanding 目标 `kernel.o`，再与可替换的宿主模块链接为可加载 `.ko`。`insmod` 之后，内核线程在内核上下文中调用程序的周期函数（`main()` / `timerthread()` / `plc_main()`），宿主则提供 pinned `hrtimer` 定时并替代 POSIX 线程原语。

有两点使其区别于既有方法。其一，它是**分裂**变换而非整进程翻译：算法核心位于 `kernel.o`，而启动、ABI 适配与卸载位于可替换的宿主组件，因此同一融合对象可与不同宿主搭配。其二，它**由 manifest 驱动**：每个应用由一份 manifest 描述其入口根、源与选项，工具链据此推导可行性、把 POSIX 调用重映射到面向内核的 ABI（`plc_*`）、删除不可达代码并产出验证工件。完整方法见 §6。

### 2.4 Timed C 与端到端工具链 — 一个结构模板

Timed C 以一组最小时间原语扩展 C 语言——时间点（`sdelay`、`fdelay`）、任务与调度策略——由可重定目标的源到源编译器编译，从而使时间行为显式化、可分析 [2]。随后的端到端工具链把编译、测量与分析耦合为单一工作流 [8]。我们强调一条刻意的范围边界：我们借用的是这类工作的**端到端工具链结构**与"报告基于测量的上界"的纪律，而非语言语义。K-Fusion 不向源语言引入时间原语，而是改变未修改 C 的**交付形态**。因此二者互补而非竞争，§3.4 与 §10.2 讨论其组合方式。

---

# Part 4 · §3 相关工作（中文对照）

## 3. 相关工作

### 3.1 Linux 的实时扩展

主流单内核路线是 PREEMPT_RT [4]（见 §2.1）。另一被广泛采用的方案是 Xenomai，它既可作为**双内核**系统运行——由小型协同内核（Cobalt）在 Linux 旁处理时敏活动——也可在主线之上以**单内核**配置（Mercury）运行，可选叠加 PREEMPT_RT [11]。cRTOS [1] 与本文均在与此类设计的关系中被评估：cRTOS 在其原始研究中对照 PREEMPT_RT 与 Xenomai 3 [1]，而 K-Fusion 则在共享的 PREEMPT_RT 配置下对照用户态基线与手写内核模块基线。K-Fusion 的特征不在于新的内核扩展，而在于一个把既有 C 在不引入协同内核或 hypervisor 的前提下搬入内核的编译器。

### 3.2 虚拟化与分区实时

静态分区 hypervisor 通过把硬件资源直接分配给各域来隔离负载 [5]。Jailhouse [5] 具有代表性：它以简化优先于功能，不提供调度器与虚拟 CPU，仅借助虚拟化扩展做隔离。cRTOS 在其之上把硬实时 realm 置于 Linux 旁 [1]，其他混合关键性架构亦遵循同一静态分区哲学；诸如 XtratuM、PikeOS 这样的分区式设计同样代表 guest 管理资源，而非授予直接访问 [5]。这一族方法提供强**空间**隔离，代价是 hypervisor 层、硬件虚拟化支持与逐平台配置。K-Fusion 占据设计空间中互补的一格：它在单一、未分区的域内，通过编译期干预而非系统结构层面的干预，追求**时间**可预测性。

### 3.3 编译器、WCET 与内核内执行

界定执行时间是硬实时保证的前提，其困难源于体系结构特性——cache、流水线、分支预测——这些使指令时序依赖历史；该领域区分测量法与静态分析法，后者以保证上界为代价换来保守性 [9]。可预测执行模型（PREM）在编译期入手，把任务重构为内存相与计算相，使共享资源访问被限制在已知区间，并通过对 LLVM 基础设施的改造实现 [3]。K-Fusion 与 PREM 共享"编译器是获得可预测性的正当工具"这一信念，但作用于不同的杠杆：它不重排计算相位，而是把程序的运行时依赖重映射到内核内 ABI 并剪除不可达代码，使既有控制回路能于内核上下文执行。一族相关而正交的工作通过沙箱字节码（eBPF）或重宿主的 Linux 内核库（LKL）把用户代码送入内核，或干脆消除通用 OS（unikernel）`【待补文献：eBPF 内核执行 / LKL / unikernel MirageOS】`。这些方法以受限运行时为代价换取源码级 C 的访问；K-Fusion 则保留原始程序的 IR，并通过 manifest 使其内核化可复现。

### 3.4 语言级实时扩展

另一条独立支线把时间提升到**语言**层面：Timed C 提供时间点、任务与调度策略，并配以可重定目标的源到源编译器 [2]，其端到端工具链整合编译、测量与分析 [8]；该更大家族中的语言——包括 Ada 的 Ravenscar profile 与诸如 Giotto 的时间触发语言——同样使时间结构显式化 `【待补文献：Ada/Ravenscar 与 Giotto】`。这些方法使时间约束可读且可静态分析，但要求程序以相应方言编写。K-Fusion 与之互补：Timed C 式外壳规定任务**何时**、**以何策略**运行，K-Fusion 则决定既有 C 体**如何**在内核内执行（见 §10.2）。我们的实验刻意保持各基线分立，以免把抖动差异与编程模型的变化混为一谈。

### 3.5 小结

表 1 沿"核心机制、隔离/执行域、是否需修改 Linux 内核、典型平台"四轴，把 K-Fusion 与最相近的系统并列定位。该表显式呈现 K-Fusion 与 cRTOS 的两点差异：它无需 hypervisor、无需双 OS 维护；但相应地，它本身**不**提供空间隔离与透明的 Linux 二进制兼容性。

**表 1. K-Fusion 与代表性系统的定位对照。**
（表内容同 Part 2 的表 1，此处略。）

---

## 附：参考文献增补（本轮新增核实项）

| 编号 | 文献 | 核实状态 |
|------|------|----------|
| [5] | R. Ramsauer, J. Kiszka, D. Lohmann, and W. Mauerer, "Look Mum, no VM Exits! (Almost)," in *Proc. 13th Workshop on Operating Systems Platforms for Embedded Real-Time Applications (OSPERT)*, 2017. arXiv:1705.06932. | ✅ 已核实（Jailhouse 系统描述） |
| [9] | R. Wilhelm et al., "The worst-case execution-time problem—overview of methods and survey of tools," *ACM Trans. Embed. Comput. Syst. (TECS)*, vol. 7, no. 3, pp. 1–53, 2008. DOI: 10.1145/1347375.1347389. | ✅ 已核实 |
| [11] | Xenomai 3 real-time framework（官方文档，Cobalt/Mercury 双核心）. | ✅ 实体真实（引用格式待定） |
| [10] | T. Gleixner and D. Niehaus, "Hrtimers and beyond," *OLS 2006*. | ⚠️ **待核实完整出处** |
| [8] | S. Natarajan et al., Timed C end-to-end toolchain, *RTSS 2019*. | ⚠️ **完整标题/页码待核实** |

**正文中出现的待补文献**：`【待补文献：eBPF 内核执行 / LKL / unikernel MirageOS】`、`【待补文献：Ada/Ravenscar 与 Giotto】`。

---

## 合规自检

- **无编造数据**：§2/§3 未出现任何本文自测数值；cRTOS 的 ~4 µs 明确标注为**原文 x86-64 引用值**（[1]）。
- **无编造文献**：[1][2][3][5][9] 已逐一联网核实；[4][6][7][11] 为真实软件/文档；[8][10] 标 `【待核实】`；不明出处一律 `【待补文献：主题】`。
- **无编造指标**：所述指标（jitter、max latency）均来自被引原文或仓库既有协议。
- **逻辑递进**：§2 由"实时 Linux 栈 → 基线 cRTOS → 本文方法 → 结构模板"逐层铺垫；§3 由"单内核 → 分区 → 编译器/WCET → 语言级 → 定位表"层层收束。

---

*文档：`docs/paper/DRAFT_C__背景与相关工作.md` · 前置：`DRAFT_A__论文详细大纲.md`、`DRAFT_B__引言正文.md`*
