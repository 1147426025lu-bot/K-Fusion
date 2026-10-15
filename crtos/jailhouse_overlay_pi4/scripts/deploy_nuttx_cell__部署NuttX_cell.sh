#!/bin/bash
# deploy_nuttx_cell__部署NuttX_cell.sh — 在 Raspberry Pi 4 上把 NuttX inmate 装入 Jailhouse cell 并启动
#
# 前置条件：
#   - Arm GNU Toolchain 装在 /opt/arm/arm-gnu-toolchain-*-aarch64-none-elf（build 脚本负责）
#   - jailhouse 已 enable、root cell 已激活（jailhouse cell list 看到 Raspberry-Pi4 running）
#   - /root/jh 是"与镜像同版本"的 jailhouse 头文件（d6e64f90，v0.12 + 342 提交），用于编译 cell
#   - 已执行过 build_nuttx_inmate（存在 /root/nuttx-build/nuttx/nuttx.bin）
#
# 关键坑（均已验证/已修）：
#   1) CONFIG_RAM_SIZE 在 Kconfig 里是 int 类型，必须写十进制！128MB = 134217728。
#      写成 0x08000000 会被 int 解析器当成 0 → 堆为 0 → NSH 起不来（inmate 只是 idle 空转、无 fault，极难察觉）。
#   2) 内核链接地址 0x480000（dramboot.ld 固定）。加载用 guest 虚拟地址：
#        jailhouse cell load rpi4-nuttx nuttx.bin -a 0x480000
#      填 phys(0x10480000) 在本镜像的 jailhouse fork 里会报 EINVAL。
#   3) cell 的 cpu_reset_address 必须是 0x480000，否则 CPU 从 guest 0 启动、跑的不是 NuttX。
#   4) objcopy 提取的段名是 .data.config（不是 .config）——用错会得到 0 字节 cell。
#   5) arm64_head.S 的 mpidr 检查必须打补丁（apply_patches 负责），否则 CPU1 上跳 wfe 死循环。
#   6) inmate 输出走物理 mini-UART → 只看 GPIO 14/15 的 USB-TTL(115200 8N1)；
#      `jailhouse console` 抓的是 hypervisor 虚拟调试台，看不到 inmate 物理串口输出。
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/root}"
NUTTX="${NUTTX_SRC:-$ROOT_DIR/nuttx-build/nuttx}"
JH_HDR="${JH_HDR:-$ROOT_DIR/jh}"                 # 与镜像同版本(d6e64f90)的 jailhouse 头文件
CELL_SRC="${CELL_SRC:-$ROOT_DIR/rpi4-nuttx.c}"
CELL_BIN="${CELL_BIN:-$ROOT_DIR/rpi4-nuttx.cell}"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_OVERLAY="${REPO_OVERLAY:-$(cd "$SCRIPTS_DIR/.." && pwd)}"

# 工具链（tarball 内目录名大小写不固定，用通配避免踩坑）
TC_BIN="$(ls -d /opt/arm/arm-gnu-toolchain-*-aarch64-none-elf/bin 2>/dev/null | head -1 || true)"
export PATH="${TC_BIN:+$TC_BIN:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export LANG=C.UTF-8 LC_ALL=C.UTF-8

echo "== 0. 检查 =="
[ -f "$NUTTX/nuttx.bin" ] || { echo "FAIL: 缺 $NUTTX/nuttx.bin（先跑 build_nuttx_inmate）" >&2; exit 1; }
[ -d "$JH_HDR/include/jailhouse" ] || { echo "FAIL: 缺 jailhouse 头文件 $JH_HDR/include/jailhouse" >&2; exit 1; }
[ -f "$CELL_SRC" ] || { echo "FAIL: 缺 cell 源 $CELL_SRC" >&2; exit 1; }

echo "== 1. 确保 arm64_head.S 补丁已应用（幂等）=="
bash "$REPO_OVERLAY/patches/apply_patches__应用补丁.sh" "$NUTTX"

echo "== 2. 编译 cell =="
cd "$ROOT_DIR"
gcc -c "$CELL_SRC" -I "$JH_HDR/include" -I "$JH_HDR/hypervisor/include" \
    -ffunction-sections -fdata-sections -o rpi4-nuttx.o
# 段名是 .data.config（不是 .config）
objcopy -O binary -j .data.config rpi4-nuttx.o "$CELL_BIN"
SZ=$(stat -c%s "$CELL_BIN" 2>/dev/null || echo 0)
echo "cell 大小: ${SZ} bytes"
[ "$SZ" -gt 0 ] || { echo "FAIL: cell 为 0 字节（objcopy 段名错？）" >&2; exit 1; }

echo "== 3. create / load / start =="
jailhouse cell list | grep -q rpi4-nuttx && jailhouse cell destroy rpi4-nuttx || true
jailhouse cell create "$CELL_BIN"
# -a 是 guest 虚拟地址；入口点由 cell 的 cpu_reset_address(=0x480000) 决定，与 -a 无关
jailhouse cell load rpi4-nuttx "$NUTTX/nuttx.bin" -a 0x480000
jailhouse cell start rpi4-nuttx
sleep 1
jailhouse cell list
echo "DONE. 接 GPIO 14/15 串口线(115200 8N1)即可看到 NuttX NSH 提示符 nsh>。"
