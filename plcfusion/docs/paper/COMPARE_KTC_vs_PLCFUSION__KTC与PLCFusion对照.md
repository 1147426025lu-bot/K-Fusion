# KTC（Timed C）与 PLCFusion 编译器对照

> **用途**：论文 §2.4、§3.4–§3.5、§4.2 与 Figure 7 的正文素材。  
> **插图**：[`figures/fig7_compiler_ktc_plcfusion__编译器对照.svg`](figures/fig7_compiler_ktc_plcfusion__编译器对照.svg)

---

## 1. 定位一句话（Abstract / Introduction 可用）

**Timed C / KTC** 通过**语言级时间原语**（`sdelay`、`task`、`spolicy`）与 CIL 源码变换，在用户态 POSIX 运行时上实现可分析的软实时周期任务；**PLCFusion** 通过 **LLVM IR Pass** 将现有 POSIX C **分裂内核化**为 freestanding `kernel.o` 并链入 PREEMPT_RT 内核模块，在不改语言的前提下把热路径移入内核对 hrtimer/kthread 执行。二者在 Pi 实验中以 **相同 cyclictest 协议**（1 ms、CPU3、`chrt -f 99`）对照 jitter，但解决的是**不同设计轴**：显式时间语义 vs 编译期交付形态变换。

---

## 2. §3.4 语言级实时扩展（扩写稿）

### 2.1 Timed C 与 KTC

Timed C（Zhao et al., RTAS 2018）将时间行为提升为**一等语言构造**：程序员在源码中标注 timing point（`sdelay` / `fdelay`）、任务（`task`）与调度策略（`spolicy`），由 **KTC**（Timed C Compiler）做源码到源码变换。RTSS 2019 进一步给出端到端 toolchain：编译 → profile 插桩 → 调度可行性 / 敏感性分析。KTC 基于 **goblint-cil 1.7.3**，在 CIL AST 上运行 `sdelay.ml` 等扩展，生成 `*.cil.c` 并链接 `libktc.a`；在 POSIX 目标上，`sdelay` 展开为 `ktc_sdelay_init`，底层使用 `clock_nanosleep(TIMER_ABSTIME)` 维持软时间点间隔。

**优点**：时间约束**可读、可静态分析**；同一 Timed C 程序可面向 POSIX 或 FreeRTOS（`--enable-ext1`）；不依赖内核模块加载。  
**局限**：应用必须用 Timed C 方言重写；周期精度受用户态 sleep 与 `CLOCK_REALTIME` 路径影响；工具链对现代 glibc/aarch64 需额外移植（本仓库 `scripts/timedc/`）。

### 2.2 PLCFusion

PLCFusion 不引入新语法，而是对**已有 C**（如 rt-tests `cyclictest.c`）做 Clang → LLVM IR → **PLCFusionPass**（POSIX→`plc_*` 重映射、DCE、默认定点、热路径 `OptimizeNone`）→ `kernel.o`，再与宿主 `.ko`（hrtimer/pthread/kthread 实现）链接。时间行为由**宿主定时器**与 PREEMPT_RT 调度保证，编译器侧通过 WCET-oriented Pass 组合降低热路径代码膨胀，而非在源码中声明 deadline。

**优点**：保留原始算法 IR；可内核化复杂多线程 POSIX 程序；与 Pi PREEMPT_RT 测量流水线深度集成。  
**局限**：无语言级 task model；依赖 `insmod`/内核模块生命周期；未映射 libc 需 stub；WCET 来自 Pass 约束 + 测量，非形式化证明。

### 2.3 关系：竞争、互补、可并存

- **竞争维度**：在 Pi 上均可作为「除手写内核模块以外的」自动化实时交付路径，本文以 cyclictest 抖动对照。  
- **互补维度**：Timed C 擅长**规格层**（何时、何任务、何策略）；PLCFusion 擅长**实现层**（如何把现有 C 放进内核执行）。  
- **可并存**：Timed C 编写周期外壳 + PLCFusion 内核化计算密集型 `plc_cycle` 体——本文实验采用**分立三基线**以保证归因清晰。

---

## 3. §3.5 对比表（Related Work Summary — 扩展版）

### Table I — 系统与隔离（与 cRTOS 表合并投稿）

| 工作 | 核心机制 | 隔离 / 执行域 | Linux 内核修改 | 典型平台 |
|------|----------|---------------|----------------|----------|
| cRTOS (VEE'20) | Hypervisor 分域 + NuttX sRTOS | 空间双 realm | 无（分域） | x86 + Jailhouse |
| PREEMPT_RT | 内核抢占与 IRQ 线程化 | 单域，调度隔离 | 有（RT 补丁） | 多平台 |
| **Timed C / KTC** | 语言 + CIL 变换 + `libktc.a` | **用户态**单域 | 无 | POSIX / FreeRTOS |
| **PLCFusion（本文）** | LLVM 分裂内核化 | **内核模块**单域 | 无（可加载模块） | **Pi / aarch64** |

### Table II — 编译器与 toolchain（Figure 7）

| 维度 | KTC (Timed C) | PLCFusion |
|------|---------------|-----------|
| **编译器类型** | 领域语言 + CIL 源码变换（OCaml） | LLVM IR Pass 工具链（C++17） |
| **前端** | gcc `-E` + `cilktc.h` → CIL parse | Clang `-emit-llvm` |
| **核心 IR** | CIL AST | LLVM bitcode (`.ll`) |
| **核心变换** | 插入 timing labels、`ktc_sdelay_init`、pthread `task` | `malloc`→`plc_kmalloc`、`pthread_*`→`plc_*`、DCE、Q 定点、`optnone` 热路径 |
| **后端** | 系统 gcc | `llc` → aarch64 `kernel.o` |
| **链接产物** | `a.out` + `libktc.a` | `*_mod.ko` + freestanding `kernel.o` |
| **运行入口** | `main` / `task` 线程 | `insmod` → kthread → `main` / `timerthread` |
| **时间语义来源** | `sdelay` / `fdelay` / `spolicy` | hrtimer 宿主 + manifest 策略 + Pass 标记 |
| **调度策略** | 编译器生成 `sched_setattr`（EDF/FIFO/RM） | 外部 `chrt` + FIFO kthread |
| **WCET / 可分析性** | Timing-point 静态分析 + profile | `wcet-benchmark` Pass + 可选 autotune + 测量 |
| **输入程序** | Timed C 源码 | 标准 C / rt-tests / plc-cc |
| **浮点** | 原生 `double` | 默认 Q16.16 定点 |
| **Pi 5 移植** | 需重编 KTC + `libktc.a` + header shim | 原生 aarch64 主线 |

### Table III — 本机实验协议（Pi, PREEMPT_RT）

| 基线 | 脚本 / 产物 | 周期机制 | 全周期样本 | 图用数据 |
|------|-------------|----------|------------|----------|
| Userspace | `run_paper_userspace` | cyclictest `-i 1000` | ~28.8M（8h 直方图） | 直方图（无逐点时序） |
| **Timed C** | `run_paper_timedc` → `cyclictest_paper.out` | `sdelay(1,ms)` + `clock_nanosleep` | ~28.8M cycles | decim jitter.bin（stride 50） |
| **PLCFusion** | `run_paper_compare` fused cell | kthread + `hrtimer` | ~28.8M cycles | decim jitter.bin v2 |

**公平性声明（§4.4）**：三基线共用 `paper_env_setup`（L2 soak / L1 stress）、`taskset -c 3`、`chrt -f 99`、相同 `DURATION_MIN`。Userspace 与 Timed C 在**用户态**执行，PLCFusion 在**内核态**执行——抖动差异部分来自执行域，§10 讨论中须明确，不可仅归因于「编译器优化幅度」。

---

## 4. §4.2 设计轴补充（KTC vs PLCFusion）

在原有 cRTOS vs PLCFusion 五轴之外，增加：

6. **Explicit timing language**（KTC）vs **Post-hoc IR kernelization**（PLCFusion）。  
7. **Source-to-source**（保留用户态 ELF）vs **Split artifact**（`kernel.o` + `.ko`）。  
8. **Soft timing point**（`sdelay` 最小间隔）vs **Kernel timer interrupt**（hrtimer ABS_PINNED）。  
9. **Portable across OS targets**（POSIX/FreeRTOS）vs **Deep PREEMPT_RT integration**（单平台极致优化）。

---

## 5. 测量结果占位（实验后填入 §8）

### 15 min soak（`compare_20260702_221351`）

| 基线 | abs_max (ns) | 备注 |
|------|--------------|------|
| Userspace | ~16 µs (hist max) | cyclictest 直方图 |
| Timed C | 9,281 | `TimedCSummary` |
| PLCFusion | 3,391 | `FusedSummary` / PASS |

### 8 h soak（进行中 / 部分完成）

| 基线 | abs_max (ns) | 全周期 | decim 导出 |
|------|--------------|--------|------------|
| Userspace | ~21 µs | 28.8M ✅ | 直方图（解析需支持 >6 位 count） |
| Timed C | 28,371 | 28.8M ✅ | 72k（15min 上限，待重跑 576k） |
| PLCFusion | TBD | 进行中 | `EXPORT_DECIM_MAX=576000` |

**解读要点**：8h 上 Timed C abs_max 高于 15min，符合长测 tail 恶化预期；PLCFusion 是否保持 <5 µs 待 fused 8h 完成。对比时应并列 **测量时长、样本量、decim 策略**。

---

## 6. 建议正文段落（英文，可直接改写入 LaTeX）

### §3.4.1 Timed C and KTC

Timed C elevates temporal behavior to language primitives and compiles them with the KTC source-to-source compiler built on CIL. At each timing point, the compiler inserts runtime calls that enforce minimum inter-point delays via POSIX clocks and, optionally, `sched_setattr` for EDF or fixed-priority policies. This design makes timing constraints explicit and amenable to static timing-point analysis, but requires programs to be authored in the Timed C dialect and executed in user space.

### §3.4.2 PLCFusion

PLCFusion takes the opposite starting point: unmodified (or lightly configured) POSIX C is lowered to LLVM IR, remapped to a kernel-oriented ABI (`plc_*`), dead-code eliminated from declared entry roots, and linked as a freestanding `kernel.o` inside a PREEMPT_RT loadable module. Temporal behavior is not expressed in the source language; instead, a replaceable host component arms pinned `hrtimer` callbacks and invokes fused functions in kernel context. The compiler contributes predictability by constraining hot-path optimization (e.g., `optnone` on cycle functions) and optional WCET-oriented pass tuning.

### §3.4.3 Comparison and coexistence

KTC and PLCFusion are not direct substitutes. KTC is a **language + compiler** solution for specifying and analyzing timed tasks; PLCFusion is a **delivery toolchain** for executing legacy C control loops inside the Linux kernel without hypervisor partitioning. On our Raspberry Pi testbed, we evaluate both alongside user-space `cyclictest` under identical isolation profiles. We deliberately keep the baselines separate so that jitter improvements are not conflated with the shift from user space to kernel execution.

---

## 7. Figure 7 图题

- **EN**: *Compiler toolchain comparison: Timed C/KTC (CIL source transformation, user-space runtime) versus PLCFusion (LLVM split kernelization, kernel module). Both evaluated on Raspberry Pi under a shared cyclictest-style measurement protocol.*
- **ZH**: *编译器工具链对照：Timed C/KTC（CIL 源码变换与用户态运行时）与 PLCFusion（LLVM 分裂内核化与内核模块）。二者在树莓派上采用统一的 cyclictest 式测量协议评估。*

---

## 8. 与现有插图关系

| 图 | 内容 |
|----|------|
| Fig.1 | cRTOS **系统架构** vs PLCFusion **系统架构** |
| Fig.2 | PLCFusion **端到端 toolchain + Pi 基线**（仿 Timed C 方法论） |
| **Fig.7（新）** | KTC **编译器流水线** vs PLCFusion **编译器流水线** |
| `fig_compare_*` | 三基线 **测量结果** 叠加 |
