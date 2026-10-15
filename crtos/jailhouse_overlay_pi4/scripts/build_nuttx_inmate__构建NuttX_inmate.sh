#!/bin/bash
# build_nuttx_inmate__构建NuttX_inmate.sh — 在 Pi4 上构建 NuttX inmate（nsh-inmate）
# 运行前提（由 WorkBuddy 侧上传）:
#   /root/toolchain.tar.xz        Arm GNU 13.2.Rel1 aarch64-aarch64-none-elf
#   /root/nuttx.tar.gz            apache/nuttx master 源码包
#   /root/apps.tar.gz             apache/nuttx-apps master 源码包
#   /root/nsh-inmate-defconfig    inmate 专用 defconfig
# 产物: /root/nuttx-build/nuttx/nuttx.bin
# 注意: 工具链解压约需 1.5GB 磁盘；确认 rootfs 有足够空间。
set -euo pipefail

TC_TAR="/root/toolchain.tar.xz"
NUTTX_TAR="/root/nuttx.tar.gz"
APPS_TAR="/root/apps.tar.gz"
DEFCONFIG_SRC="/root/nsh-inmate-defconfig"
BUILD="/root/nuttx-build"

echo "== 0. 前提检查 =="
for f in "$TC_TAR" "$NUTTX_TAR" "$APPS_TAR" "$DEFCONFIG_SRC"; do
  [ -f "$f" ] || { echo "FAIL 缺文件: $f" >&2; exit 1; }
done
df -h / | tail -1

echo "== 1. 解压工具链 =="
mkdir -p /opt/arm
[ -x /opt/arm/arm-gnu-toolchain-13.2.rel1-aarch64-aarch64-none-elf/bin/aarch64-none-elf-gcc ] || \
  tar -xJf "$TC_TAR" -C /opt/arm
TC_BIN=$(ls -d /opt/arm/arm-gnu-toolchain-*-aarch64-none-elf/bin)
export PATH="$TC_BIN:$PATH"
aarch64-none-elf-gcc --version | head -1

echo "== 2. 解压 NuttX 源码 =="
mkdir -p "$BUILD"
if [ ! -d "$BUILD/nuttx" ]; then
  tar -xzf "$NUTTX_TAR" -C "$BUILD"
  mv "$BUILD"/nuttx-* "$BUILD/nuttx"
fi
if [ ! -d "$BUILD/apps" ]; then
  tar -xzf "$APPS_TAR" -C "$BUILD"
  mv "$BUILD"/nuttx-apps-* "$BUILD/apps"
fi

echo "== 3. 注入 nsh-inmate defconfig =="
CFGDIR="$BUILD/nuttx/boards/arm64/bcm2711/raspberrypi-4b/configs/nsh-inmate"
mkdir -p "$CFGDIR"
cp "$DEFCONFIG_SRC" "$CFGDIR/defconfig"

echo "== 4. configure =="
cd "$BUILD/nuttx"
tools/configure.sh raspberrypi-4b:nsh-inmate

echo "== 5. make -j4 =="
make -j4 2>&1 | tail -25

echo "== 6. 产物 =="
ls -la nuttx.bin nuttx 2>/dev/null
echo "完成。下一步(WorkBuddy 侧):
  pscp nuttx.bin → Pi
  jailhouse cell create /etc/jailhouse/rpi4-nuttx.cell
  jailhouse cell load rpi4-nuttx nuttx.bin -a 0x10480000
  jailhouse cell start rpi4-nuttx
  timeout 5 jailhouse console"
