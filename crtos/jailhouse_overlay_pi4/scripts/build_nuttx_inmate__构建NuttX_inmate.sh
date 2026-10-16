#!/bin/bash
# build_nuttx_inmate__构建NuttX_inmate.sh — 在 Pi4 上构建 NuttX inmate（nsh-inmate）
#
# 运行前提（需先由 PC 侧 pscp 上传到 Pi）:
#   /root/toolchain.tar.xz                    Arm GNU 13.2.Rel1 aarch64-aarch64-none-elf
#   /root/nuttx.tar.gz                        apache/nuttx master 源码包
#   /root/apps.tar.gz                         apache/nuttx-apps master 源码包
#   /root/rpi4-nuttx-nsh-inmate.defconfig     inmate 专用 defconfig（仓库 configs/arm64/）
#   本仓库 patches/ 目录（arm64_head.S.jailhouse-inmate.patched）
#
# 产物: /root/nuttx-build/nuttx/nuttx.bin
# 磁盘: 工具链解压约需 1.5GB；确认 rootfs 有足够空间。
#
# 可覆盖的环境变量:
#   ROOT_DIR(默认 /root)  BUILD(默认 $ROOT_DIR/nuttx-build)
#   DEFCONFIG_SRC / TC_TAR / NUTTX_TAR / APPS_TAR
#   REPO_OVERLAY(默认脚本上级目录，即 jailhouse_overlay_pi4)
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/root}"
TC_TAR="${TC_TAR:-$ROOT_DIR/toolchain.tar.xz}"
NUTTX_TAR="${NUTTX_TAR:-$ROOT_DIR/nuttx.tar.gz}"
APPS_TAR="${APPS_TAR:-$ROOT_DIR/apps.tar.gz}"
DEFCONFIG_SRC="${DEFCONFIG_SRC:-$ROOT_DIR/rpi4-nuttx-nsh-inmate.defconfig}"
BUILD="${BUILD:-$ROOT_DIR/nuttx-build}"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_OVERLAY="${REPO_OVERLAY:-$(cd "$SCRIPTS_DIR/.." && pwd)}"

echo "== 0. 前提检查 =="
for f in "$TC_TAR" "$NUTTX_TAR" "$APPS_TAR" "$DEFCONFIG_SRC"; do
  [ -f "$f" ] || { echo "FAIL 缺文件: $f" >&2; exit 1; }
done
df -h "$ROOT_DIR" | tail -1

echo "== 1. 解压工具链 =="
mkdir -p /opt/arm
# 注意：tarball 内目录名是 13.2.Rel1（大写 R）；用通配避免大小写踩坑
if ! ls /opt/arm/arm-gnu-toolchain-*-aarch64-none-elf/bin/aarch64-none-elf-gcc >/dev/null 2>&1; then
  tar -xJf "$TC_TAR" -C /opt/arm
fi
TC_BIN="$(ls -d /opt/arm/arm-gnu-toolchain-*-aarch64-none-elf/bin | head -1)"
export PATH="$TC_BIN:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export LANG=C.UTF-8 LC_ALL=C.UTF-8
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

echo "== 4. 应用 jailhouse-inmate 补丁（幂等）=="
# 注意：非 ASCII 文件名经 pscp 传输可能变成乱码，故用通配查找而非硬编码中文名
PATCHER="$(ls "$REPO_OVERLAY"/patches/apply_patches*.sh 2>/dev/null | head -1 || true)"
[ -n "$PATCHER" ] || { echo "FAIL: 未找到 patches/apply_patches*.sh" >&2; exit 1; }
bash "$PATCHER" "$BUILD/nuttx"

echo "== 5. configure =="
cd "$BUILD/nuttx"
# configure.sh 在已配置的树上会拒绝（"Already configured"）；先清再配
if [ -f .config ]; then
  make distclean >/dev/null 2>&1 || rm -f .config
fi
tools/configure.sh raspberrypi-4b:nsh-inmate

echo "== 6. make -j4 =="
make -j4 2>&1 | tail -25

echo "== 7. 产物 =="
ls -la nuttx.bin nuttx 2>/dev/null
echo "完成。下一步（在 Pi 上执行部署）:
  bash $REPO_OVERLAY/scripts/deploy_nuttx_cell__部署NuttX_cell.sh
  # 或一键复现：
  bash $REPO_OVERLAY/scripts/reproduce_pi4_crtos__一键复现.sh
串口观测：接 GPIO 14/15 的 USB-TTL，115200 8N1（不是 jailhouse console）。"
