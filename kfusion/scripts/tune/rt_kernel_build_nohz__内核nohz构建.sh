#!/bin/bash
# 为 Raspberry Pi RT 内核启用 CONFIG_NO_HZ_FULL 并编译（耗时数小时）
# 用法: bash scripts/tune/rt_kernel_build_nohz__内核nohz构建.sh
# 完成后安装、改 cmdline、reboot
set -euo pipefail

KVER="$(uname -r)"
BUILD_DIR="${KERNEL_BUILD_DIR:-/home/pi/rpi-linux-nohz}"
JOBS="${JOBS:-$(nproc)}"
FRAGMENT="$(cd "$(dirname "$0")" && pwd)/kernel-nohz__nohz配置.fragment"

echo "=== Pi RT 内核 NO_HZ_FULL 构建 ==="
echo "当前运行: ${KVER}"
grep -E 'NO_HZ' "/boot/config-${KVER}" 2>/dev/null || true
echo ""

if [ ! -d "$BUILD_DIR/.git" ]; then
    echo "[1/6] 克隆 Raspberry Pi 内核源码（首次较慢）..."
    git clone --depth=1 --branch rpi-6.12.y https://github.com/raspberrypi/linux.git "$BUILD_DIR"
else
    echo "[1/6] 使用已有源码: $BUILD_DIR"
fi

cd "$BUILD_DIR"

echo "[2/6] 复制当前运行内核配置..."
if [ -f "/boot/config-${KVER}" ]; then
    cp "/boot/config-${KVER}" .config
else
    echo "❌ 找不到 /boot/config-${KVER}"
    exit 1
fi

echo "[3/6] 应用 NO_HZ_FULL fragment..."
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|'#'*) continue ;;
        CONFIG_*)
            key="${line%%=*}"
            scripts/config --disable "${key#CONFIG_}" 2>/dev/null || true
            if [[ "$line" == *=y ]]; then
                scripts/config --enable "${key#CONFIG_}"
            elif [[ "$line" == *=m ]]; then
                scripts/config --module "${key#CONFIG_}"
            fi
            ;;
    esac
done < "$FRAGMENT"

make olddefconfig

echo "[4/6] 验证 NO_HZ_FULL..."
grep 'CONFIG_NO_HZ_FULL' .config

echo "[5/6] 编译内核+模块 (jobs=${JOBS})，预计 1-3 小时..."
make -j"${JOBS}" Image.gz modules dtbs

echo "[6/6] 安装模块..."
sudo make modules_install

echo ""
echo "✅ 编译完成。下一步（手动）:"
echo "  1. sudo cp arch/arm64/boot/Image.gz /boot/firmware/kernel8.img   # 备份原文件!"
echo "  2. bash scripts/tune/rt_cmdline_apply__cmdline应用.sh"
echo "  3. sudo reboot"
echo ""
echo "⚠️ Pi 5 / RT 镜像设备树与启动路径可能不同，安装前请核对官方文档。"
