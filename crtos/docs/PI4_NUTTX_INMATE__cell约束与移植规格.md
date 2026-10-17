# Pi4 Jailhouse cell 约束分析（NuttX inmate 移植规格与实测结果）

> 来源：siemens/jailhouse `configs/arm64/rpi4-inmate-demo.c`（与镜像内二进制 .cell 对应，Jailhouse v0.12, 342-gd6e64f90）。
> **状态：规格已落地实现并实测通过（2026-10-15）。** NuttX 作为非根 cell 在 CPU1 完整启动 NSH 并可交互；实时抖动实测见 §6。
> 产物：`crtos/jailhouse_overlay_pi4/`（cell 配置 / defconfig / 补丁 / 部署脚本 / rtlat app）。

## 1. 镜像自带 inmate-demo cell（实测验证过可运行）

| 项 | 值 |
|---|---|
| cell 名 | `inmate-demo`，flags = `JAILHOUSE_CELL_PASSIVE_COMMREG` |
| CPU | `0b0010`（仅 CPU1） |
| RAM | phys `0x1fa00000` → **guest 虚拟地址 0**，大小 **64 KB**（READ\|WRITE\|EXEC\|LOADABLE） |
| mini-UART | phys `0xfe215040`，64B，MEM_IO（已分配给 inmate；console 声明同地址，8250 型，regdist 4） |
| IVSHMEM | PCI 设备（shmem_dev_id=1, peers=3），共享区 `0x1faf0000`~`0x1fb00000`（约 64KB，ROOTSHARED） |
| GIC | irqchip `0xff841000`，pin_base 160，vpci_irq_base 184（SPI） |
| 通信区 | guest 虚拟 `0x80000000`，4KB，COMM_REGION |

## 2. NuttX inmate 实测 cell（rpi4-nuttx.c，已运行）

实测可用的 cell 配置（与 §1 demo 的**三处关键差异**已用 ⚠ 标出）：

```
CPU        : 0b0010（CPU1，与 RT 测量核一致）
cpu_reset_address : 0x480000  ⚠ 必设！jailhouse 用它设首个 CPU 的启动地址。
                               默认 0 → CPU 从 guest 0 启动、跑的不是 NuttX（静默挂死）。
                               CLI 的 -a 只改镜像加载地址，不改入口点。
RAM        : phys 0x10000000 → guest virt 0，size 0x08000000 (128MB，取自 DT 预留池)
             flags = READ|WRITE|EXEC|DMA|LOADABLE
mini-UART  : 0xfe215000, 0x100  ⚠ 必须覆盖整个 AUX 块（不能只映射数据寄存器 0xfe215040）。
             NuttX 驱动会写 AUX_ENABLES(0xfe215004)、读 AUX_IRQ(0xfe215000) → 越界即 data abort。
GPIO       : 0xfe200000, 0x1000, MEM_IO|ROOTSHARED  ⚠ NuttX 串口初始化会配 GPIO14/15 ALT5
系统定时器  : 0xfe003000, 0x1000, MEM_IO|ROOTSHARED  ⚠ oneshot 用 BCM 系统定时器(非 arch timer)
GIC        : 0xff841000, pin_base 160 → 放行 SPI 184（vpci）
             + 第二个 irqchip pin_base 96 → 放行 SPI 125  ⚠ mini-UART IRQ(BCM_IRQ_VC_AUX=96+29)
               NuttX 控制台是中断驱动，不放行则横幅只进缓冲、永不发出。
IVSHMEM    : 0x1faf0000 起（与 demo 一致；remote-syscall 预留）
console    : 声明 mini-UART 0xfe215040
```

加载方式（**实测可用**）：
```
jailhouse cell load rpi4-nuttx nuttx.bin -a 0x480000
```
- `-a` 是 **guest 虚拟地址**（不是物理地址）。填 phys `0x10480000` 在该 fork 上报 **EINVAL**。
- 旧文档里"先 `dd` 4.5MB pad.bin 再 load"的技巧**已废弃**（入口点由 `cpu_reset_address` 决定后无需填充）。

## 3. NuttX 侧实际改动清单（raspberrypi-4b → inmate，已落地）

1. **链接地址**：保持 `CONFIG_LOAD_BASE 0x480000`（dramboot.ld 写死），无需改。
2. **启动路径解耦（关键补丁）**：`arch/arm64/src/common/arm64_head.S` 非 SMP 分支的
   `get_cpu_id x1; cmp x1,#0; bne fail` 会把物理核≠0 的 inmate 打进 `wfe; b fail` 死循环
   → 改为 `nop`（见 `patches/arm64_head.S.jailhouse-inmate.patched`）。
3. **串口**：确认走 mini-UART（`bcm2711_serial.c` 的 `g_miniuartport`），非 PL011。
4. **中断**：GICv2 分发器由 hypervisor 管；inmate 侧仅需 cell 放行 SPI 125（UART）+ 184（vpci）。
5. **定时器**：NuttX oneshot 用 BCM 系统定时器 MMIO（`0xfe003000`）——已映射。
6. **MMU**：NuttX 既有 `DEVICE_REGION [0xfc000000, 0x100000000)` 覆盖 mini-UART，无需改。
7. **defconfig 关键项**：`RAM_SIZE=134217728`（十进制！int 类型）、关 `BOARD_LATE_INITIALIZE`、
   关 `ARCH_LEDS`、`SCHED_TICKLESS=y`、`USEC_PER_TICK=1`。
8. **remote syscall**：IVSHMEM 已由 cell 分配，NuttX 侧驱动 + root 侧守护进程待做（下一步）。

## 4. 工具链与构建

- 官方要求：Arm GNU Toolchain **aarch64-none-elf**（13.2.Rel1）—— Pi 本机构建通过。
- 构建脚本：`crtos/jailhouse_overlay_pi4/scripts/build_nuttx_inmate__构建NuttX_inmate.sh`。
- 部署脚本：`crtos/jailhouse_overlay_pi4/scripts/deploy_nuttx_cell__部署NuttX_cell.sh`（一键 create/load/start）。
- 产物 `nuttx.bin`（≈360KB）→ `cell load -a 0x480000`。
- 参考：上游移植日志 https://nuttx.apache.org/docs/latest/guides/porting/case-studies/bcm2711-rpi4b.html

## 5. 与 cRTOS 的对齐度说明（更新后）

| cRTOS 构成 | 本方案状态 |
|---|---|
| Jailhouse 分区 | ✅ 已验证（同一 hypervisor） |
| Pi 4B | ✅ 同板 |
| NuttX sRTOS | ✅ **已移植并启动 NSH**（无公开先例，本工作首创） |
| 周期抖动指标 | ✅ **已实测**（见 §6，协议与 PLCFusion 对齐） |
| remote syscall（shadow process + 共享内存） | 🔧 IVSHMEM 已预留；NuttX 侧驱动待做 |

## 6. 实施结果（2026-10-15 实测）

### 6.1 里程碑
NuttX 作为 Jailhouse **非根 cell** 在 Raspberry Pi 4B（CPU1）完整启动，串口输出：
```
NuttShell (NSH)
nsh> help
Builtin Apps: dd getprime hello nsh ostest sh
nsh>
```
命令回显、help 列表、提示符全部正常 → **TX/RX 双向控制台工作**。

### 6.2 一路踩的 6 个坑（缺一不可）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | 静默无输出 | `CONFIG_RAM_SIZE` 是 int，`0x08000000` 被解析成 **0** → 堆为 0 | 改十进制 `134217728` |
| 2 | 仍无输出、无 fault | 驱动写 **AUX_ENABLES(0xfe215004)**，cell 只映射了 `0xfe215040+0x40` → 越界 | UART 区扩到整个 AUX 块 `0xfe215000+0x100` |
| 3 | 静默卡死 | `arm64_head.S` 非 SMP 分支 `cmp x1,#0; bne fail` → inmate 在物理核 1 上跳 WFE 死循环 | 补丁：`bne fail` → `nop` |
| 4 | CPU 跑的不是 NuttX | cell 缺 **`cpu_reset_address`**，默认 0 → 从 guest 0 启动 | 设 `.cpu_reset_address = 0x480000` |
| 5 | FATAL trap @ 0xfe00b8b8 | `CONFIG_ARCH_LEDS` 让板级初始化走 **VideoCore 邮箱**（cell 未映射） | 关闭 `CONFIG_ARCH_LEDS` |
| 6 | NSH 横幅只进缓冲不发 | 串口**中断驱动**，cell 未放行 **UART IRQ (SPI 125)** | cell 第二个 irqchip 放行 SPI 125 |

### 6.3 实时抖动实测（与 PLCFusion 协议对齐）

工具：自写 `rtlat`（`apps/benchmarks/rtlat`，cyclictest 内核等价：SCHED_FIFO 高优先级 + 周期
`clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME)` + `clock_gettime` 量唤醒偏差）。
配置：`CONFIG_SCHED_TICKLESS=y` + `CONFIG_USEC_PER_TICK=1`，间隔 1ms。

| realm | 平台 | 时长 | avg | **abs_max** | >200µs |
|---|---|---|---|---|---|
| **NuttX inmate**（cRTOS 域） | Pi 4B | **15 min** | **5 µs** | **5 µs** | 0 |
| root cell Linux（同板，正确配置） | Pi 4B | **15 min** | 18 µs | **96 µs** | 0 |

直方图（inmate 15 min）：`5: 900000`（60 s 旧跑为 `5:59999, 6:1`）。

> ⚠ **该直方图不可解读为"零抖动"**：早期 `rtlat` 用 `lat / 1000` 把延迟**整数截断到 µs**，
> 所以 `5: 900000` 只表示 90 万个样本都落在 `[5, 6) µs`，**分辨率上限就是 1 µs**。
> 2026-10-16 已把工具改为 **ns 输出 + 保留带符号值 + 全样本直方图 + 逐样本导出**，
> 复测结果（6 万样本、桶宽 100 ns、实测时钟粒度 **55 ns**）：
> **稳态 ≤ 5.26 µs**（p50 4.87 / p99.9 4.94 µs），另有**一次可复现的启动瞬态 ≈ 52.4 µs（第 4–5 次唤醒）**。
> 详见 `../results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md` §6 与图 C/D。

**同板改善**：abs_max **19.2×**（96→5µs）、avg **3.6×**（18→5µs）。

> ⚠ 早前记的「root 60s avg 71µs / max 2086µs、改善 347×」系 `cyclictest -Sp3` 被解析成
> `-p3`（优先级 3）的**误配**结果，**已撤回**。

### 6.4 与主线 PLCFusion / 文献对照（cross-study）

| 系统 | 平台 | 机制 | 时长 | abs_max | 来源 |
|---|---|---|---|---|---|
| K-Fusion 融合 | Pi 5 | 单域 PREEMPT_RT，无 hypervisor | 15 min | 3.391 µs | 仓库实测(fig3) |
| **NuttX inmate（cRTOS-like）** | **Pi 4B** | **Jailhouse 分区 + NuttX tickless** | **15 min** | **5 µs** | **本工作实测** |
| root cell Linux | Pi 4B | 同板 Linux（非 RT） | 15 min | 96 µs | 本工作实测 |
| cRTOS | x86-64 | Jailhouse + sRTOS | — | ~4 µs | IEEE Access 2025（引用） |

⚠ 两条硬约束（写入 Threats to Validity）：**板子不同**（Pi4 GIC-400 vs Pi5 GIC-600）、
**机制不同**（hypervisor 硬分区 vs 单域）。时长已对齐（均为 15 min）✓。

详细对照见 `crtos/results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md`；
schema 对齐的测量数据见 `crtos/results/pi4_crtos_soak__Pi4对比测量.json`。

## 7. 已知遗留项

1. NuttX cell 运行时会让 root cell 网络掉线（destroy 后立即恢复）——疑似 inmate 无 tick 时空转/共享外设压力。调试期按需 destroy。
2. mini-UART 为 ROOTSHARED 且 inmate 抢了 SPI 125 → root cell 串口控制台会受影响（SSH 不受影响）。若要"干净"，可给 NuttX 打补丁把控制台改轮询，或改走 hypervisor 虚拟控制台。
3. IVSHMEM remote-syscall 通道（NuttX 侧驱动 + root 侧守护）尚未实现。
