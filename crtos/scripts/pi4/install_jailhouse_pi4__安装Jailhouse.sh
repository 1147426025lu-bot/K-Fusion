#!/bin/bash
# install_jailhouse_pi4__安装Jailhouse.sh — 在 Pi4 上编译并安装上游 Jailhouse（不自动 reboot / 不 enable）
# 用法: bash install_jailhouse_pi4__安装Jailhouse.sh
# 环境变量: JH_REPO(可选,默认用仓库内 upstream/jailhouse)  KDIR(可选,默认当前内核)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../../kfusion/scripts/lib/repo_paths__仓库路径.sh" 2>/dev/null || true

JH="${JH_REPO:-${CRTOS_JAILHOUSE:-$REPO_ROOT/crtos/upstream/jailhouse}}"
KREL="$(uname -r)"
KDIR="${KDIR:-/lib/modules/$KREL/build}"

echo "=== 安装 Jailhouse (Pi4) ==="
echo "JH=$JH"
echo "KDIR=$KDIR"

if [ ! -f "$JH/Makefile" ]; then
  echo "未找到 Jailhouse 源码于 $JH" >&2
  echo "请先放置上游源码（仓库内 crtos/upstream/jailhouse 已在位），或设 JH_REPO=..." >&2
  exit 1
fi
if [ ! -d "$KDIR" ]; then
  echo "缺内核头文件 $KDIR；先装 linux-headers-$KREL" >&2
  exit 1
fi

# 1. 依赖（仅在能 sudo 时）
if command -v sudo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq git build-essential python3-dev libpython3-dev \
    device-tree-compiler bison flex libssl-dev kmod
fi

# 2. 编译（Pi 本机原生编译，无需交叉）
echo "--- make (arch=arm64) ---"
make -C "$JH" ARCH=arm64 KDIR="$KDIR"

# 3. 安装（driver/ko + 工具 + cell 到 /etc/jailhouse + firmware）
echo "--- make install ---"
sudo make -C "$JH" ARCH=arm64 KDIR="$KDIR" install

# 4. 校验产物
BIN="$JH/hypervisor/jailhouse.bin"
KO="$JH/driver/jailhouse.ko"
CELL="$JH/configs/arm64/rpi4.cell"
echo "校验:"
for f in "$BIN" "$KO" "$CELL"; do
  if [ -s "$f" ]; then echo "  OK  $f ($(stat -c '%s' "$f") bytes)"; else echo "  FAIL $f 缺失" >&2; fi
done
ls -l /lib/firmware/jailhouse.bin 2>/dev/null && echo "  OK  /lib/firmware/jailhouse.bin" || echo "  WARN /lib/firmware/jailhouse.bin 未安装"

echo ""
echo "完成编译安装。下一步（手动、需 sudo + 可能 reboot）："
echo "  sudo bash deploy_crtos_pi4__部署cRTOS.sh enable"
echo "注意: Jailhouse 可能需要特定内核配置(CONFIG_JAILHOUSE)与 EL2；若 enable 报"
echo "      'CPU not in EL2' 或 symbol 缺失，需换 jailhouse-images 镜像或自编译内核。"
