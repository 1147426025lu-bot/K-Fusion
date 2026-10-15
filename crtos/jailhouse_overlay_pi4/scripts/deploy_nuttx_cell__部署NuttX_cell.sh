#!/bin/bash
# deploy_nuttx_cell.sh — 在 Raspberry Pi 4 上构建 NuttX inmate 并装入 Jailhouse cell 启动
#
# 前置条件：
#   - Arm GNU Toolchain 13.2.Rel1 装在 /opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf
#   - kconfig-frontends / libncurses 已装（老版本 NuttX 需要，新版用内建 kconfig）
#   - jailhouse 已启用、root cell 已激活（jailhouse cell list 看到 Raspberry-Pi4 running）
#   - /root/jh 是"与镜像同版本"的 jailhouse 头文件（d6e64f90，v0.12 + 342 提交），用于编译 cell
#
# 关键坑（已验证）：
#   1) CONFIG_RAM_SIZE 在 Kconfig 里是 int 类型，必须写十进制！
#      128MB = 134217728。写成 0x08000000 会被 int 解析器当成 0 → 堆为 0 → NSH 起不来。
#      （之前就栽在这，inmate 只是 idle 空转，且无 fault，极难察觉）
#   2) nuttx.bin 内核链接地址是 0x480000（dramboot.ld 固定），cell RAM 在 guest 虚拟 0。
#      所以加载顺序：先 dd 一个 4.5MB(pad, 0x480000) 的零填充，再加载 nuttx.bin，
#      让它正好落在 guest 0x480000(phys 0x10480000)。
#      `jailhouse cell load ... -a 0x10480000` 在本镜像的 jailhouse fork 里会报 EINVAL，别用。
#   3) cell 把 mini-UART(0xfe215040, ROOTSHARED) 分配给 inmate；NuttX 用 bcm2711_serial.c
#      的 mini-UART 驱动（CONSOLE_DEV 宏无条件注册 /dev/console），与物理串口线(GPIO 14/15)对应。
#      CONFIG_SERIAL_CONSOLE 这个符号在本 NuttX 版本不存在，但控制台仍由驱动注册，无关紧要。
#   4) inmate 输出走物理 mini-UART → 只看接在 GPIO 14/15 的 USB-TTL 线(115200 8N1)。
#      `jailhouse console` 抓的是 hypervisor 虚拟调试台，看不到 inmate 的物理串口输出。
#
set -euo pipefail
export PATH=/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8 LC_ALL=C.UTF-8

NUTTX=/root/nuttx-build/nuttx
JH_HDR=/root/jh                       # 与镜像同版本(d6e64f90)的 jailhouse 头文件
DEF=/root/rpi4-nuttx-nsh-inmate.defconfig
CELL_SRC=/root/rpi4-nuttx.c

cd "$NUTTX"

# 1) 用硬化 defconfig 重新生成 .config
cp "$DEF" boards/arm64/bcm2711/raspberrypi-4b/configs/nsh-inmate/defconfig
cp "$DEF" .config
make olddefconfig
grep -E 'CONFIG_RAM_SIZE|CONFIG_RAM_START|CONFIG_BOARD_LATE_INITIALIZE' .config

# 2) 构建 nuttx.bin
make -j4
ls -la nuttx.bin

# 3) 编译 cell（基于 inmate-demo：mini-UART + 128MB RAM，CPU1）
cd /root
gcc -c "$CELL_SRC" -I "$JH_HDR/include" -I "$JH_HDR/hypervisor/include" \
    -ffunction-sections -fdata-sections -o rpi4-nuttx.o
objcopy -O binary -j .config rpi4-nuttx.o rpi4-nuttx.cell
ls -la rpi4-nuttx.cell

# 4) 销毁旧 cell（若存在）→ 重建 → 加载 → 启动
jailhouse cell list | grep -q rpi4-nuttx && jailhouse cell destroy rpi4-nuttx
jailhouse cell create /root/rpi4-nuttx.cell
# 用 -a 指定 guest 虚拟地址 0x480000（NuttX 链接地址 dramboot.ld=.0x480000）。
# 注意：-a 要填 guest 虚拟地址；填 phys(0x10480000) 会 EINVAL。
# 入口点由 cell 配置的 cpu_reset_address=0x480000 决定（不是 -a）。
jailhouse cell load rpi4-nuttx "$NUTTX/nuttx.bin" -a 0x480000
jailhouse cell start rpi4-nuttx
jailhouse cell list
echo "DONE. 接 GPIO 14/15 串口线(115200 8N1)即可看到 NuttX NSH 提示符。"
