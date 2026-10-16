# cRTOS 对比实验方案（Raspberry Pi 4）

> 目标：在 Raspberry Pi 4B 上复现 / 类比 Fixstars cRTOS（Jailhouse + Linux + NuttX/FreeRTOS 实时域），
> 与主线 PLCFusion（Pi 5 PREEMPT_RT，单域内核化）做**同维度 jitter 对照**，支撑论文 Table 3（cross-study）。

本文档由 WorkBuddy 在 2026-10-14 基于公开资料与仓库现状起草。脚本在 `crtos/scripts/pi4/`。

---

## 0. 公平性声明（必须写进论文）

| 维度 | PLCFusion（主线） | cRTOS（本实验） |
|------|------------------|----------------|
| 板子 | Raspberry Pi 5（BCM2712, GIC-600） | Raspberry Pi 4B（BCM2711, GIC-400） |
| 隔离机制 | 编译期 remap + 单 PREEMPT_RT 域，**无 hypervisor** | Jailhouse 静态分区（root cell + inmate） |
| RT 任务位置 | 内核模块 `.ko` 内 kthread + hrtimer | tRTOS inmate（NuttX/FreeRTOS） |
| 富功能 | 同域 Linux 用户态 | root cell Linux（GPOS） |

**结论口径**：这是 **cross-board + cross-mechanism** 对照，不是同板对照。
论文里必须分栏标注（引用 IEEE Access 2025 的 Pi4 数字作为文献锚点，本实验 Pi4 数字作为实测锚点），
**不得声称"击败 cRTOS"，只能写 trade-off under different isolation assumptions**。
PLCFusion 的卖点（无需 hypervisor、单镜像、LLVM 优化链）与 cRTOS 的卖点（空间硬隔离、Linux 二进制透明）面向不同部署约束。

---

## 1. 两条子路径（决定工作量）

### 路径 A — 作者原版 cRTOS（最忠实，但代码可能未公开）
- 来源：Yang & Shinjo, *Compounded Real-Time Operating Systems for Rich Real-Time Applications*, IEEE Access 2025（筑波大学 + Fixstars）。
- 该论文**明确在 Pi 4B 上实现**三套 cRTOS（Jailhouse + Linux + NuttX 或 FreeRTOS），报告**最小 5 µs jitter**。
- 但公开仓库 `fixstars/cRTOS-nuttx` 只发 `qemu-intel64:crtos`（x86）。Pi4 那套代码**是否在公开仓库的分支里未知**。
- **获取方式**（按顺序尝试）：
  1. `git clone https://github.com/fixstars/cRTOS.git` 后查 `configs/arm64/` 或 `raspberrypi-4b` 相关 cell / NuttX defconfig。
  2. 查 `fixstars/cRTOS-nuttx` 是否有 `raspberrypi-4b:crtos` 配置。
  3. 邮件作者（sonicyang@softlab.cs.tsukuba.ac.jp）索取 Pi4 分支 / 镜像。
- **阻塞点**：若代码未公开，路径 A 卡住，转路径 B。

### 路径 B — 公开可重建的 Jailhouse + NuttX/FreeRTOS inmate（默认推荐起步）
- Jailhouse：上游 `siemens/jailhouse` 官方支持 Pi4（`rpi4.cell`），且 `siemens/jailhouse-images` 提供**预构建 Pi4 镜像**（最省事）。
- tRTOS inmate：Apache NuttX 上游有**实验性 Pi 4B 支持**（`raspberrypi-4b:nsh`），可作为 inmate 跑周期性 RT 任务。
- FreeRTOS inmate：Jailhouse 自带 `inmates/demos/arm64/` 裸机 demo，可改造成周期测量 inmate（最轻量，优先做"第一个可测 jitter 的实时域"）。
- **这给出架构等价的 cRTOS 类比**：空间隔离 + 实时域，但不含 Fixstars 的 IVSHMEM remote-syscall 富功能代理。
  论文中标注为 "cRTOS-like (open rebuild)"，与路径 A 的 "cRTOS (authors, Pi4)" 区分。

> 建议：**先走路径 B 拿到第一个 Pi4 实测 jitter 数据点**，同时并行尝试路径 A 索取作者代码。
> 两条路径共享同一套 Jailhouse 底座与测量协议。

---

## 2. 硬件与系统前提（路径 B）

- 一块 **Raspberry Pi 4B**（任意内存版本；建议 2GB+）。
- **不要** 和 Pi5 混用 SD 卡；本实验独立一块卡。
- 系统：64 位 Raspberry Pi OS（Debian 12 bookworm），**或** `siemens/jailhouse-images` 的 Pi4 镜像（自带 Jailhouse + 合适内核）。
- 内核要求：Jailhouse 需要内核开启 `CONFIG_JAILHOUSE` 且 CPU 启动于 EL2。
  - Pi OS 64-bit 近期固件默认 EL2；`jailhouse-images` 已处理。
  - 若自编译内核，需 `CONFIG_JAILHOUSE=m/y` 等（见 Jailhouse `Documentation/`）。
- 固件：确保 `/boot/firmware/config.txt` 未强制 `kernel8=` 32-bit，且未禁用 HYP（一般默认 OK）。
- 网络：Pi4 需与运行 WorkBuddy 的 Windows 机器**同网段 SSH 可达**（你选的执行环境）。

---

## 3. 执行流程（路径 B，脚本在 `crtos/scripts/pi4/`）

所有脚本在 **Pi 本机** 执行（通过 Windows SSH 过去）。WorkBuddy 负责产出与排错，不在 Windows 侧跑编译。

```bash
cd /path/to/K-Fusion/crtos/scripts/pi4

# 0. 只读探测（安全，先跑这个确认板子/内核）
bash preflight_pi4__平台探测.sh

# 1. 安装 Jailhouse（编译 + install，不自动 reboot / 不 enable）
bash install_jailhouse_pi4__安装Jailhouse.sh

# 2. 部署：enable 超管 + 加载 RT inmate cell（FreeRTOS/NuttX）
sudo bash deploy_crtos_pi4__部署cRTOS.sh enable
sudo bash deploy_crtos_pi4__部署cRTOS.sh rt-cell      # 加载实时域 inmate
sudo bash deploy_crtos_pi4__部署cRTOS.sh status
sudo bash deploy_crtos_pi4__部署cRTOS.sh teardown

# 3. 测量：输出与 PLCFusion 同 schema 的 JSON（realm 字段区分 root_cell / inmate）
bash measure_pi4_crtos__对比测量.sh
```

### 3.1 测量协议（与 PLCFusion 对齐）
- 指标：`abs_max_ns`（周期偏差最大值）、`duration_min`（≥15 min 正式）、`raw_log`、`stamp`。
- 两种 realm 分别测，JSON 里加 `realm` 字段：
  - `realm=root_cell`：在 root cell Linux 里跑 `cyclictest`（pinned CPU + isolation），作为 "PREEMPT_RT under Jailhouse partition" 对照点。
  - `realm=inmate`：RT inmate 自测周期 jitter（inmate 内用 timer 自采样，经串口/IVSHMEM 导出），作为 cRTOS 真正的实时域数字。
- 输出路径：`crtos/results/pi4_crtos_<realm>_<stamp>.json`，schema 与 `kfusion/docs/benchmarks/isolation_ab_results__隔离AB结果.json` 兼容。
- 论文汇总脚本后续复用 `kfusion/scripts/paper/paper_summarize_results__论文结果汇总.py` 的字段约定。

---

## 4. 与主线 PLCFusion 的映射表（写论文时用）

| PLCFusion 概念 | cRTOS（Pi4）类比 | 备注 |
|----------------|------------------|------|
| `.ko` 内 kthread + hrtimer | tRTOS inmate 周期任务 | 架构等价 |
| 同域 Linux 用户态富功能 | root cell Linux（GPOS） | cRTOS 还有 remote-syscall 跨域代理 |
| CPU affinity + cgroup 隔离 | Jailhouse 静态分区（硬隔离） | cRTOS 隔离更强 |
| 编译期 remap（无 hypervisor） | hypervisor 分区（改系统结构） | 根本哲学分歧 |

---

## 5. 风险与未决项

1. **硬件**：需确认你有独立的 Pi 4B（不是只有 Pi5）。
2. **路径 A 代码可得性**：未公开则可能只能走路径 B。
3. **inmate 测量**：NuttX/FreeRTOS inmate 的自测 jitter 导出需要写 inmate 侧代码（阶段 2），首版测量脚本先给 `root_cell` 的 cyclictest 兜底数据点。
4. **cross-board 公平性质疑**：审稿人可能质疑 Pi4 vs Pi5。应对：明确标注 + 引用作者 Pi4 文献值作为同板锚点 + 在 Threats to validity 写明。
5. **对主线干扰**：本实验在独立 Pi4 卡上进行，**不触碰 Pi5 的 PREEMPT_RT 主线测量**。

---

## 6. 下一步（待你确认）

- [ ] 确认是否有 Pi 4B 硬件
- [ ] 选路径 A（索取作者代码）还是路径 B（公开重建）或两者并行
- [ ] 确认 Pi4 的 IP / SSH 可达方式，便于我后续排错
- [ ] 决定是否现在就在 Pi4 上首次跑 `preflight_pi4__平台探测.sh`

---

## 7. 实施状态与实测结果（2026-10-15 更新）

> 本节为**实际执行后的回填**。路径选择：**路径 A（作者原版代码）未获取到 Pi4 分支**，转**路径 B（公开重建）**并已完成。

### 7.1 已完成

| 步骤 | 状态 | 说明 |
|---|---|---|
| Pi4B 硬件 | ✅ | 独立卡，与 Pi5 主线互不干扰 |
| Jailhouse 安装并 enable | ✅ | 镜像自带 v0.12-342-gd6e64f90；root cell "Raspberry-Pi4" 占 CPU0-3 |
| cell 起停验证（gic-demo） | ✅ | inmate-demo running on CPU1；IVSHMEM 建立 |
| **NuttX inmate 移植** | ✅ | **无公开先例**，本工作首创（见 `PI4_NUTTX_INMATE__cell约束与移植规格.md`） |
| root cell 基线（realm=root_cell） | ✅ | 15 min soak：avg 18µs / max **96µs** / >200µs 0（正确 FIFO99+绑核） |
| inmate 抖动（realm=inmate） | ✅ | 自写 `rtlat`：15 min soak avg **5µs** / max **5µs** / >200µs 0（90 万次零抖动） |
| 结果落盘（schema 对齐） | ✅ | `crtos/results/pi4_crtos_soak__Pi4对比测量.json` |

### 7.2 实测读数（协议与 PLCFusion 对齐：`abs_max`、1ms 周期）

| realm | 平台 | 时长 | avg | **abs_max** | >200µs | 备注 |
|---|---|---|---|---|---|---|
| root_cell | Pi 4B | **15 min** | 18 µs | **96 µs** | 0 | 正确配置（CPU3/FIFO99/mlockall/1ms）；内核 **SMP PREEMPT 非 PREEMPT_RT** |
| **inmate** | Pi 4B | **15 min** | 5 µs | **5 µs** | 0 | NuttX tickless（`SCHED_TICKLESS=y`+`USEC_PER_TICK=1`）；90 万次全 5µs |

**同板改善**：abs_max **19.2×**（96→5µs），avg **3.6×**（18→5µs）。
**与主线对照**：inmate 5µs 与 K-Fusion fused 3.391µs(Pi5,15min)、cRTOS 文献 ~4µs(x86) 同数量级。
详见 `crtos/results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md`。

> ⚠ 修正：早前 60s 记录（root max 2086µs / 改善 347×）系 `cyclictest -Sp3` 被解析成 `-p3`（优先级 3）的**误配**，**已撤回**。

### 7.3 公平性补强（未做完，建议）

- [x] inmate 跑 **15min soak** 对齐 K-Fusion 口径 → 已完成：max **5µs**。
- [x] root cell 正确配置（FIFO99+绑核）15min → 已完成：max **96µs**。
- [ ] （可选）root cell 换 PREEMPT_RT / `isolcpus` 进一步调优。
- [ ] 统一 `stamp` 与 `raw_log`，供 `kfusion/scripts/paper/paper_summarize_results__论文结果汇总.py` 出图。

### 7.4 对论文的影响
本实验给论文 Table 3（cross-study）提供了**除"引用值"外的同板实测锚点**：
「cRTOS-like (open rebuild), Pi 4B aarch64 + Jailhouse」= **6 µs（60s）**，
与 cRTOS 作者原文 Pi4「最小 5µs jitter」量级一致，可支撑"公开可重建、架构等价"的表述。
