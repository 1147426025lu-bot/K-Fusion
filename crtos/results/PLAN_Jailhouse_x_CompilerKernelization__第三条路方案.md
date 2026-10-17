# 第三条路方案：编译器内核化 × Jailhouse 硬分区

> 目标：让**同一份 LLVM 内核化产物**在 Linux 内核模块域与 Jailhouse 裸机 inmate 域之间可迁移运行，
> 用受控实验把实时性改善归因到「编译器层 / 隔离层 / 架构层」。
> 前置查新结论见 §6；与 cRTOS 的关系见 §8。

---

## 1. 一句话定位

**不是**"在 cell 里跑 RTOS 测抖动"（cRTOS 与 Jailhouse 官方 gic-demo 都已做过），
**而是**"编译器把任务、定时器、测量装置融合成一个专用内核，这个内核可以在三个运行域里跑同一份代码"。

---

## 2. 为什么还需要第三条路

### 2.1 现有 A/B 域已经触到天花板

| 域 | 配置 | abs_max（20 min，全量样本） | 瓶颈 |
|---|---|---|---|
| A | 普通 Linux + fused ko | 268.393 µs（15 min 口径） | Linux 全栈抖动 |
| B | Linux + L4 隔离 + rt-bubbles + 测量静默 | **7.834 µs**（最优单轮 5.465 µs） | 2-5 次/20 min 的**单发外部事件** |

B 域已经把所有软件/配置级手段试完（见 COMPARE §8.1-8.4，含 4 项已回退的负向尝试）。
剩余尾部完全由外部事件决定，**软件层已无可挖空间**。

### 2.2 更重要的：编译器贡献在 B 域根本测不出来

LLVM pass 的收益量级是 **几十~几百 ns**（指令数减少、死代码消除、寄存器压力降低），
而 B 域的 OS 抖动底噪就有 **±600 ns（p99）/ ±7.8 µs（abs_max）**——编译器优化被完全淹没。

> 这是当前工作一个真实的**方法论困境**：我们做了内核化 pass，却无法量化它本身的价值。

域 C（裸机 inmate，无 OS 调度、无中断风暴）恰好提供了**一台能看清 ns 级差异的显微镜**。

---

## 3. 系统架构

### 3.1 三域一产物

```
                    ┌─────────────────────────────────────────┐
                    │  LLVM 管线（域无关，唯一产物来源）        │
   app.c ──clang-IR─▶│ PLCFusionPass → PLCLowJitterPass → llc   │
                    │        → official_cycletest_kernel.o     │
                    └───────────────┬─────────────────────────┘
                                    │  同一个 .o
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
   ┌─────────┐               ┌─────────────┐             ┌──────────────┐
   │ 域 A     │               │ 域 B         │             │ 域 C（新增）  │
   │ Linux    │               │ Linux + L4   │             │ Jailhouse    │
   │ 无隔离    │               │ + rt-bubbles │             │ inmate 裸机   │
   ├─────────┤               ├─────────────┤             ├──────────────┤
   │ Linux 桩 │               │ Linux 桩     │             │ inmate 桩（新）│
   │ 268 µs   │               │ 7.834 µs     │             │ 目标 ≈5 µs    │
   └─────────┘               └─────────────┘             └──────────────┘
                                     ▲                           ▲
                          已有（plc_abi：POSIX 桩 / Linux 内核桩）  │
                                              第三套桩：inmate 裸机桩（待写）
```

### 3.2 关键接缝：`plc_abi__运行时ABI.h`

编译器产物的外部依赖只走这层 ABI（~30 个 `plc_*` 符号）：

| 能力 | POSIX/Linux 桩（已有） | inmate 裸机桩（待写） |
|---|---|---|
| 时间源 `plc_ktime_get_ts` | `clock_gettime` / `ktime_get` | `mrs cntpct_el0` + `cntfrq_el0` 换算 |
| 睡眠 `plc_nanosleep` | `clock_nanosleep` / hrtimer | 写 `cntp_cval_el0` + `wfi`，中断返回 |
| 内存 `plc_kmalloc/kfree` | malloc / kmalloc | 静态池（bump allocator，无碎片） |
| 输出 `plc_printk/puts` | printf / printk | mini-UART 直接写（**仅非测量路径**） |
| timer 对象 `plc_timer_*` | POSIX timer / hrtimer | generic timer 比较器 + PPI 30 |

> **这是整个方案的技术核心，也是它的最大优势**：内核化产物对 OS 零依赖，
> 换域 = 换一套桩 + 一个链接脚本，**Clang / opt / llc 全链不改一行**。

---

## 4. 实施阶段（每阶段有独立验收，可随时停）

### 阶段 0：地基校验（半天，无风险）
- 跑 `crtos/jailhouse_overlay_pi4/scripts/reproduce_pi4_crtos__一键复现.sh`
- `jailhouse enable /etc/jailhouse/rpi4.cell` + 加载现有 NuttX cell，确认 5/5/5 µs 可复现
- **验收**：NuttX inmate NSH 起来，`rtlat` 跑出与历史一致的数值

### 阶段 1：裸机 hello + generic timer（1-2 天，风险低）
- 写最小 bare-metal inmate：UART 打点 → 配置 CNTP PPI 30 → 中断里计数
- cell 配置新增 **PPI 30 放行**（现有配置只放了 SPI 24 / SPI 125）
  ```c
  { .address = 0xff841000, .pin_base = 32,
    .pin_bitmap = { 1u << (30 - 32 + 32) /* PPI 30：需按 jailhouse PPI 编码核对 */ } }
  ```
  > ⚠️ 具体编码要按 jailhouse arm64 的 PPI 表示法核对（`pin_base = 32` 段 + 偏移），
  > 现有配置里 `pin_base=160` 的 `1<<24` 是 SPI 24（BCM sys timer 相关），不要照抄。
- **验收**：裸机中断能周期性触发，串口能打印计数（**这一步跑通，方案就成功 70%**）

### 阶段 2：inmate 桩（3-5 天，主要工作量）
- 新建 `kfusion/src/plc_runtime_stubs__inmate桩.c`
- 参照 `plc_runtime_stubs__POSIX桩.c`（91 个符号）逐项替换，约 60% 可复用（字符串/格式化等纯计算部分）
- 测量路径的关键设计：
  - 时间戳：`cntpct_el0` 单次读取（≈几个 cycle，比 `clock_gettime` 系统调用快 2 个数量级）
  - 唤醒：`cntp_cval_el0 = 目标` + `wfi`，ISR 里只更新下一个绝对时刻（**无调度器参与**）
  - 样本存储：静态数组（1 kHz × 20 min ≈ 1.2 M × 8 B ≈ 9.6 MB，cell 有 128 MB）
- **验收**：`official_cycletest_kernel.o` 与 inmate 桩链接成功，无未定义符号

### 阶段 3：跨域链接与镜像（1-2 天，坑最多）
- 新增 inmate 链接脚本：入口 `0x480000`、RAM 起 `0`、`.data.config` 段（jailhouse 要求）
- 复用已踩过的坑清单：`CONFIG_RAM_SIZE` 无关（裸机无 NuttX）；`-a 0x480000` 填 **guest 虚拟地址**；`objcopy` 段名 `.data.config`；AUX 块整映射 `0xfe215000 + 0x100`
- **验收**：`jailhouse cell load/start` 后串口出现 `FusedSummary: cycles=... min_ns=... max_ns=...`

### 阶段 4：回传与出图（1 天）
- 两条路，任选其一：
  - **简单**：串口直接输出 `FusedSummary`（数值可信，但无全量直方图）
  - **完整**：cell 写 comm_region 共享内存（现有配置已留 `0x80000000` 4 KB）→ root cell 落盘 v2 格式 `jitter.bin` → **现有出图脚本直接可用**
- **验收**：`plot_frequency_polygon__抖动绘图.py` 出图，与 A/B 域同款图

### 阶段 5：实验矩阵（关键，见 §5）

---

## 5. 实验矩阵：把三层贡献干净地拆开

| 编号 | 运行域 | 被测对象 | 度量的是 | 状态 |
|---|---|---|---|---|
| **A** | Linux 无隔离 | fused ko | 起点 | ✅ 已测 268 µs |
| **B** | Linux + L4 + rt-bubbles | fused ko（同一 .o） | **隔离层贡献** A→B | ✅ 已测 7.834 µs |
| **C1** | NuttX inmate | 同算法 NuttX 用户态 app（rtlat） | 架构层 + OS 开销 | ✅ 已测 5/5/5 µs |
| **C2** | **裸机 inmate** | **fused 产物（同一 .o）** | **架构层贡献** B→C2 | ⬜ 本方案 |
| **C3** | 裸机 inmate | **手写等价实现**（不用 LLVM pass） | **编译器贡献** C3→C2 | ⬜ 本方案 |
| **D** | Linux | 原生 cyclictest（未融合） | 编译器贡献（Linux 域） | ✅ 已有 §7.1（含自扰陷阱） |

**C2 vs C3 是整个方案最有价值的一格**：

- 同一套算法逻辑、同一块硬件、同一个域
- 唯一差别是「手写 C」还是「LLVM pass 内核化生成」
- 在 B 域这个差异（几十 ns）会被 7.8 µs 抖动淹没；在 C2/C3 的裸机域，底噪只有 timer 分辨率级别，**差异可测**
- 这直接回答一个审稿必问的问题：**"你的编译器 pass 到底带来了多少收益？"**

---

## 6. 创新点（对照查新结果，精确表述）

### ✅ 可主张（检索确认无直接先例）

**创新点 1：编译器内核化（compiler kernelization）——把任务、定时器、测量装置在编译期融合为单一专用内核**

- 查新对比：RuSTy（plc-lang/rusty）与 CN118778940B 专利都用 LLVM 编译 IEC 61131-3，但走的都是**常规优化管线**（Mem2Reg / DCE / 内联 / 循环展开），产出的是通用可执行文件；
- 我们的 `PLCFusionPass` / `PLCLowJitterPass` 做的是**结构级融合**：把业务逻辑、定时驱动、抖动测量合成一个无 OS 依赖的执行体，使其可跨域部署。
- 措辞注意：**不要用 "kernel fusion"**（该词被 GPU/AI 领域 TVM/XLA/MLIR 强占用），统一用 `compiler kernelization` / `task kernel synthesis`。

**创新点 2：同一编译器产物的跨运行域受控归因**

- 查新对比：cRTOS（VEE'20 / IEEE Access 2025）原文明确声明 *"We did not measure native, bare-metal performance"*，且其 B/C 域跑的不是同一份被测物；
- CTU 2016 学位论文做过"bare-metal vs in-cell"对比，但对象是 L4/Fiasco 移植 + 内存带宽，不是编译器产物。
- 我们的 A/B/C2 用**同一份 `.o`**，唯一自变量是运行域 → 改善可干净归因到隔离层/架构层。

**创新点 3：裸机域作为编译器贡献的"显微镜"（方法论）**

- 实时编译器优化的收益（ns 级）在 OS 抖动（µs 级）下不可测，这是编译器与实时系统交叉领域的老问题；
- 用 hypervisor 裸机分区提供一个"无 OS 噪声、但与最终生产环境同板同源"的测量域，**使编译器优化的净收益首次可量化**（C2 vs C3）。

**创新点 4：测量装置自扰的系统化消融方法学**

- 查新对比：Linux Foundation 官方文档承认 *"the test is an observer, not an invisible observer"*；cRTOS 只评估了 verbose 打印 / 远程系统调用开销；
- 我们做了**三级系统化消融**（监控器 `ps` 全表扫描 → 看门狗 debugfs 读取 → SD 卡日志回写），>3 µs 事件 328→2；
- 并给出**反证实验**：人为偏置 2529 ns 被 EWMA 闭环完全吸收（稳态均值 0.1 ns），**证明指标无法靠调参"刷低"**——无先例。

### ❌ 不可主张（已被占用，必须规避）

| 主张 | 已被谁做 |
|---|---|
| hypervisor 分区提升实时性 | Jailhouse 原论文（Ramsauer/Kiszka 2017）、cRTOS |
| cell 内 µs 级 timer 抖动 | **Jailhouse 官方 gic-demo**（"Timer fired, jitter: 999 ns"）、apic-demo |
| NuttX/FreeRTOS 在 cell 里跑 cyclictest | cRTOS（Pi 4B，5 µs） |
| 用 LLVM 编译 PLC 语言 | RuSTy、CN118778940B |
| 裸机 cell 比 Linux 抖动小 | cRTOS、TI E2E 实测、大量工程博客 |

> 一句话：**数字不新，组合与控制变量新。**

---

## 7. 风险与回退

| 风险 | 概率 | 缓解 |
|---|---|---|
| PPI 30 放行编码错误 → 裸机无中断 | 中 | 先用 `jailhouse` 自带 gic-demo 验证 GIC 通路，再改配置 |
| 静态池耗尽 / 1.2 M 样本超 128 MB | 低 | 9.6 MB 远小于 128 MB；可改 ring 抽取 |
| 桩覆盖不全 → 链接未定义符号 | 中 | 链接期即暴露，逐项补（POSIX 桩 91 个符号可参考） |
| 编译器产物含 Linux 专有构造 | 中 | `PLCLowJitterPass` 已做无 OS 化；剩余依赖由桩吃掉 |
| **效果不达预期（C2 不比 C1 好）** | 中 | **这本身是可发表的负结果**（说明 NuttX 的 tickless + 高优先级已足够），且 C2/C3 的编译器贡献度量仍成立 |

**回退策略**：每个阶段独立提交、独立验收；域 C 失败不影响已完成的 A/B 域数据与 COMPARE 文档。

---

## 8. 与 cRTOS 的关系（论文口径）

继续沿用 COMPARE 文档的三条约束：

1. **机制不同**：cRTOS = tRTOS + GPOS 双域协作（远程系统调用）；我们是单任务内核化产物跨域部署；
2. **被测物不同**：cRTOS 测的是现成 cyclictest 在 NuttX 里的表现；我们测的是 LLVM pass 生成的专用内核；
3. **不比较优劣**：cRTOS 是**域 C1 的既有参照**，我们的 C2 与其并列呈现（同板、同 128 MB cell、同 1 kHz），
   差异只归因于"被测物是否经过编译器内核化"，不声称击败 cRTOS。

---

## 9. 工期估计

| 阶段 | 内容 | 估计 |
|---|---|---|
| 0 | 地基校验 | 半天 |
| 1 | 裸机 hello + generic timer | 1-2 天 |
| 2 | inmate 桩 | 3-5 天 |
| 3 | 跨域链接与镜像 | 1-2 天 |
| 4 | 回传与出图 | 1 天 |
| 5 | C2/C3 实验 + 数据 | 2-3 天（含多轮 20 min 浸泡） |
| — | **合计** | **约 2 周** |

---

## 10. 立即可做的第一步

```bash
# Pi 上，先确认现有 NuttX cell 链路仍然通（阶段 0）
sudo bash crtos/jailhouse_overlay_pi4/scripts/reproduce_pi4_crtos__一键复现.sh --root-soak 0
# 然后跑一遍现有 rtlat，确认 5/5/5 µs 可复现，作为 C1 基线
```
