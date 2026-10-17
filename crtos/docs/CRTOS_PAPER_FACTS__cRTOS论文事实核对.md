# cRTOS 论文事实核对（测量代码 / 通信 / 分工 / 测量独立性）

> 本文所有结论均来自原文或作者开源仓库，逐条标注出处。**不含推测**。
> 数据来源：
> - **VEE'20**：Yang & Shinjo, *Obtaining hard real-time performance and rich Linux features in a compounded
>   real-time operating system by a partitioning hypervisor*, ACM VEE '20, pp. 59–72,
>   DOI `10.1145/3381052.3381323`
> - **Access'25**：Yang & Shinjo, *Compounded Real-Time Operating Systems for Rich Real-Time Applications*,
>   IEEE Access vol. 13, pp. 26079–26104, DOI `10.1109/ACCESS.2025.3538561`（CCBY 开放获取，
>   全文 PDF：`tsukuba.repo.nii.ac.jp/record/2014668/files/IEEEA_13-26079.pdf`）
> - **Fixstars 开源实现**：`github.com/fixstars/cRTOS`
>
> 核对日期：2026-10-16。

---

## 0. ⚠ 先分清两篇论文（我们稿子此前混用了）

| | **VEE'20** | **IEEE Access 2025** |
|---|---|---|
| 平台 | **x86-64**（Xeon E5-2650 v4 / Supermicro C7X99-OCE-F / 16 GB） | **Raspberry Pi 4B** |
| hypervisor | Jailhouse | Jailhouse |
| GPOS | Linux 5.4 + PREEMPT_RT | **vanilla Linux 5.9** |
| tRTOS | NuttX（移植到 x86-64） | **FreeRTOS 10 与 NuttX 10**（各做 DSI / SSI 两种） |
| 抖动 | **约 4 µs** | **最小 5 µs** |
| 代码 | **Fixstars 开源** | Pi4B 部分**未公开** |

**待修**：我们稿子里有 5 处写成「cRTOS … x86-64」并挂引用 `[1]`。其中「≈4 µs / x86-64」是 **VEE'20** 的量；
**Access'25 是 Pi4B、最小 5 µs**。需确认 `[1]` 指向哪篇并分别标注。
（Access'25 全文**只有一处 "x86"**，且是 Xenomai 驱动路径的 URL——评估平台**只有 Pi4B**。）

**一个意外的好消息**：cRTOS(Access'25) 与我们重建的 cRTOS-like **同在 Pi 4B**，
可比性比我们原先假设的（"不同平台、只能引用"）**更好**。

---

## 1. 测量代码是什么？改过没有？

### 1.1 timing accuracy（抖动）＝ **标准 rt-tests `cyclictest`**，只做了一处小改

**Fixstars 仓库给出的原始命令（逐字）**：

```bash
sudo apt-get install rt-tests

# 在 Linux 本地跑
cyclictest -nq -p 90 -h 200 -l 1000 -i 1000

# 在 sRTOS(NuttX) 内跑 —— 同一个 cyclictest 二进制，经 loader 代理进去
chrt -f 90 ./loader cyclictest -nq -p 90 -h 200 -l 1000 -i 1000
```

> 即：**测 Linux 与测 sRTOS 用的是同一个 `cyclictest` 可执行文件**，区别只是后者由 `loader` 载入 RT 域执行。
> `-n` 用 clock_nanosleep、`-q` 静默、`-p 90` 优先级、`-h 200` 直方图、`-l 1000` 次数、`-i 1000` 间隔 1 ms。

**Access'25 论文的补充**（§X-A1）：

- 用的是 **rt-test 套件的 cyclictest**，**upstream v0.12**（Xenomai 3 用其自带变体）
- 参数：`clock_nanosleep` **100,000 次**，**1 ms** 间隔；测量线程给**更高优先级 + `SCHED_FIFO`**
- **确实改过**（仅一处、为减少干扰）：
  > "we modified the cyclictest program to **skip printing the load average in the /proc/loadavg file**"
- 做了**两组**实验：**verbose / 非 verbose**（详见 §4）
- 也额外用 **PREEMPT_RT 版 Linux 作 GPOS** 复测，结论"与 vanilla Linux 几乎一致"

### 1.2 interrupt latency（中断延迟）＝ **自研 benchmark + 外部仪器**

- 自己实现为「rich RT application」：一个线程监听 **GPIO 中断**，收到通知后在**另一个 GPIO 脚**输出脉冲
- 外部：**脉冲发生器**触发示波器 + **示波器**采集；脉冲长度 **500 µs**、采样率 **14.286 MHz**
- 由**后处理示波器数据**得到「触发时刻 → 应用收到中断」的差值
- verbose 版本额外用一个**非 RT 线程**输出脉冲计数；**在 cRTOS 里这一步走 remote system call**，
  RT 线程与非 RT 线程之间用 **lockless atomic variables** 传递
- 为在 Pi4B 上探测/注册 GPIO，他们**改了 Xenomai/DM 驱动**（仅用于 Xenomai 对照）

---

## 1.3 「我们能不能也用同一套？」—— **2026-10-16 实测：能**（此前判断有误）

### 结论
**可以在 NuttX inmate 里直接跑 cyclictest**，且其**输出格式与 rt-tests cyclictest 逐字一致**；
从而把"root 用 cyclictest / inmate 用自写 rtlat"的**两把尺子问题解决掉**。

### 实测证据（Pi4B，Jailhouse cell，CPU1，tickless）

```
nsh> cyclictest -n 0 -m 0 -q -p 200 -h 200 -l 5000 -i 1000
# Histogram
000004 000221
000005 004777
000006 000002
...
# Total: 000005000
# Min Latencies: 00004
# Avg Latencies: 00004
# Max Latencies: 00006
# Histogram Overflows: 00000
```

输出头（`# Total:` / `# Min Latencies:` / `# Avg Latencies:` / `# Max Latencies:` /
`# Histogram Overflows:`）与 rt-tests cyclictest **同名同格式** → 两侧日志可用**同一个解析器**。

### 1 小时长跑（2026-10-16，正式数据）

```
nsh> cyclictest -n 0 -m 0 -q -p 200 -h 200 -l 3600000 -i 1000
# Total: 003600000          <- 360 万次，实测耗时 3621 s
# Min Latencies: 00003
# Avg Latencies: 00004
# Max Latencies: 00009
# Histogram Overflows: 00000
```

全样本分布（1 µs 桶）：5 µs 占 **95.28 %**（3 429 913 次）、4 µs 占 4.71 %、
6 µs 491 次、7 µs 34 次、9 µs 47 次、3 µs 40 次；
**360 万次唤醒全部落在 3–9 µs，零次 >200 µs**。
→ 日志 `crtos/results/raw/pi4_inmate_cyclictest_1h.log`；
图 `crtos/results/figures/fig_pi4_inmate_cyclictest_1h_dist__1h抖动分布.png`；
口径与三条引用注意事项见 `../results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md` §7。

**顺带交叉验证**：cyclictest 报 min4/avg4/max6 µs，与自写 `rtlat` 的稳态
（p50 4.87 µs / max 5.26 µs）**在 1 µs 分辨率内一致** —— 两把独立尺子互证，说明这个量级是真实的。

### ⚠ 纠正此前一处**错误判断**
此前记的是「NuttX 自带 cyclictest **硬依赖 `/dev/timer`**，配置太重、编译不过」。**错了**：

- **运行期不需要 `/dev/timer`**。源码 `main()` 里默认值是
  `config.wait_method = W_NANOSLEEP (=0)`、`config.meas_method = M_GETTIME (=0)`、`config.timer_dev = NULL`；
  而 `if (config.wait_method == W_DEVTIMER || config.meas_method == M_TIMER_API) { timerfd = open(...) }`
  **只有显式 `-n 1` / `-m 1` 才会进这个分支**。默认路径走 `clock_nanosleep` + `clock_gettime`。
  （cyclic' 选项语义与上游不同：`-n`/`-m` **带参数**，0=nanosleep/gettime、1=devtimer/timer-API。）
- **真正的门槛是编译期**：`<nuttx/timers/timer.h>` 里的 `TCIOC_*` 与 `struct timer_status_s`
  被 **`CONFIG_TIMER`** 门控；不打开则 `-Werror` 下报
  `'TCIOC_GETSTATUS' undeclared` / `parameter has incomplete type` 直接失败。
- **修复 = 加一行** `CONFIG_TIMER=y`（+ `CONFIG_BENCHMARK_CYCLICTEST=y`）。
  已写入 `configs/arm64/rpi4-nuttx-nsh-inmate.defconfig`。

### 与 cRTOS 的差别（必须如实标注）
cRTOS 用的是 **上游 rt-tests 的同一个二进制**，靠它的 Linux-ABI `loader` 把该 ELF 搬进 RT 域执行；
我们用的是 **NuttX 对 cyclictest 的移植版**（`apps/benchmarks/cyclictest`）。因此对齐程度为：

| 层级 | 内容 | 状态 |
|---|---|---|
| **L2**（cRTOS 级） | **同一个上游二进制**经 loader 进 RT 域 | ❌ 需移植 loader/shadow（数周） |
| **L1** | **同族工具 + 同参数 + 同算法 + 同输出格式** | ✅ **本次达成** |
| L0 | cyclictest vs 自写 rtlat（两把尺子） | ⚠ 之前的做法，不可直接比对 |

> 写论文时用 **L1** 措辞：「两侧均使用 cyclictest（root 侧为 rt-tests，RT 域侧为其 NuttX 移植版），
> 相同参数、相同直方图口径」，并注明**非同一二进制**。

---

## 2. 通信方式是什么？

### 2.1 OS 之间：**共享内存 + IPI**，不经 hypervisor

原文（§II）：

> "Our cRTOS uses **shared memory and IPIs** for direct communication between the OSs
> **without the intervention of the hypervisor**."

> "Our implementation of remote system calls uses **IPIs as the inter-OS notification mechanism**
> to preserve the RT property while avoiding priority inversions."

RPC 流程（§IV，七步）：RT 侧 stub 把请求消息放进共享内存 → 发 IPI → Linux 侧服务线程取出、执行真实
syscall → 把应答放回共享内存 → 再发 IPI → RT 侧 stub 取出、唤醒调用线程。

### 2.2 共享内存区（Pi4B PoC，FreeRTOS DSI，§VIII-A）

> "We set up **three shared memory regions** between the GPVM and RTVM with sizes of
> **768 MB, 16 kB, 4 kB**. We assigned the rest of the resources to the GPVM."

> "We **slightly modified the hypervisor's source code**. We changed the **access permission for the
> shared memory regions to allow code execution**. This was required to place the **tRTOS kernel and
> rich RT application code in the shared memory**."

→ 768 MB 之所以这么大：**tRTOS 内核与 RT 应用代码本身就被放进这块可执行的共享内存**（loader/shadow 机制的基础）。

### 2.3 文件系统层面：**shadow 覆盖**（Fixstars x86 版文档）

> "access to devices/files/system services are **multiplexed by the location of the corresponding file**.
> The root filesystem are **overlayed as Nuttx's shadowing Linux's**.
> The **system calls will first try on Nuttx files before Linux files**."

→ 因此可以：RT 域访问 NuttX `/dev` 里的**实时设备**，也能访问 Linux `/dev` 里的**非实时设备**；
用命名管道与两边的进程通信。**Linux 内核本身不改**（与 PREEMPT_RT/Xenomai 的关键区别）。

---

## 3. 两个操作系统各自的分工

### 3.1 硬件分配（Access'25 图 13，Pi 4B）

| 资源 | **GPVM（Linux）** | **RTVM（tRTOS）** |
|---|---|---|
| CPU（4 核） | **3 个核**（每核各带 timer） | **1 个核** |
| RAM（4 GB） | **2.9 GB** | **256 MB** |
| VM 共享 | 768 MB + 16 KB + 4 KB | 同 |
| hypervisor | 32 MB | — |
| I/O | **SD 卡、以太网** | **2× GPIO、串口控制台** |

### 3.2 职责划分

- **RTVM（tRTOS）**：跑 **modified tRTOS** + rich RT 应用；负责 **RT 调度、内存管理、同步**；
  直接控制**分配给它的 RT 设备**；**实时关键的 syscall 在本地处理**
- **GPVM（Linux）**：跑 **verbatim GPOS 内核** + 非 RT 程序；控制**非 RT 设备**；
  **复杂 syscall、凭据等交给 Linux 侧**
- 总原则（§III）：**不共享 I/O**，而是把设备**直接分配给** GPVM 或 RTVM；
  并让共享内存与 IPI 功能对**两个 OS 内核**都可见
- hypervisor **不做调度、不提供虚拟 CPU**——只做**静态分区**；VM-Exit 只在 VM 初始化与
  PCI 枚举时发生，运行时**无 VM-Exit 开销**（§III，Pi4B/GIC-400 满足条件）

---

## 4. 测量任务的"独立完成情况"（最关键，也是最容易被问到的）

分三层看，**他们的测量并不是完全"与世隔绝"的**：

### ① 核级：**完全独立**
两个 VM 各占**独享核**（3 + 1），原文：
> "The VMs run in parallel and **never block each other**."

RT 测量线程所在核不被 Linux 的任何负载影响——这也是他们抖动远小于同板 Linux 的根本原因。

### ② 任务级：非 verbose 模式下**只有 RT 测量线程**
> "We executed cyclictest with the `-q` option to disable verbose printing.
> It created **only an RT thread** as the measurement thread."

### ③ ⚠ 但 verbose 模式**会自我干扰**，论文自己给出了证据
> cyclictest 默认 verbose → "It created an **additional non-RT thread** that printed the latest
> measurement result **by remote system calls**."

结果：**图 15b（verbose）比图 15a（非 verbose）抖动明显增大**（原文：
"Compared to Figure 15a, the jitters increased for all cases"）。

### ④ ⚠ 更根本的：**remote system call 本身会打断 RT 线程**
论文在 §VII 明确承认（并给出缓解）：

> "the interrupts of the RPC messages **can disrupt the execution of RT threads** …
> Sending an RPC message from the GPOS can create a **short priority inversion**."

缓解手段（三条 + seqlock）：

- tRTOS **持续把当前运行线程的优先级发布到共享内存**
- 若 RT 域当前运行线程优先级**低于**远调用发起者 → 服务程序**立即发 IPI**
- 否则**不发 IPI**，等 tRTOS 下次调度可运行线程时再轮询 RPC 消息唤醒
- 用 **seqlock** 让「选下一个线程 / 轮询待处理 RPC / 发布优先级」三者保持原子（避免漏发 IPI 导致优先级颠倒）

> **对我们的意义**：我们的 `rtlat` **完全不使用跨域输出**（先在 inmate 内存里缓冲、**跑完再经串口导出**），
> 结构上相当于他们的"非 verbose"模式；而他们的 verbose 模式是**反例**——正好说明"测量通路会污染被测对象"。
> 我们把逐样本打印从实时循环里挪到循环之后，就是为了避开同一个陷阱。

### 4.1 ✅ 我们把这条现象**在 Pi4B 上定量复现了**（2026-10-16）

cyclictest 的延迟时间线只能来自它的状态行（`… Act: N`，每 ~100 ms 一行），而状态行
只在**非 quiet** 模式输出。于是做了同参数 A/B（各 20 s / 20 000 次，1 ms，`-p 200`）：

| 模式 | Min | Avg | **Max** |
|---|---|---|---|
| `-q`（quiet —— 正式口径） | 4 µs | 4 µs | **9 µs** |
| 默认 verbose（出时间线所必需） | 4 µs | 5 µs | **64 µs** |

→ **max 被抬高约 7 倍，min/avg 基本不变**，与 cRTOS Fig.15a/15b 同型。
日志：`../results/raw/pi4_inmate_cyclictest_{quiet,verbose}20s.log`；
图：`../results/figures/fig_pi4_inmate_cyclictest_verbose_perturbation__verbose污染示意.png`。

**两个直接推论**：
1. 我们的**正式 1 h 数据一律用 `-q`**；verbose 数据只能当"污染演示"，不得混入对照表。
2. **时间线天然是抽取的**（约 1 % 采样），单发大事件会被漏掉 —— **尾部结论只引用全样本直方图**。
   （实测中就出现过：verbose 时间线自身 max 仅 5 µs，而工具累计 Max 是 64 µs。）

---

## 5. 可引用的抖动数值

**Access'25（Pi 4B，cyclictest，1 ms）**：

- 摘要：**minimum 5 µs jitter**
- 正文（带负载，尤其 `stream` 负载下的最大抖动）：
  - vanilla Linux：**> 190 µs**
  - PREEMPT_RT Linux：**> 100 µs**（cpu/cache 负载）、**> 190 µs**（vmem/stream）
  - Xenomai 3：**< 90 µs**
  - **NuttX-based cRTOS：60 µs**
  - **FreeRTOS-based cRTOS：20 µs**
  - 概述："for RTOSs, the jitters were **less than 100 µs** with and without loads"
- 图文：**Fig. 15a（非 verbose）/ 15b（verbose）**，横轴 latency 0–200 µs，5 档负载
  （noload / cpu / cache / vmem / stream），box plot；图例仅列 **SSI 变体**（NuttX+Linux、FreeRTOS+Linux）
- 另：**Fig. 16a/16b** 为中断延迟结果，结论与 cyclictest 一致

**VEE'20（x86-64）**：**约 4 µs** jitter + 有界最大延迟。

---

## 6. 由此产生的待办（写入我们稿子的修正清单）

1. **平台归属**：`[1]` 若指 Access'25 → 平台是 **Pi 4B**（不是 x86-64）；"≈4 µs" 应归 VEE'20。
2. **可比性上调**：cRTOS(Access'25) 与我们的 cRTOS-like **同板 Pi 4B** → 从"仅引用值"升级为
   "**同板引用值**"（仍非我们复现，但不能再说"平台不同、无法比较"）。
3. **工具对齐**：cRTOS 用的是**官方 rt-tests cyclictest**；我们 inmate 侧用的是**自写 rtlat**。
   → **2026-10-16 已解决**：`CONFIG_TIMER=y` + `CONFIG_BENCHMARK_CYCLICTEST=y` 后，
   NuttX 移植版 cyclictest 已在 inmate 里实测跑通、输出格式与 rt-tests 逐字一致（见 §1.3）。
   剩余工作 = 把两侧都用 cyclictest **重跑一遍同长 soak**，替换掉 `rtlat` 数据。
4. **测量污染**：论文中若引用 cRTOS 数字，需注明其 **verbose 模式会自增抖动**（Fig.15b），
   论文自身给出的口径是**非 verbose**。
5. **"不改 Linux 内核"** 是 cRTOS 的核心卖点，但**它改了 hypervisor**（开放共享内存的代码执行权限）——
   引用时应完整表述，避免只强调"不改 Linux"。
