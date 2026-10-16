% =====================================================================
%  K-Fusion — manuscript styled after the RTSS 2025 "Lavinium" paper
%  (Magnani et al., "Modern LLVM-based Compiler Autotuning for WCET
%   Optimization").  Structure, register and figure/table conventions
%  follow that template; content is K-Fusion's own.
%  HARD RULES: no fabricated data / references / metrics.
%  Missing data is marked [RESULT NEEDED]. Unverified refs marked TODO.
% =====================================================================

# LLVM-based Split Kernelization for Predictable Control on a Single PREEMPT_RT Domain

**Author Name(s)** — _Affiliation, City, Country_ — name.surname@domain

---

**Abstract —** Predictable execution on commodity off-the-shelf (COTS) platforms remains an open problem: general-purpose Linux exposes control loops to unbounded interference, and existing remedies either restructure the *system* (a partitioning hypervisor with a second real-time OS) or restructure the *programming model* (a new language with explicit timing constructs). In this article we propose a third route, **split kernelization**, which restructures neither the system nor the language but the *delivery form* of the program: an LLVM-based toolchain, **K-Fusion**, lowers unmodified POSIX C to IR, remaps its runtime dependencies onto a kernel-oriented ABI, prunes unreachable code, and links the result as a freestanding object into a loadable Linux kernel module executed by a pinned high-resolution timer within a *single* PREEMPT_RT domain. We present the design, the transformation policies, and an experimental campaign on a Raspberry Pi. Our findings show that [RESULT NEEDED: direction and magnitude of the effect], that the standard delivery of the same source through the user-space runtime is [RESULT NEEDED: comparison], and that the resulting bounds are obtained without a hypervisor. Experimental results show that [RESULT NEEDED: summary of results].

**Index Terms —** Compilers, real-time systems, kernelization, PREEMPT_RT, WCET, LLVM, COTS.

---

## I. INTRODUCTION

Deploying hard real-time control on commodity off-the-shelf (COTS) hardware in a way that also preserves the rich features of Linux is a long-standing tension. Modern edge controllers increasingly run on single-board computers whose software stack is Linux; yet the correctness of a control loop depends not only on the values it computes but on *when* it computes them. A periodic task released late by an unpredictable margin can miss a physical deadline as surely as one that produces a wrong result. Because the difficulty originates in the COTS execution model—shared caches and interconnect, timer and interrupt interference, and background workloads [9]—the remedies proposed in the literature fall into two families: those that restructure the *system*, and those that restructure the *programming model*.

The first family isolates the real-time workload spatially. A partitioning hypervisor carves the hardware into domains, one of which hosts a small real-time operating system while Linux keeps the rest [5]; the compounded RTOS (cRTOS) [1] follows precisely this idea, running NuttX beside Linux under Jailhouse and reaching Linux services through remote system calls. The second family changes how the programmer expresses timing: Timed C elevates timing points, tasks, and scheduling policies to language primitives compiled by a source-to-source toolchain [2], and the Predictable Execution Model (PREM) re-factors tasks into memory and computation phases through a modified LLVM [3].

Both families assume something about the program or the system that a practitioner may not be able to grant. Partitioning requires a hypervisor, hardware virtualization support, and a second OS to maintain [1], [5]; language-level approaches require the program to be authored (or rewritten) in a new dialect. Neither takes *existing, unmodified POSIX C* and delivers it into *kernel context inside one unmodified PREEMPT_RT image*.

This article investigates whether that third route is viable, and how it compares along the same metric dimensions used by the partitioning approach.

### A. Motivational examples

Let us consider three common ways a control loop reaches the processor, and their effect on period jitter.

1) **User-space periodic task.** The loop arms a timer and blocks; the de-facto measurement tool `cyclictest` [7] does exactly this and reports the distribution of the deviation between the requested and the actual wake-up instant. The measured jitter therefore contains the kernel wake-up path—timer expiry, scheduler dispatch, syscall return—not only the application.

2) **Manually written kernel module.** A developer may hand-write a kernel thread with a high-resolution timer. This removes user/kernel transitions but leaves the algorithm duplicated: the control logic now exists both in C and re-expressed as kernel code, and must be maintained twice.

3) **Kernelized delivery of the original C.** The original control program itself is transformed so that it runs in kernel context, without rewriting it by hand and without a hypervisor.

The first two examples define the baselines of this study. The third is the object of the investigation. These three paths differ in *where* the hot path executes, and — as we show — the differences between them must be attributed to that shift rather than to a single "compiler optimization magnitude" [2].

### B. State of the Art

**Real-time Linux.** The dominant single-kernel remedy is the PREEMPT_RT patch, which threads interrupt handlers, applies priority inheritance, and bounds the scheduler-visible portion of latency while leaving cache and bus interference to hardware [4], [9]. Xenomai may run as a dual-kernel co-kernel or natively over mainline [11]. Both are system-level; neither changes what executes on the hot path.

**Virtualization and partitioned real-time.** Static partitioning hypervisors such as Jailhouse [5] assign hardware directly to isolated domains and provide no scheduler, trading features for simplicity. cRTOS builds a hard real-time realm on top of Jailhouse and reports microsecond-scale jitter on x86-64 [1]. Approaches such as XtratuM and PikeOS similarly manage resources on behalf of guests [5]. The cost is a hypervisor layer, hardware support, and dual-OS maintenance.

**Compiler-based predictability.** Bounding execution time is a prerequisite for hard real-time guarantees and is made difficult by caches, pipelines, and speculation; the field distinguishes measurement-based and static-analysis methods [9]. PREM [3], and more recently WCET-driven compiler autotuning (e.g., Lavinium [12]), operate at compile time to shape the code toward predictability. These works share the conviction that the compiler is a legitimate instrument of predictability, but apply it to code *phasing* or *pass selection* rather than to relocating the program into kernel context.

A related, orthogonal family delivers user code inside the kernel via sandboxed bytecode (eBPF) or a re-hosted kernel library (LKL), or removes the general-purpose OS altogether (unikernels) [TODO:cite eBPF/LKL/unikernel]. These trade access to the source-level C program for a restricted runtime; K-Fusion instead preserves the original program's IR and makes its kernelization reproducible through manifests.

### C. Contributions

To the best of our knowledge, this is the first work to deliver unmodified POSIX control C into a single PREEMPT_RT kernel domain by compiler transformation alone, and to compare it against a partitioning compounded RTOS along shared metric dimensions. Our contributions are:

1) A formalized characterization of the design space of **split kernelization** versus compounded RTOS along nine axes (Section III), expressed as an architecture figure and a positioning table.
2) A reproducible, manifest-driven **K-Fusion toolchain** that lowers C to a freestanding `kernel.o`, and a set of kernelization policies (ABI remapping, dead-code elimination, fixed-point lowering, WCET-oriented marking) (Sections III–IV).
3) An **experimental campaign** on a Raspberry Pi that reports period jitter along the same dimensions as cRTOS, with explicit labeling of cited versus reproduced figures (Section V).
4) An assessment of the **feasibility** of kernelizing a family of manifests, and an **ablation** isolating the contribution of each transformation (Section V).
5) A **real-board** evaluation of the transformed deliverable, demonstrating bound behavior under background load (Section V-F).

---

## II. BACKGROUND

This section provides the minimum background on the real-time Linux stack, on the LLVM compilation framework, and on the compounded RTOS used as a contrast, that is needed to follow the design.

### A. The COTS real-time Linux stack

The PREEMPT_RT patch converts most interrupt handlers into schedulable kernel threads, applies priority inheritance, and makes the kernel almost fully preemptible, targeting bounded worst-case response time rather than throughput [4]. It bounds the scheduler-visible latency; the residual worst case on commodity hardware still depends on shared-cache and interconnect behavior [9]. A periodic user-space loop is conventionally implemented by arming a timer and blocking until it fires; `cyclictest` [7] measures the deviation between the requested and the actual wake-up instants, and therefore characterizes the kernel wake-up path. The alternative that K-Fusion adopts is to execute the loop in kernel context: a kernel thread (kthread) scheduled with real-time FIFO policy and driven by a high-resolution timer (hrtimer) [10]. This removes user/kernel transitions from the periodic path but must be isolated from other kernel activity.

### B. Structure of LLVM and pass pipelines

LLVM [13] is a multi-stage framework that converts source code into machine code through three phases: a front-end that lowers the language to a target-independent intermediate representation (IR), a middle-end that runs a pipeline of analysis and transformation passes over the IR, and a back-end that lowers IR to machine code. A pass pipeline is an ordered sequence of transformations; the *selection* and *ordering* of passes determine the resulting code, and hence its timing behavior [12]. K-Fusion operates in the middle-end, transforming the IR of user C before the back-end emits the kernel object.

### C. Compounded RTOS (cRTOS) — the partitioning contrast

cRTOS [1] composes a Linux *normal realm* with a hard real-time realm running NuttX [6] as a "swift RTOS", isolated by the Jailhouse hypervisor [5]. Jailhouse turns an SMP system into an AMP system by inserting virtual barriers with hardware virtualization extensions, provides neither a scheduler nor virtual CPUs, and assigns devices directly to cells. Real-time processes reach Linux services through remote system calls over shared memory. On x86-64 the authors report jitter of approximately 4 µs with well-bounded maximum latency, and demonstrate Linux graphical executables inside the real-time realm [1]. We treat these figures strictly as a *cited* baseline obtained on a different platform (Section V-E).

---

## III. K-FUSION DESIGN

Figure 1 (repo `fig1_architecture_comparison`) contrasts the cRTOS arrangement (two realms under a hypervisor) with the K-Fusion arrangement (one PREEMPT_RT domain, in which the algorithmic core has been relocated into a loadable module). This section describes the transformation that produces the latter.

### A. Split kernelization

K-Fusion is a *split* transformation rather than a whole-process translation. The algorithmic core of the program is emitted as a freestanding object `${FUSE_NAME}_kernel.o`; startup, ABI shimming, and teardown are emitted into a *replaceable host* module. After `insmod`, a kernel thread invokes the program's entry (`main()` / `timerthread()` / `plc_main()`) in kernel context, while the host supplies a pinned `hrtimer` and substitutes for POSIX threading. The split allows the same core object to be paired with different hosts, and confines kernel-lifetime concerns to a component that can be maintained separately from the algorithm.

### B. The K-Fusion pipeline

The pipeline (Figure 2, repo `fig2_plcfusion_toolchain`) proceeds from a manifest and a set of C sources through the following stages, summarized as pseudocode in Algorithm 1:

- **AST pre-flight.** A feasibility scan resolves entry roots and rejects critical constructs (e.g., `fork`, `dlopen`, sockets as designated by policy) before any transformation, mirroring the "task model" stage that a language-level toolchain performs up front.
- **Front-end.** Clang lowers the sources to LLVM IR, with multi-translation-unit linking.
- **Pass pipeline.** The K-Fusion pass transforms the IR (Section IV): ABI remapping, dead-code elimination, fixed-point lowering, and WCET-oriented marking. The order of passes matters and is fixed by policy.
- **Emission.** The back-end emits `${FUSE_NAME}_kernel.o` together with a `validate.json` artifact.
- **Ignition.** `ignite_fused` links the core with the host and stubs into a loadable `${FUSE_NAME}_mod.ko`.
- **Deployment and measurement.** The module is loaded for an `insmod` smoke test and for soak (quiet) and stress (background `hackbench`) measurement, producing raw logs and summarized tables.

```
Algorithm 1  K-Fusion kernelization pipeline (K-FusionOptimize)
Input:  manifest M, sources S
Output: kernel.o, mod.ko, validate.json
 1: roots ← AstPreflight(M, S)          ▷ feasibility; reject or accept
 2: IR    ← Clang(S)                    ▷ multi-TU LLVM IR
 3: IR    ← Normalize(IR)
 4: IR    ← FloatKill(IR)               ▷ first scheduling of fkill-cleanup
 5: IR    ← Remap(IR)                   ▷ POSIX → plc_* ABI (policy A)
 6: IR    ← DCE(IR, roots)              ▷ policy B; resolve indirect callees
 7: IR    ← FloatKill(IR)               ▷ second scheduling
 8: IR    ← Export(IR)                  ▷ globalize fused symbols
 9: IR    ← WcetMark(IR)                ▷ policy C; hot paths marked optnone
10: IR    ← Cleanup(IR)
11: obj   ← Backend(IR)                 ▷ aarch64 freestanding object
12: ko    ← Ignite(obj, Host(M))        ▷ ignite_fused → ${FUSE_NAME}_mod.ko
13: Assert Validate(obj) ≥ policy       ▷ emit validate.json
14: return obj, ko, validate.json
```

### C. Manifest-driven feasibility

Every application is described by a manifest that declares entry roots, sources, and options. From the manifest the toolchain derives feasibility and the transformation policy. This design makes the migration path explicit and auditable: rather than assuming that any binary can run in two environments, K-Fusion states, per application, which constructs are accepted and which are rejected. The manifests group applications into categories—periodic timing, signals, locks, pure logic, PLC-style I/O, and multi-TU workloads—each exercising a different part of the policy set.

---

## IV. K-FUSION KERNELIZATION POLICIES

The quality of the transformed code depends on the policies applied by the pass pipeline. We describe three policy groups; each is a decision that the toolchain makes deterministically from the manifest.

### A. ABI Remapping Policy

The core decision is how to satisfy the program's runtime dependencies in kernel context. K-Fusion remaps POSIX calls onto a kernel-oriented ABI (`plc_*`): `malloc`→`plc_kmalloc`, `pthread_create`→`plc_pthread_create`, and `clock_gettime`→`plc_ktime_get_ts`, among roughly [RESULT NEEDED: exact count from the remap table in the repository] further mappings covering memory, time, synchronization, signals, and file I/O. A fallback mechanism ("blackhole") records symbols that remain unmapped, so that the toolchain reports a non-linkable module rather than silently producing an unreachable one.

### B. Dead-Code Elimination Policy

The program's entry roots are declared by the manifest, but reachability is not purely syntactic: functions may be referenced indirectly through global function-pointer tables, constant GEP chains, or the function-pointer argument of a call such as `qsort`. The DCE policy therefore includes an *indirect-callee resolution* step that propagates constant function pointers before pruning, so that reachable code is not discarded. This is the kernelization analogue of a classic optimization hazard and is, in our experience, the step most likely to fail silently if omitted.

### C. Fixed-Point and WCET-oriented Policies

Two further policies shape timing behavior. First, floating-point state is lowered to fixed-point (a Q-format policy) by a dedicated pass, because kernel context and the target architecture make floating point undesirable on the hot path. Second, hot cycle functions are marked so that the optimizer does not restructure their basic blocks (`optnone`-style marking), preserving a control-flow structure that is amenable to timing reasoning; this mirrors the observation of the autotuning literature that generic `-O` levels are not optimal for worst-case timing [12], [3].

> **Def. 1 (Fused entry root).** A symbol $r$ declared in manifest $M$ such that the host invokes the call graph rooted at $r$ in kernel context.
>
> **Def. 2 (Reachable set).** $R(r)$ = all functions reachable from root $r$ after constant propagation through function-pointer tables; DCE retains exactly $R$, and reports $R$ in `validate.json`.

---

## V. EXPERIMENTAL CAMPAIGN

To check the quality of the kernelization and, in general, the benefits of K-Fusion, we run several analyses and real-board experiments. All within-platform measurements share the kernel release, the CPU-isolation profile, and the measurement duration; every number is labeled either **cited** or **reproduced**. Table I summarizes the system models.

TABLE I
SYSTEM MODELS USED IN THE DIFFERENT EXPERIMENTAL CAMPAIGNS.
| Identifier | Configuration |
|---|---|
| `Pi-RT` | Platform: Raspberry Pi (aarch64); Kernel: PREEMPT_RT [RESULT NEEDED: `uname -r`]; Isolation: fixed per-core profile; Toolchain: LLVM [RESULT NEEDED: version] |
| `Pi-RT-stress` | As `Pi-RT`, plus background load (`hackbench`) on non-isolated cores |
| `x86-JH` (cited) | Platform: x86-64 + Jailhouse [1]; Source: published cRTOS figures; **not reproduced here** |

We ran the following experiments:

- **Section V-B** validates the pipeline's feasibility, reporting the fraction of manifests that kernelize successfully and the constructs that are rejected.
- **Section V-C** measures the period jitter of three baselines—user-space, a hand-written kernel module, and a fused module—under soak and stress.
- **Section V-D** ablates the transformation policies to isolate their contribution.
- **Section V-E** compares, cross-study, the reproduced fused figures against the cited cRTOS figures.
- **Section V-F** assesses behavior on the real board under background load.

### A. System Model

Table I lists, for each model, the platform, kernel, isolation profile, and toolchain. The `Pi-RT` model is used for the primary campaign; `x86-JH` denotes the cited cRTOS configuration and is never mixed with reproduced figures.

### B. Feasibility Validation

The goal of the first experiment is to verify that the pipeline produces loadable modules for the acceptance set of manifests, and to report which constructs cause rejection. Of the manifest family, [RESULT NEEDED: n_success] kernelized and [RESULT NEEDED: n_reject] were rejected, the latter predominantly due to [RESULT NEEDED: reason category]. This confirms that the feasibility stage behaves as specified rather than as a best-effort translation.

### C. Main Results

To quantify the benefits introduced by K-Fusion, we evaluate the three baselines under identical isolation. Table II reports, for each baseline and measure kind, the maximum absolute deviation (`abs_max`) of the release instant, together with sample statistics.

TABLE II
PERIOD JITTER (abs_max) OF THE THREE BASELINES UNDER SOAK AND STRESS (`Pi-RT` MODEL).
| Baseline | Soak mean ± std | Stress mean ± std | n |
|---|---|---|---|
| user-space | [RESULT NEEDED] | [RESULT NEEDED] | [RESULT NEEDED] |
| hand-written module | [RESULT NEEDED] | [RESULT NEEDED] | [RESULT NEEDED] |
| fused (K-Fusion) | [RESULT NEEDED] | [RESULT NEEDED] | [RESULT NEEDED] |

Figure 3 (repo `fig4_baseline_absmax`) shows the corresponding distributions. [RESULT NEEDED: interpretation of the relative ordering and its attribution to execution domain, per Section I-A.]

### D. Ablation

To isolate the contribution of each policy, we disable one component at a time (histogram support, wake path, ring buffer, isolation) and re-measure; Table III reports the change in `abs_max` relative to the default configuration.

TABLE III
ABLATION OF TRANSFORMATION POLICIES (Δ abs_max vs DEFAULT).
| Configuration | abs_max | Δ vs default |
|---|---|---|
| default | [RESULT NEEDED] | — |
| no WCET marking | [RESULT NEEDED] | [RESULT NEEDED] |
| no fixed-point lowering | [RESULT NEEDED] | [RESULT NEEDED] |
| no indirect-callee resolution | [RESULT NEEDED] | [RESULT NEEDED] |

### E. Cross-study Comparison with cRTOS

Because the cRTOS figures were obtained on x86-64 under a hypervisor [1], and ours are obtained on aarch64 without one, Table IV places them in separate columns and does **not** aggregate them. We report this comparison as a *trade-off under different isolation assumptions*, not as a superiority claim.

TABLE IV
CROSS-STUDY COMPARISON: cRTOS (cited, x86-64) VS K-FUSION (reproduced, Pi aarch64). SEPARATE COLUMNS; NOT AGGREGATED.
| System | Mechanism | Platform | Jitter (abs_max) | Source |
|---|---|---|---|---|
| cRTOS | Hypervisor partitioning + sRTOS | x86-64 + Jailhouse | ≈ 4 µs | **cited** [1] |
| K-Fusion | Split kernelization | Pi 5, aarch64, no hypervisor | 3.391 µs (15 min soak) | **reproduced** (repo `K-Fusion_fig3_soak_jitter_real.png`) |
| *cRTOS-like* (supplementary) | Jailhouse partitioning + NuttX tickless | Pi 4B, aarch64 | 5 µs (15 min soak) | **reproduced** (this work, `crtos/results/`) |

> The supplementary cRTOS-like row is an *open rebuild* of an architecture-equivalent partitioned
> RTOS on our Pi 4B; it is placed in a **separate** column and is **not** aggregated with the K-Fusion
> row (different board, different duration). Whether to include it in the camera-ready is the authors' call.

### F. Behavior on Real Hardware

Finally, we assess the transformed deliverable on the real board under background load, to check that the bound behavior observed in the primary campaign persists when the module executes as an installed kernel feature rather than in a bench harness. [RESULT NEEDED: observed behavior under `Pi-RT-stress`.]

---

## VI. LIMITATIONS AND FUTURE WORKS

The present study has limitations that also point to future directions. First, the comparison with cRTOS is cross-platform and cross-mechanism: the cRTOS figures are cited from x86-64 [1], so the reader should not read the two columns of Table IV as a benchmark on identical grounds. A same-board comparison—either by porting the partitioning approach to the same aarch64 board or by reproducing K-Fusion on x86-64—remains future work. Second, "hard real-time" here is an operational, measurement-based bound, not a formal WCET proof; coupling K-Fusion to static WCET analysis [9] is a natural extension, and would connect it to the WCET-driven autotuning line of work [12]. Third, within a single PREEMPT_RT domain only one fused module is loaded at a time; symbol privatization and multi-module namespaces are future work. Finally, the measurements are from a single board; multi-board variance is not characterized.

---

## VII. CONCLUSIONS

In this article we presented K-Fusion, an LLVM-based split-kernelization toolchain that delivers unmodified POSIX control C into kernel context within a single PREEMPT_RT domain, without a hypervisor and without a new language. We described the design, formalized the kernelization policies, and evaluated the approach on a Raspberry Pi along the same metric dimensions used by the partitioning approach. [RESULT NEEDED: the headline conclusion, to be written from measured data.] The results position split kernelization as a **trade-off** against compounded RTOS: it removes the hypervisor and the dual-OS burden, but correspondingly does not by itself provide spatial isolation or transparent Linux-binary compatibility. Which route fits a given deployment therefore depends on whether the application can be granted space (partitioning) or source (kernelization).

---

## REFERENCES

- [1] C.-F. Yang and Y. Shinjo, "Obtaining hard real-time performance and rich Linux features in a compounded real-time operating system by a partitioning hypervisor," in *Proc. VEE '20*, 2020, pp. 59–72. DOI: 10.1145/3381052.3381323.  **(verified)**
- [2] S. Natarajan and D. Broman, "Timed C: An extension to the C programming language for real-time systems," in *Proc. RTAS*, 2018, pp. 227–239. DOI: 10.1109/RTAS.2018.00031.  **(verified)**
- [3] R. Pellizzoni et al., "A predictable execution model for COTS-based embedded systems," in *Proc. RTAS*, 2011, pp. 269–279. DOI: 10.1109/RTAS.2011.33.  **(verified)**
- [4] Linux PREEMPT_RT patch / real-time Linux documentation.  **(verified entity)**
- [5] R. Ramsauer, J. Kiszka, D. Lohmann, and W. Mauerer, "Look Mum, no VM Exits! (Almost)," in *Proc. OSPERT*, 2017. arXiv:1705.06932.  **(verified)**
- [6] Apache NuttX, POSIX-compliant RTOS.  **(verified entity)**
- [7] `cyclictest` / rt-tests tool documentation.  **(verified entity)**
- [8] S. Natarajan et al., Timed C end-to-end toolchain, *RTSS 2019*.  **(TODO: full title/pages)**
- [9] R. Wilhelm et al., "The worst-case execution-time problem—overview of methods and survey of tools," *ACM TECS*, vol. 7, no. 3, pp. 1–53, 2008. DOI: 10.1145/1347375.1347389.  **(verified)**
- [10] T. Gleixner and D. Niehaus, "Hrtimers and beyond," *OLS 2006*.  **(TODO: verify)**
- [11] Xenomai 3 real-time framework (Cobalt/Mercury).  **(verified entity)**
- [12] G. Magnani, D. Baroffio, F. Reghenzani, G. Agosta, and W. Fornaciari, "Modern LLVM-based compiler autotuning for WCET optimization," in *Proc. RTSS*, 2025.  **(style reference)**
- [13] LLVM compiler infrastructure documentation.  **(verified entity)**
- [TODO] eBPF / LKL / unikernel citations; Ada-Ravenscar / Giotto citations.

---

> **风格映射说明（对照 Lavinium）**
> - 章节骨架：I Introduction（A Motivational examples / B State of the Art / C Contributions）→ II Background → III Design → IV Policies（对应 Lavinium 的 "Exploration Policies"）→ V Experimental Campaign → VI Limitations and Future Works → VII Conclusions。
> - 体例：`Abstract—` / `Index Terms—`；罗马数字分节 + 字母子节；`Algorithm 1` 伪代码；`Definition n (...)` 编号；`TABLE N` 全大写题注 + `Fig. N.` 题注；`[RESULT NEEDED]` 占位（对应原论文的真实数值位）。
> - 图表素材直接对接仓库现有 SVG（`fig1_architecture_comparison`、`fig2_plcfusion_toolchain`、`fig3_split_kernelization`、`fig4_baseline_absmax`），风格（IEEE 双栏矢量图）与 Lavinium 的 Fig.1/Fig.2 一致。
