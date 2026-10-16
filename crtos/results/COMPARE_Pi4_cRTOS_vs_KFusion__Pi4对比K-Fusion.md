# Pi4 cRTOS-like 实测 vs K-Fusion 仓库数据 对照

> 本文档由 WorkBuddy 于 2026-10-16 更新（**15 min 正式口径 + 一处基线修正**）。
> **左侧**：GitHub 仓库 `1147426025lu-bot/K-Fusion` 已跟踪文件（`kfusion/docs/benchmarks/*.json`、论文稿图 3 读数）。
> **右侧**：本项目在 Raspberry Pi 4B 实测（Jailhouse 分区 + NuttX tickless inmate）。
> 指标统一为周期抖动绝对最大值 `abs_max`（请求释放时刻与实际释放时刻的偏差，单位 µs）。

## 0. ⚠️ 一处重要修正（请先读）

早前记录的「root cell 60s：avg 71µs / max **2086µs**」**是错误配置**：当时的 `cyclictest -Sp3` 被解析成 `-S -p3`，即**优先级 3**（FIFO 里几乎最低），并非 FIFO99。
用正确配置（`taskset -c3` + `--priority=99`）在 15 min 上复测，root cell 的 max 只有 **96µs**。

→ 据此得出的「**同板改善 347×**」**撤回**。正确口径见下表。

## 1. 主对照表（15 min 为正式口径）

| 系统 / 路径 | 平台 | 机制 | 时长 | avg | **abs_max** | 来源 |
|---|---|---|---|---|---|---|
| K-Fusion 融合（论文图3） | Pi 5 | 单域 PREEMPT_RT，无 hypervisor | 15 min | — | **3.391 µs** | 仓库实测 |
| K-Fusion 融合 best | Pi 5 | 同上（no_probe_rt） | 5 min | — | 2.175 µs | 仓库实测 |
| K-Fusion 融合 long | Pi 5 | 同上 | 15 min | — | 4.833 µs | 仓库实测 |
| K-Fusion 融合 L2 / L3 | Pi 5 | 同上 | 15 min | — | 9.383 / 13.455 µs | 仓库实测 |
| **NuttX inmate（cRTOS-like）** | **Pi 4B** | **Jailhouse 分区 + NuttX tickless** | **15 min** | **5 µs** | **5 µs** | **本工作实测** |
| root cell Linux（正确配置） | Pi 4B | 同板 Linux（SMP PREEMPT，非 RT） | 15 min | 18 µs | **96 µs** | 本工作实测 |
| cRTOS（文献引用） | x86-64 | Jailhouse + sRTOS | — | ~4 µs | — | IEEE Access 2025 |

**60 s 旧口径（仅供趋势）**：NuttX inmate avg 5 / max 6 µs。

## 2. 同板对照（最公平的一格，15 min）

| 指标 | root cell Linux | NuttX inmate | 改善 |
|---|---|---|---|
| avg | 18 µs | 5 µs | **3.6×** |
| abs_max | 96 µs | 5 µs | **19.2×** |
| >200µs 溢出 | 0 | 0 | — |

→ 在同一块 Pi4B 上，把控制任务从 Linux 移进 Jailhouse 分区内的 NuttX tickless 实时域，**最坏抖动 96µs → 5µs**；inmate 在 90 万次唤醒中**零抖动**（直方图 `5:900000`）。

## 3. 与主线 K-Fusion 的可比性
- inmate 15min **5 µs** 与 K-Fusion fused **3.391 µs（Pi5, 15min）**、cRTOS 文献 **~4 µs（x86）** 处于**同一数量级**。
- ⚠ 论文中须**并列标注**三条约束，不得直接比优劣：
  1. **板子不同**：Pi4B（GIC-400）vs Pi5（GIC-600）；
  2. **机制不同**：hypervisor 硬分区（cRTOS/NuttX）vs 单域无 hypervisor（K-Fusion）；
  3. root 侧内核为 **SMP PREEMPT，非 PREEMPT_RT**。
- 时长现已对齐（均为 15 min）✓。

## 4. 口径与风险（写入 Threats to Validity）
- root 基线用非 RT 内核：96µs 已属不错；用 PREEMPT_RT / `isolcpus` 调优后会更低——但这不影响「分区内实时域抖动更小」的结论。
- inmate 直方图 90 万次**全部为 5µs**，说明 NuttX tickless 在该负载下抖动被量化到 1µs 且无异常样本。这是「硬实时域」的强证据，但须注明是**单任务、无干扰**条件下的结果。
- 60 s 与 15 min 两种时长都做了：结论一致（inmate 5~6µs），说明短测未掩盖尾部恶化。

## 5. 数据出处
- 仓库（GitHub 已跟踪）：`kfusion/docs/benchmarks/isolation_ab_results__隔离AB结果.json`、`optimization_continue__优化记录.json`、`kfusion/docs/paper/论文初稿__K-Fusion_中文完整版.md` 图 3 读数。
- 本次实测：`crtos/results/pi4_crtos_soak__Pi4对比测量.json`；
  inmate 原始日志 `crtos/results/raw/pi4_inmate_rtlat_15min.log`；
  root 原始 `Pi:/root/kfusion-pi4/results/rootcell_15min.txt`（+`.hist`）。
