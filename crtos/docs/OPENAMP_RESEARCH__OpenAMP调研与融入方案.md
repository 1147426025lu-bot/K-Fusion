# OpenAMP 调研与 K-Fusion 融入方案

> 调研日期：2026-10-19
> 结论先行：**能融入，而且比预想的省事**——NuttX 侧已有现成的 `RPMSG_VIRTIO_IVSHMEM`（不用写），
> Linux 侧需要自己补一个轻量 rpmsg 后端（因为 Jailhouse 的 virtio-over-ivshmem 仍是实验特性）。
> 最大价值不在"替换现有 mailbox"，而在**能量化「标准协议栈 vs 裸共享内存」的代价**——这是一个我们
> 目前恰好缺的对照实验。

---

## 1. OpenAMP 是什么

OpenAMP（Open Asymmetric Multi-Processing）是一套**标准化 AMP 协作组件**，不是单一框架。
官方 2025 Q1 材料列出的组成：

| 组件 | 作用 | 本项目相关性 |
|---|---|---|
| **remoteproc** | 远程处理器生命周期管理（加载固件、启动/停止） | ⚠️ **与 Jailhouse 冲突**，见 §5 |
| **RPMsg** | 基于端点（endpoint）的消息系统，含 name service | ✅ 核心目标 |
| **Virtio-msg** | virtio 传输，可复用 virtio-net/blk/vsock/console/fs/i2c/gpio/spi | ✅ 传输层 |
| **Libmetal** | 硬件抽象（寄存器、中断、内存） | ✅ 移植层 |
| **System Devicetree + Lopper** | 整机 AMP 资源配置 | ⬜ 非必需 |

**支持的运行环境**（官方原文列举）：Linux 用户态、Linux 内核、FreeRTOS / **NuttX** / Zephyr /
VxWorks、裸机、**hypervisor 之上的 OS**、**hypervisor 内部**。

**关键硬件前提**（官方原文）：只需要两样东西——
1. 双方都能访问的**共享内存**；
2. **核间中断**（可选：若 vring 的 flags 允许，可以完全不用中断、纯轮询）。

不需要核间信号量/队列/互斥锁——virtqueue 是**单写单读环形缓冲**，天然无锁。

---

## 2. 关键先例（这说明我们的路子已经被验证过）

| 先例 | 做法 | 对我们的意义 |
|---|---|---|
| **NXP Harpoon 2.5** | Jailhouse 硬分区 Linux + RTOS，**RPMsg-Lite 直接构建在 Jailhouse IVSHMEM 之上**（`pci_devices` 定义 ivshmem，两端映射同一块物理内存做零拷贝） | 商用产品级验证：**Jailhouse + RPMsg 是成熟组合** |
| **Xilinx/AMD Ultrascale+** | Jailhouse 分区后，RT 域内用 `ivshmem` 传大数据 + `RPMsg/virtio` 到 R5 传控制 | 双通道分工（大数据走裸共享内存，控制走 RPMsg）的范式 |
| **NuttX 本体** | `drivers/rpmsg/` 实现了 OpenAMP 框架，另有 NuttX 特有的 `RPTUN` 桥接层 | 我们不用移植，直接开配置 |
| **Jailhouse 官方文档** | 明确写有 *"work-in-progress support for transporting virtio over ivshmem"*（实验性） | Linux 侧有官方在研路径，但尚不稳定 |

---

## 3. 我们已有的资产与缺口

### 3.1 已经具备的（这决定了成本很低）

| 项 | 状态 | 位置 |
|---|---|---|
| Jailhouse IVSHMEM 通道 | ✅ **已实测双向打通**（2026-10-16） | `configs/arm64/rpi4-nuttx.c` 的 `pci_devices`（`shmem_dev_id=1, peers=3`） |
| root 侧访问 | ✅ `uio_ivshmem` 内建，`/dev/uio0` map2 = `0x1faf1000` | 已有 `ivshmem_probe__跨域共享内存.py` |
| NuttX 侧访问 | ✅ 同物理地址映射到相同 guest 虚拟地址 | 已有 `apps/examples/xshm`（自建 mailbox 协议） |
| NuttX 的 RPMsg 支持 | ✅ 内核自带，含 **`RPMSG_VIRTIO_IVSHMEM`** | 见 §3.2 |
| 跨域延迟基线 | ✅ 裸 ivshmem 实测：中位 **33–44 ns**、p99.9 ~55 µs、max ~999 µs | COMPARE §9.4 |

### 3.2 NuttX 侧：几乎零开发

`drivers/rpmsg/Kconfig` 里直接有（NuttX 12.9 / master 均确认）：

```
CONFIG_RPMSG_VIRTIO_LITE=y          # rpmsg virtio 传输（轻量）
CONFIG_RPMSG_VIRTIO_IVSHMEM=y       # 基于 pci ivshmem 的 rpmsg virtio 驱动（依赖 PCI_IVSHMEM）
CONFIG_RPMSG_PING=y                 # 自带 ping 通道，官方说明：用于获取 send/recv 速度与延迟
```

另有一整套 `drivers/rptun/`（NuttX 特有的 remoteproc 桥接）+ `rpmsg_port_{uart,spi}`（跨芯片传输）。

> 也就是说：**inmate 侧的 RPMsg 我们不用写代码，开三个 config 就能跑。**

### 3.3 Linux 侧：这是唯一的缺口

- Linux 的 rpmsg 是**总线抽象，不绑定传输**（virtio 只是最常见的一种；理论上可以是
  shared memory 直接映射、mailbox 中断、PCIe doorbell）。
  只要实现 `rpmsg_device_ops` + `rpmsg_endpoint_ops` 就能接入 rpmsg core。
- 但**上游没有 rpmsg-over-ivshmem 的现成后端**；Jailhouse 的 virtio-over-ivshmem 仍是实验状态。
- → 需要我们自己写一个**最小 rpmsg 后端**（基于 `/dev/uio0` + MSI-X 门铃），或者等 Jailhouse 的实验特性成熟。

### 3.4 本地实测核查（2026-10-19，Pi 上实际查证）

不看文档、直接查我们自己的树，结论比预期更好——但有三个**具体的待改项**：

**① 驱动源码确实在，且规模很小**

```
drivers/rpmsg/rpmsg_virtio_ivshmem.c   382 行
drivers/pci/pci_ivshmem.c              564 行
drivers/rpmsg/rpmsg_virtio_lite.c      （轻量 rpmsg）
```

**② 当前 `.config` 里 RPMSG 相关一个都没开**

`grep -E "^CONFIG_(RPMSG|PCI_IVSHMEM|RPTUN)" .config` 返回空 → 阶段 A 的第一步就是打开它们。

**③ ⚠️ cell 现在用的是 INTx，不是 MSI-X**

```c
{ /* IVSHMEM (与 root cell 00:00.0 对端) */
    .type              = JAILHOUSE_PCI_TYPE_IVSHMEM,
    .bdf               = 0 << 3,
    .bar_mask          = JAILHOUSE_IVSHMEM_BAR_MASK_INTX,   // ← 这里
    .shmem_regions_start = 0,
    .shmem_dev_id      = 1,
    .shmem_peers       = 3,
    .shmem_protocol    = JAILHOUSE_SHMEM_PROTO_UNDEFINED,
}
```

Jailhouse 官方文档明确：**ivshmem 用 MSI-X 做通知，每个虚拟设备只支持 1 个向量**；
而我们现在配的是 `BAR_MASK_INTX`（传统中断，Jailhouse 下是模拟的）。
RPMsg 靠 vring 门铃通知，**用 INTx 会明显更慢**。

→ 要走 RPMsg，需要改成：
```c
.bar_mask         = JAILHOUSE_IVSHMEM_BAR_MASK_MSIX,
.num_msix_vectors = 1,
```

**④ 多 peer 的区域布局也需要按协议补齐**

官方文档要求每个 peer 至少要有：**state table（1 页，只读）+ 共享读写区 + 每个 peer 的 output 区**，
且**这些区域在 guest 物理内存里必须连续**（只把首地址传给 guest）。
我们目前是单 region 的简化做法（`shmem_regions_start = 0`，仅供自建 mailbox 用）。
上 RPMsg 时需要按 `JAILHOUSE_SHMEM_PROTO_*` 的约定扩充。

> 所以阶段 A 的真实工作量不是"开配置"，而是**改 cell 配置（INTx→MSIX + 区域布局）+ 重编 NuttX**。
> 好在这两处改动都很局部，不涉及驱动代码。

---

## 4. 五个可融入点（按价值/成本排序）

### 融入点 1（★★★ 最推荐，成本最低）：量化「协议栈成本」——补一个我们现在没有的对照实验

**现状**：我们只有**裸 ivshmem** 的跨域延迟（`xlat` 测的 33–44 ns 中位）。

**补什么**：同一块板、同一 1 kHz、同样的采集方式，换成 **RPMsg over ivshmem** 再测一遍。

**能得到什么**：

```
协议栈成本 = RPMsg 延迟分布 − 裸 ivshmem 延迟分布
```

这条差值本身就是论文里一个**很硬的数字**——"标准化 IPC 要付多少代价"。
而且它直接对应一个工程决策：**周期数据走裸共享内存，控制/配置走 RPMsg**（这正是 Xilinx 的双通道做法）。

**怎么做**：NuttX 侧开 `CONFIG_RPMSG_PING`，它自带 send/recv 延迟统计；Linux 侧最小后端打通后
用同一套 `xlat_collect` 思路采集，出同款图。

> 价值：把"我们自建 mailbox"从"权宜之计"变成"有依据的选择"——因为有了对照数据。

### 融入点 2（★★☆）：把自建 mailbox 换成 RPMsg

**现状**：`xshm`/`xlat` 用的是自定义 32 位字布局（`ROOT_MAGIC` / `INMATE_SEQ` / `INMATE_ECHO` / `INMATE_STATE`），
**无分帧、无端点、无多路复用**，每加一个用途就要改布局。

**换成 RPMsg 的好处**：
- 端点（endpoint）天然多路复用：`plc-io`、`plc-cmd`、`plc-log` 各走一条，互不干扰；
- name service 支持动态发现；
- 社区驱动、可复用现成测试工具；
- 可移植到其他平台（不绑死 Jailhouse）。

**代价**：多一层解析，延迟上升（需用融入点 1 实测确认是否可接受）。

**建议**：**分通道迁移**——控制/日志先迁，周期数据保留裸共享内存。

### 融入点 3（★★☆）：OpenAMP 的 RPC / proxy 服务 —— 对标 cRTOS 的 remote syscall

OpenAMP 提供两个上层服务：
- **RPC service**：远程过程调用（server 在 remote，client 在 main）；
- **proxy service**：文件 IO 代理，让 remote 复用 main 的文件系统（无需在 remote 上做完整 FS）。

**这正好是 cRTOS 论文的核心机制**（"实时域通过 remote syscall 复用 GPOS 的富功能"）。

**价值**：可以论证——"cRTOS 自研的 remote syscall 能力，OpenAMP 有标准化替代（RPC/proxy）；
两者延迟我们可以实测对比"。这比单纯说"我们也做了跨域调用"要强。

**注意**：proxy 走文件 IO，延迟是 µs–ms 级，**绝不能进实时路径**，只能做配置加载/日志落盘。

### 融入点 4（★★☆）：`plc_abi` 的第五套后端

`plc_abi__运行时ABI.h` 已经是可插拔运行时抽象（现有 POSIX 桩 / Linux 内核桩 / 计划中的裸机桩）。
再加一套 **RPMsg 桩**：

| `plc_*` | RPMsg 后端 |
|---|---|
| `plc_printk` / `plc_puts` | 发到 `plc-log` 端点 → Linux 侧打印 |
| `plc_kmalloc` | 静态池（不变） |
| `plc_ktime_get_ts` | 本地计数器（不变，**绝不走 RPMsg**） |

**意义**：让编译器内核化产物在 inmate 里用**标准 IPC** 与 Linux 通信，而不是私有协议；
这也让"同一份 fused 产物跨域部署"的故事更完整（第四种运行域后端）。

> ⚠️ **铁律**：RPMsg 只承载非实时的控制/日志/批量结果。**测量路径和周期数据一律不走它**，
> 否则协议栈延迟会直接污染抖动数据——这是我们整个项目最敏感的地方。

### 融入点 5（★☆☆，认知层面）：明确**不要用 remoteproc**

OpenAMP 的 `remoteproc` 假设 master 负责远程处理器的**加载、启动、复位**（Linux remoteproc 框架）。
但在 Jailhouse 里，cell 的生命周期由 `jailhouse cell create/load/start/shutdown` 管理。

**两套 LCM 会打架**。正确做法：**只用 OpenAMP 的 IPC 层（RPMsg/virtio），不用 remoteproc。**
NuttX 的 `RPMSG_VIRTIO_IVSHMEM` 恰好就是纯 IPC（不含 remoteproc），NXP Harpoon 用 RPMsg-Lite 同理。

这条写进方案能省掉一个常见的坑：很多人一开始就想"用 remoteproc 管 cell"，然后发现和 hypervisor 冲突。

---

## 5. 落地路径（按风险递增，每阶段独立可停）

| 阶段 | 内容 | 工期 | 风险 | 产出 |
|---|---|---|---|---|
| **A** | 改 cell 配置（**INTx→MSI-X** + 按协议补齐 ivshmem 区域）+ 开 `RPMSG_VIRTIO_LITE` / `RPMSG_VIRTIO_IVSHMEM` / `RPMSG_PING` + 重编 NuttX | 1 天 | 中（cell 配置改错会起不来） | inmate 侧 rpmsg 可用；ping 延迟初值 |
| **B** | 写 Linux 侧最小 rpmsg 后端（`uio_ivshmem` + MSI-X 门铃），打通 NuttX↔Linux ping | 1–2 天 | 中（新代码） | 双向通路 |
| **C** | A/B 对照实验：裸 ivshmem vs RPMsg，同 1 kHz、同采集口径 | 1 天 | 低 | **协议栈成本**（新论文数据） |
| **D**（可选） | `plc_abi` 第五后端 + 控制/日志通道迁移 | 2–3 天 | 中 | 标准化 IPC 通路 |

**建议先做 A + C 的 NuttX 内部部分**：即使 Linux 后端没写完，`RPMSG_PING` 也能先给出
"协议栈在 A72 上的开销量级"，用来判断这条路值不值得继续。

> 阶段 A 的**验收标准**（可判定，不模糊）：
> `jailhouse cell list` 正常、NuttX 起来后 `dmesg`/串口出现 rpmsg virtio 初始化日志、
> `rpmsg ping` 能打印出 send/recv 延迟数字。三者缺一即失败，回退 cell 配置即可。

---

## 6. 风险与限制（如实列出）

| 风险 | 说明 | 缓解 |
|---|---|---|
| **Linux 侧无现成驱动** | 上游没有 rpmsg-over-ivshmem；Jailhouse 的 virtio-over-ivshmem 是实验特性 | 自己写最小后端（rpmsg 是总线抽象，门槛比想象低）；或先只做 NuttX↔NuttX |
| **延迟可能显著上升** | RPMsg-Lite 在 Cortex-M0+ 上实测 89 µs（OpenAMP 152 µs）；A72 1.5 GHz 会小很多，但未知 | 融入点 1 就是为了先量出这个数，再决定 |
| **Jailhouse 的 MSI-X 只有单向量** | 官方文档：每个虚拟设备只支持 1 个中断 | 够用（vring 只需要一个门铃） |
| **非确定性** | name service、动态端点、动态内存会引入抖动 | 用静态 API + 静态端点（RPMsg-Lite 的 `RL_USE_STATIC_API`） |
| **不要把 RPMsg 塞进实时路径** | 协议栈开销会污染抖动测量 | 双通道：裸共享内存跑周期数据，RPMsg 跑控制/日志 |

---

## 7. 与本项目现有工作的关系（论文口径）

| 对象 | 关系 |
|---|---|
| **cRTOS (Access'25)** | 它自研 remote syscall，**不用 OpenAMP**；我们用 OpenAMP 的 RPC/proxy 作为标准化对照，并给出延迟实测 |
| **NXP Harpoon 2.5** | 同样是 Jailhouse + RPMsg，但它是**商用产品文档、无公开延迟数据**；我们补的是可复现的定量数据 |
| **我们自建的 xshm/xlat** | 不是"要被替换的临时方案"，而是**对照组**——因为有了它，才能算出协议栈成本 |
| **K-Fusion 编译器内核化** | RPMsg 作为 `plc_abi` 的第五种后端，让"同一产物跨域部署"从 3 域扩到 4 域（含标准 IPC 域） |

**一句话定位**：别人做"Jailhouse 上跑 RPMsg"（能通），我们做"**Jailhouse 上 RPMsg 与裸共享内存的
代价差异，以及它对实时任务抖动的影响**"（有数）。

---

## 8. 引用来源

1. OpenAMP 官方 *Elevator Pitch 2025 Q1* — 组件、支持环境、IVSHMEM 作为 AMP 硬件示例
2. OpenAMP *Project Overview*（readthedocs v2025.10.0）— 架构与 hypervisor 关系
3. OpenAMP *Introduction to OpenAMP Library* v1.1a — RPMsg 消息格式、channel/endpoint、remoteproc/resource table
4. NuttX `drivers/rpmsg/Kconfig`（nuttx-12.9.0-RC0 / master）— `RPMSG_VIRTIO_IVSHMEM`、`RPMSG_PING`
5. Apache NuttX 邮件列表（Xiang Xiao, 2023-07）— RPTUN 是 NuttX 特有桥接层、Linux 侧需自写 rpmsg 驱动
6. Jailhouse `Documentation/inter-cell-communication.md` — ivshmem 单 MSI 向量、virtio-over-ivshmem 为 WIP
7. NXP *MCUXpresso SDK / RPMsg-Lite* 文档 — Lite vs OpenAMP 的体积与延迟对比（5547 B/152 µs vs 2926 B/89 µs，Cortex-M0+）
8. NXP Harpoon 2.5 中文解析 — Jailhouse + RPMsg-Lite 构建于 IVSHMEM 之上
9. Xilinx *High-Performance Real-Time Linux Solution for Ultrascale+* — ivshmem 大数据 + RPMsg 控制的双通道
10. rpmsg core 分析 — rpmsg 是总线抽象、可自定义后端（shared memory / mailbox / PCIe doorbell）
