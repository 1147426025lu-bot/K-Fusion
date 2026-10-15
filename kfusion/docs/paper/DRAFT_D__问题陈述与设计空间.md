# 论文初稿·§4 问题陈述与设计空间（正文）

> 对应大纲：`docs/paper/DRAFT_A__论文详细大纲.md` 的 §4。
> 语言：英文版（投稿用）+ 中文对照，内容一一对应。
> 引用标注：`[n]` 对应参考文献表；`【此处需要实验结果】` 表示缺数据；`【待补文献：主题】` 表示需补真实出处。**不编造文献、数据、指标。**
> 本文的假设清单在此由 `DRAFT_B` 的要点式（A1–A10）**展开为正文**。

---

# Part 1 · §4（English）

## 4. Problem Statement, Design Space, and Assumptions

This section makes precise what the paper claims. We first give operational definitions of the two properties at stake—predictable timing and rich Linux features—so that the claims are falsifiable (§4.1); we then characterize the design space along which solutions differ (§4.2); we state the assumptions and scope explicitly (§4.3); and we fix the rules that make the comparison fair (§4.4).

### 4.1 Operational definitions

**Predictability.** Throughout this paper, "hard real-time" denotes an *operational* property: under a fixed isolation configuration, the period jitter of a control task—the deviation between the requested and the actual release instant—admits a **reproducible upper bound** that is observed across repeated independent runs. This is a measurement-based bound, not a formal worst-case execution time (WCET) proof; establishing a sound WCET requires processor-level modeling of caches, pipelines, and speculation [9], which is outside our scope. We state this weakening explicitly because it delimits every claim we make: we report the *measured* maximum deviation, its distribution, and the conditions under which it was obtained, and we do not represent it as a proven bound. For a task with nominal period $T$, requested release times $r_i = r_0 + iT$, and actual release instants $a_i$, we define jitter as $|a_i - r_i|$ and report the maximum (`abs_max`) and upper percentiles (e.g., p99) of this quantity, together with the sample count.

**Rich Linux features.** We treat "rich features" as graded rather than binary, because the two baselines satisfy them at different levels. Level 0 is kernel-resident control execution itself—the ability to run periodic control logic in kernel context. Level 1 is *same-domain* access to Linux services: file systems, networking, dynamic module loading, and multi-process execution, reached by ordinary user-space processes in the same PREEMPT_RT domain. Level 2 is *binary compatibility*: the ability to run unmodified existing Linux executables (including graphical applications) within the real-time context. cRTOS targets Level 2 in the real-time realm via remote system calls [1]; K-Fusion provides Level 0 by construction and preserves Level 1 through the surrounding Linux domain, but does **not** by itself provide Level 2 for the kernelized code (see §4.3, assumption A2, and §9.3). Making this grading explicit prevents an equivocation in which "Linux compatibility" means different things for different systems.

### 4.2 Design space

Existing and proposed solutions to the problem occupy a design space that we characterize by a small set of axes. Each axis is a spectrum, and the two systems we compare anchor opposite (but not exhaustive) ends of it.

1. **Spatial isolation vs. in-place acceleration.** cRTOS partitions hardware so that the real-time workload *cannot* physically contend with the general-purpose workload [1], [5]; K-Fusion leaves the system unpartitioned and instead accelerates the control path in place, accepting that isolation must be provided by configuration (CPU affinity, isolation profiles) rather than by hardware partitioning.
2. **Dual OS vs. dual artifact.** cRTOS maintains two operating systems (Linux and an sRTOS) plus a hypervisor [1]; K-Fusion maintains a single kernel image and splits the *artifact* instead—a freestanding algorithmic object plus a replaceable host module.
3. **Remote system call vs. ABI remapping.** cRTOS lets the real-time realm reach Linux functionality through remote system calls over shared memory [1]; K-Fusion remaps the program's POSIX dependencies onto a kernel-oriented ABI (`plc_*`) at compile time, so no cross-domain call occurs on the hot path.
4. **Hypervisor scheduling vs. compiler DCE + host timer.** Isolation and dispatch in cRTOS are the hypervisor's responsibility [5]; in K-Fusion they are the product of compiler dead-code elimination and a pinned host timer combined with OS-level CPU isolation.
5. **Binary compatibility vs. source-level fusion.** cRTOS preserves compatibility with existing Linux executables [1]; K-Fusion requires access to the *source*, which it recompiles and relinks into the kernel—trading binary transparency for source-level control over the fused artifact.
6. **Explicit timing language vs. post-hoc IR transformation** (relative to Timed C [2]): Timed C makes timing a language primitive; K-Fusion applies a post-hoc transformation to code that is otherwise unchanged.
7. **Source-to-source vs. split artifact** (relative to Timed C [2] and PREM [3]): source-to-source schemes emit a user-space program; K-Fusion emits a kernel object linked against a host.
8. **Soft timing point vs. kernel timer interrupt:** the soft timing point of a language-based approach enforces a minimum inter-point delay, whereas K-Fusion's host arms a pinned `hrtimer` whose expiry drives execution in kernel context.
9. **Portability across targets vs. deep single-platform integration:** a source-to-source scheme may target POSIX or a small RTOS; K-Fusion deliberately specializes for a single platform (PREEMPT_RT on aarch64) to extract the lowest achievable jitter.

Axes 1–5 contrast K-Fusion with cRTOS (§5), and axes 6–9 contrast it with the language-and-toolchain line (§3.4). The design space is not a ranking: each choice buys one property at the cost of another, and §10 returns to the question of which choice fits which deployment constraint.

### 4.3 Assumptions and scope

The validity of the results depends on the following assumptions. Each is stated so that a reader can judge—and, if necessary, relax—it.

**A1 — Platform.** Experiments are conducted on a Raspberry Pi (aarch64) running a PREEMPT_RT kernel, with a single board and a fixed per-core isolation configuration. The specific board revision, memory, and storage, together with the kernel release (`uname -r`) and toolchain versions, are pinned and reported in §7. `【实验时填具体版本】`

**A2 — Execution domain.** The real-time control path executes inside a loadable kernel module as a kernel thread driven by a pinned high-resolution timer, while the replaceable host supplies startup, ABI shims, and teardown. We do **not** assume spatial isolation, and we do **not** assume the absence of interference; we claim only a reproducible jitter bound under the fixed isolation configuration of A1. In particular, "hard real-time" here means the operational bound of §4.1, and Linux Level-2 compatibility (§4.1) is not claimed for the kernelized code.

**A3 — Timing semantics.** A manifest and the associated pass annotations describe an application's entry roots, sources, and options; they do **not** constitute a formal deadline specification. Temporal behavior is provided by the host timer and the scheduler, not by a statically provable WCET. This assumption follows from the operational definition in §4.1 and is the reason we report measured rather than proven bounds.

**A4 — Comparison.** The cRTOS figures are those reported by the authors on x86-64 under Jailhouse [1]; we do **not** reproduce the complete cRTOS on the Raspberry Pi. Comparisons are therefore same-metric but cross-platform and cross-mechanism, and are labeled as such wherever they appear (§9.2). We never claim that one system "beats" the other.

**A5 — Workload.** The primary workload is a periodic task (cyclictest as the main line). Generalization is addressed by a second application (signaltest) and by a multi-rate control workload; a production industrial control application is outside the scope of this study.

**A6 — Kernel and toolchain.** The kernel release and the LLVM version are fixed across all runs, and the platform identifier is held constant. `【实验时填具体版本】`

**A7 — Statistics.** Each configuration is repeated across multiple independent runs (the default is five), and results are reported as distributions and percentiles rather than as single-run values. No conclusion is drawn from a single run.

**A8 — Unmodeled factors.** Shared-cache and interconnect contention, DMA, frequency/thermal (DVFS) effects, and driver-stack depth are not modeled explicitly; they are only weakly controlled through the isolation profile. Their residual influence is treated as a threat to validity (§11).

**A9 — Migration preconditions.** The target program is assumed to be single-process POSIX C with no critical constructs that the pre-flight stage rejects (for example, `fork`, `dlopen`, or sockets as designated in the toolchain's pre-flight rules). Programs that violate these conditions are out of scope; the toolchain reports them rather than attempting a best-effort translation.

**A10 — Runtime constraint.** Within a single PREEMPT_RT domain, at most one fused control module is loaded at a time, as dictated by the symbol and host model. Concurrency across multiple fused modules is left to future work (§12).

### 4.4 Fair-comparison doctrine

To make the comparison meaningful despite the differences above, we adopt four rules. First, all within-platform measurements share the **same kernel release** (recorded via `uname -r`). Second, they share the **same CPU-isolation profile** and the same thread-affinity and scheduling policy. Third, they share the **same measurement duration**, with the soak protocol requiring at least fifteen minutes for the formal data `【实验时填具体时长】`. Fourth, the provenance of every number is labeled as **cited** or **reproduced**, so that a reader can never confuse a published figure obtained elsewhere with a measurement obtained here. These rules do not remove the fundamental asymmetry that cRTOS sits on x86-64 with a hypervisor while K-Fusion sits on aarch64 without one; they ensure that the asymmetry is *visible* rather than hidden.

---

# Part 2 · §4（中文对照）

## 4. 问题陈述、设计空间与假设

本节把本文的主张精确化。我们先给出所涉两个属性——可预测时序与 Linux 富功能——的**操作化定义**，使主张可被证伪（§4.1）；随后刻画各方案彼此不同的**设计空间**（§4.2）；接着**显式陈述假设与范围**（§4.3）；最后确定使对比公平的**规则**（§4.4）。

### 4.1 操作化定义

**可预测性。** 本文所称"硬实时"是一个**操作层面**的属性：在固定隔离配置下，控制任务的周期抖动——请求释放时刻与实际释放时刻之差——存在跨多次独立运行可复现的**上界**。这是**基于测量**的上界，而非形式化最坏执行时间（WCET）证明；建立可靠的 WCET 需要对处理器的 cache、流水线与推测执行做体系结构级建模 [9]，超出本文范围。我们显式陈述这一弱化，因为它界定了我们的一切主张：我们报告**实测**最大偏差、其分布以及获得条件，而不将其表述为被证明的上界。对周期为 $T$、请求释放时刻 $r_i = r_0 + iT$、实际释放时刻 $a_i$ 的任务，定义抖动为 $|a_i - r_i|$，报告其最大值（`abs_max`）与上分位（如 p99），并给出样本量。

**Linux 富功能。** 我们把"富功能"视为**分级**而非二元，因为两个基线在不同层级上满足它。Level 0 是内核内控制执行本身——在内核上下文中运行周期控制逻辑的能力。Level 1 是**同域**访问 Linux 服务：文件系统、网络、动态模块加载与多进程执行，由同一 PREEMPT_RT 域内的普通用户态进程达成。Level 2 是**二进制兼容**：能够在实时上下文中运行未修改的既有 Linux 可执行程序（含图形应用）。cRTOS 通过远程系统调用在实时 realm 内面向 Level 2 [1]；K-Fusion 按构造提供 Level 0，并通过外围 Linux 域保持 Level 1，但**本身不**为内核化代码提供 Level 2（见 §4.3 假设 A2 与 §9.3）。显式分级可避免"Linux 兼容性"在不同系统间被偷换含义。

### 4.2 设计空间

既有与本文方案占据一个可用一小组**轴**刻画的設計空间。每条轴是一个谱系，我们对比的两个系统位于其相反（但非穷尽）的两端。

1. **空间隔离 vs 就地加速。** cRTOS 对硬件分区，使实时负载在物理上**无法**与通用负载竞争 [1], [5]；K-Fusion 保持系统未分区，转而就地加速控制路径，接受隔离须由配置（CPU 亲和、隔离 profile）而非硬件分区提供。
2. **双 OS vs 双产物。** cRTOS 维护两个操作系统（Linux 与一个 sRTOS）外加一个 hypervisor [1]；K-Fusion 维护单一内核镜像，转而**分裂产物**——一个 freestanding 算法对象加一个可替换宿主模块。
3. **远程系统调用 vs ABI 重映射。** cRTOS 让实时 realm 经共享内存上的远程系统调用访问 Linux 功能 [1]；K-Fusion 在编译期把程序的 POSIX 依赖重映射到面向内核的 ABI（`plc_*`），使热路径上不发生跨域调用。
4. **hypervisor 调度 vs 编译 DCE + 宿主定时器。** cRTOS 中的隔离与分派由 hypervisor 负责 [5]；K-Fusion 中则来自编译器的死代码删除与一个 pinned 宿主定时器，结合 OS 级 CPU 隔离。
5. **二进制兼容 vs 源码级融合。** cRTOS 保持与既有 Linux 可执行程序的兼容 [1]；K-Fusion 需要**源码**，将其重编译并重链接进内核——以二进制透明性换取对融合产物的源码级控制。
6. **显式时间语言 vs 事后 IR 变换**（相对 Timed C [2]）：Timed C 把时间变为语言原语；K-Fusion 对本来不变的代码施加事后变换。
7. **源到源 vs 分裂产物**（相对 Timed C [2] 与 PREM [3]）：源到源方案产出用户态程序；K-Fusion 产出与宿主链接的内核对象。
8. **软时间点 vs 内核定时器中断**：语言类方法的软时间点强制点间最小延迟，而 K-Fusion 的宿主装载一个 pinned `hrtimer`，其到期驱动内核上下文执行。
9. **跨目标可移植 vs 单平台深度集成**：源到源方案可面向 POSIX 或小型 RTOS；K-Fusion 刻意为单平台（aarch64 上的 PREEMPT_RT）特化，以榨取可达的最低抖动。

轴 1–5 对照 K-Fusion 与 cRTOS（§5），轴 6–9 对照其与语言与工具链支线（§3.4）。设计空间不是排名：每种选择都以一种属性换另一种属性，§10 将回到"哪种选择适配哪类部署约束"。

### 4.3 假设与范围

结果的有效性取决于以下假设。每条均予陈明，以便读者判断并在必要时放宽。

**A1 — 平台。** 实验在运行 PREEMPT_RT 内核的 Raspberry Pi（aarch64）上进行，单板、每核隔离配置固定。具体板卡版本、内存与存储，连同内核版本（`uname -r`）与工具链版本，均在 §7 固定并报告。`【实验时填具体版本】`

**A2 — 执行域。** 实时控制路径作为内核线程在可加载内核模块内执行，由 pinned 高精度定时器驱动，可替换宿主提供启动、ABI 适配与卸载。我们**不**假设空间隔离，也**不**假设无干扰；我们只主张在 A1 的固定隔离配置下可复现的抖动上界。特别地，此处的"硬实时"指 §4.1 的操作性上界，且**不**为内核化代码主张 §4.1 的 Linux Level-2 兼容性。

**A3 — 时序语义。** manifest 及相应 Pass 标注描述应用的入口根、源与选项；它们**不**构成形式化 deadline 规格。时间行为由宿主定时器与调度器提供，而非可静态证明的 WCET。该假设源于 §4.1 的操作性定义，也是我们报告实测而非证明上界的原因。

**A4 — 对比。** cRTOS 数字取自作者在 x86-64 + Jailhouse 下报告的结果 [1]；我们**不**在树莓派上复现完整 cRTOS。因此对比是"同指标、跨平台、跨机制"，并在出现处（§9.2）如实标注。我们绝不声称一方"击败"另一方。

**A5 — 负载。** 主负载为周期任务（以 cyclictest 为主线）。泛化由第二应用（signaltest）与一个多速率控制负载覆盖；生产级工业控制应用不在本研究范围内。

**A6 — 内核与工具链。** 内核版本与 LLVM 版本在全部运行中固定，平台标识保持常量。`【实验时填具体版本】`

**A7 — 统计。** 每个配置跨多次独立运行重复（默认五次），结果以分布与分位报告，而非单次值。不从单次运行下结论。

**A8 — 未建模因素。** 共享 cache 与互连争用、DMA、频率/温度（DVFS）效应与驱动栈深度不被显式建模，仅通过隔离 profile 弱控制。其残余影响列为效度威胁（§11）。

**A9 — 迁移前提。** 目标程序假设为单进程 POSIX C，且不含预检阶段判定的 critical 构造（例如工具链预检规则指定的 `fork`、`dlopen` 或 socket）。违反者超出范围；工具链将报告之，而非尝试尽力翻译。

**A10 — 运行约束。** 单一 PREEMPT_RT 域内，同一时刻至多加载一个融合控制模块，此由符号与宿主模型决定。多个融合模块的并发留待未来工作（§12）。

### 4.4 公平对比原则

为使对比在存在上述差异的前提下仍有意义，我们采用四条规则。其一，所有同平台测量共享**相同内核版本**（以 `uname -r` 记录）。其二，共享**相同 CPU 隔离 profile** 以及相同的线程亲和与调度策略。其三，共享**相同测量时长**，正式数据的 soak 协议要求至少十五分钟 `【实验时填具体时长】`。其四，每个数字的来源标注为**引用**或**复现**，使读者绝不至于把在别处获得的已发表数值与在此处获得的测量混为一谈。这些规则并不消除根本性不对称——cRTOS 位于带 hypervisor 的 x86-64，而 K-Fusion 位于无 hypervisor 的 aarch64——它们确保该不对称**可见**而非被隐藏。

---

## 合规自检

- **无编造数据**：§4 未出现任何本文自测数值；时长/重复次数为**协议参数**（可由用户调整），非结果数据，且标注 `【实验时填具体时长】`。
- **无编造文献**：仅引用已核实的 [1][2][3][5][9]；其余以 `【待补文献】`/`【待核实】` 处理。
- **无编造指标**：抖动定义（$|a_i-r_i|$）、`abs_max`、p99、Level 0/1/2 均为**定义性**内容，非杜撰的实测指标。
- **假设完整展开**：A1–A10 由 `DRAFT_B` 要点式扩展为正文，中文一一对应。
- **逻辑递进**：§4.1 定义 → §4.2 轴 → §4.3 假设 → §4.4 公平规则，四步承接。

---

*文档：`docs/paper/DRAFT_D__问题陈述与设计空间.md` · 前置：`DRAFT_A/B/C`*
