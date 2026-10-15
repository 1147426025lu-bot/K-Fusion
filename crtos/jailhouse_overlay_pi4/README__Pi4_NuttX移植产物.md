# Pi4 NuttX-inmate 移植产物（jailhouse_overlay_pi4）

> NuttX 作为 Jailhouse **非根 cell** 在 Raspberry Pi 4B 上运行的完整产物（2026-10-15 实测通过）。
> 详细规格/踩坑/结果：`../docs/PI4_NUTTX_INMATE__cell约束与移植规格.md`
> 对照数据：`../results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md`

## 目录

| 路径 | 说明 |
|------|------|
| `configs/arm64/rpi4-nuttx.c` | Jailhouse cell 配置（128MB@guest0、AUX整块、GPIO、系统定时器、SPI125、`cpu_reset_address=0x480000`） |
| `configs/arm64/rpi4-nuttx-nsh-inmate.defconfig` | NuttX inmate defconfig（RAM_SIZE 十进制、关 ARCH_LEDS/BOARD_LATE_INIT、tickless、USEC_PER_TICK=1、rtlat） |
| `patches/arm64_head.S.jailhouse-inmate.patched` | 打好的 head.S（mpidr 检查→nop），供 `apply_patches` 覆盖 |
| `patches/apply_patches__应用补丁.sh` | 幂等应用补丁到 NuttX 树 |
| `scripts/build_nuttx_inmate__构建NuttX_inmate.sh` | Pi 上构建 NuttX inmate → `nuttx.bin` |
| `scripts/deploy_nuttx_cell__部署NuttX_cell.sh` | 编译 cell + create/load/start |
| `scripts/reproduce_pi4_crtos__一键复现.sh` | **一键**：检查 → 构建 → 部署 → 提示测量 |
| `nuttx/apps/benchmarks/rtlat/` | 自写实时抖动 app（cyclictest 内核等价） |

## 一键复现（Pi 上，root）

```bash
# 前置：工具链/源码/defconfig/cell 源/jailhouse 头文件已 pscp 到 /root（见 build 脚本头）
sudo bash scripts/reproduce_pi4_crtos__一键复现.sh --root-soak 1
```

## 三个必须记住的坑
1. `CONFIG_RAM_SIZE` 是 **int**，必须十进制 `134217728`（写 `0x08000000` → 0 → NSH 起不来）。
2. `objcopy` 段名是 **`.data.config`**（不是 `.config`，否则 0 字节 cell）。
3. `arm64_head.S` 的 mpidr 检查必须 nop（否则 CPU1 上 wfe 死循环）——`apply_patches` 负责。

串口观测：接 **GPIO 14/15** 的 USB-TTL，**115200 8N1**；`jailhouse console` 看不到 inmate 输出。
