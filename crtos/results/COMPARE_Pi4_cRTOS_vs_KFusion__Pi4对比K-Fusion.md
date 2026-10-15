# Pi4 cRTOS-like 实测 vs K-Fusion 仓库数据 对照

> 本文档由 WorkBuddy 于 2026-10-15 生成。
> **左侧数据来源**：GitHub 仓库 `1147426025lu-bot/K-Fusion` 已跟踪文件（`kfusion/docs/benchmarks/*.json` 与论文稿 `论文初稿__K-Fusion_中文完整版.md` 图 3 读数）。
> **右侧数据来源**：本次在 Raspberry Pi 4B 上实测（Jailhouse 分区 + NuttX tickless inmate）。
> 指标口径统一为**周期抖动绝对最大值 `abs_max`**（请求释放时刻与实际释放时刻偏差的最大值，单位 µs）。

---

## 1. 主对照表

| 系统 / 路径 | 平台 | 隔离机制 | 测量时长 | avg | **abs_max** | 来源 |
|---|---|---|---|---|---|---|
| **K-Fusion 融合（论文图3读数）** | Pi 5 aarch64 | 单域 PREEMPT_RT，无 hypervisor | 15 min | — | **3.391 µs** | 仓库实测 |
| K-Fusion 融合 best（optimization 记录） | Pi 5 | 同上（no_probe_rt） | 5 min | — | **2.175 µs** | 仓库实测 |
| K-Fusion 融合 long（optimization 记录） | Pi 5 | 同上 | 15 min | — | 4.833 µs | 仓库实测 |
| K-Fusion 融合 L2（isolation A/B） | Pi 5 | 同上（JITTER_COMPENSATION on） | 15 min | — | 9.383 µs | 仓库实测 |
| K-Fusion 融合 L3（isolation A/B） | Pi 5 | 同上 | 15 min | — | 13.455 µs | 仓库实测 |
| 用户态基线（论文图3读数） | Pi 5 | 用户态 cyclictest | 15 min | — | 16.000 µs | 仓库实测 |
| Timed C（论文图3读数） | Pi 5 | sdelay | 15 min | — | 857.759 µs | 仓库实测（批次待统一） |
| **NuttX inmate（cRTOS-like，本次）** | **Pi 4B** | **Jailhouse 分区 + NuttX tickless** | **1 min** | **5 µs** | **6 µs** | **本次实测** |
| root cell Linux（本次同板基线） | Pi 4B | 同板 Linux（SMP PREEMPT，非 RT，无调优） | 1 min | 71 µs | 2086 µs | 本次实测 |
| cRTOS（文献引用） | x86-64 | Jailhouse + sRTOS | — | ~4 µs | — | IEEE Access 2025（cited） |

---

## 2. 关键读数与差距

### 2.1 同板对照（最公平的一格）
| 指标 | root cell Linux | NuttX inmate | 改善倍数 |
|---|---|---|---|
| avg | 71 µs | 5 µs | **14.2×** |
| abs_max | 2086 µs | 6 µs | **347.7×** |
| >200µs 溢出 | 13 次 | 0 次 | — |

→ **在同一块 Pi4B 上，把控制任务从 Linux（SMP PREEMPT、未调优）移进 Jailhouse 分区内的 NuttX tickless 实时域，最坏抖动从 2086 µs 降到 6 µs。** 这正是 cRTOS 论文"空间硬隔离"论点的实测复现。

### 2.2 与主线 K-Fusion 的可比性
- NuttX inmate 的 **6 µs** 与 K-Fusion 融合路径 **3.391 µs（Pi5, 15min）**、文献 cRTOS **~4 µs（x86）** 处于**同一数量级**。
- ⚠ **三条硬约束**，论文中必须并列标注，不得直接比优劣：
  1. **板子不同**：Pi4B（BCM2711/GIC-400）vs Pi5（BCM2712/GIC-600）；
  2. **时长不同**：本次 inmate 仅 **1 min**，K-Fusion 正式值为 **15 min**（`abs_max` 随观察窗口增长，1min→15min 的尾部会抬升）；
  3. **机制根本不同**：单域无 hypervisor（K-Fusion）vs hypervisor 硬分区（cRTOS/NuttX）。

### 2.3 口径风险（写入 Threats to Validity）
- root cell 镜像内核是 **SMP PREEMPT 而非 PREEMPT_RT**，其 2086 µs 属"未调优上限估计"；用 RT 内核或 `isolcpus` 调优后基线会显著改善。两边都不是 PREEMPT_RT 时，同板对照的公平性反而更好（两域同内核基线）。

---

## 3. 待办（使对照可直接入表）

| # | 事项 | 目的 |
|---|---|---|
| 1 | NuttX inmate 跑 **15 min 标准 soak** | 与 K-Fusion 15min 口径对齐，消除时长偏差 |
| 2 | root cell 用 `isolcpus`/RT 内核复测 | 给出更公平的同板 Linux 基线 |
| 3 | 统一 stamp 与 raw_log 路径（见同目录 `pi4_crtos_soak__Pi4对比测量.json`） | 复用 `paper_summarize_results__论文结果汇总.py` 出图 |

---

## 4. 数据出处（GitHub 仓库已跟踪文件）

- `kfusion/docs/benchmarks/isolation_ab_results__隔离AB结果.json` → L2=9383ns / L3=13455ns（15min）
- `kfusion/docs/benchmarks/optimization_continue__优化记录.json` → best=2175ns（5min）/ long abs_max=4833ns（15min）
- `kfusion/docs/paper/论文初稿__K-Fusion_中文完整版.md` 图 3 读数 → fused 3.391µs / userspace 16.000µs / Timed C 857.759µs
- 本次实测 → 同目录 `pi4_crtos_soak__Pi4对比测量.json`
