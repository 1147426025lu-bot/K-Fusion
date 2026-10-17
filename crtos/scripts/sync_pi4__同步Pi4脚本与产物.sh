#!/bin/bash
# sync_pi4__同步Pi4脚本与产物.sh — PC(Git Bash) 侧：把仓库里的 Pi4 脚本/产物同步到 Pi 工作区
#
# 为什么用 tar 而不是直接 pscp 目录：
#   OpenSSH 的 pscp/pscp.exe 在 Windows 上会把「中文文件名」按 GBK 转码，
#   传到 Pi(UTF-8) 后变成乱码（如 197609 乱码名），导致脚本按名找不到依赖。
#   tar 的头部按字节存文件名 → 打包再解包可完整保留 UTF-8 名称。
#
# 目标布局（与仓库 crtos/ 一一对应）：
#   crtos/scripts/pi4/*                          -> /root/kfusion-pi4/scripts/pi4/
#   crtos/jailhouse_overlay_pi4/scripts/*         -> /root/kfusion-pi4/scripts/
#   crtos/jailhouse_overlay_pi4/patches/*         -> /root/kfusion-pi4/patches/
#   crtos/jailhouse_overlay_pi4/configs/*         -> /root/kfusion-pi4/configs/
#   crtos/jailhouse_overlay_pi4/nuttx/apps/**     -> /root/kfusion-pi4/nuttx/apps/**
#
# 用法（Git Bash，仓库根目录）：
#   bash crtos/scripts/sync_pi4__同步Pi4脚本与产物.sh                      # 用默认 host/hostkey
#   PI_HOST=root@192.168.1.4 bash crtos/scripts/sync_pi4__同步Pi4脚本与产物.sh
#   PI_HOST=root@10.0.0.5 PI_PW=xxx bash crtos/scripts/sync_pi4__同步Pi4脚本与产物.sh
#
# 幂等：重复执行只覆盖同名文件，不删除 Pi 上的其它内容（results/、build/ 不受影响）。
set -euo pipefail

PI_HOST="${PI_HOST:-root@192.168.1.4}"
PI_PW="${PI_PW:-root}"
PI_HOSTKEY="${PI_HOSTKEY:-SHA256:ECSWgs0cMn9Qk6FbrJecWoCkhHPY0JH/5JPbfWZAy3Y}"
PI_ROOT="${PI_ROOT:-/root/kfusion-pi4}"

HERE="$(cd "$(dirname "$0")" && pwd)"          # crtos/scripts
CRTOS="$(cd "$HERE/.." && pwd)"                 # crtos
REPO_ROOT="$(cd "$CRTOS/.." && pwd)"            # 仓库根
OV="$CRTOS/jailhouse_overlay_pi4"

# 暂存目录放在仓库内（避免 mktemp 在 Git Bash 下返回 C:\ 盘符路径被 tar 当成远程主机）
STAGE="$REPO_ROOT/.workbuddy/tmp/pi4-sync-stage"
TARBALL="$REPO_ROOT/.workbuddy/tmp/kfusion-pi4-sync.tgz"
rm -rf "$STAGE" 2>/dev/null || true
mkdir -p "$STAGE"
cleanup() { rm -rf "$STAGE" 2>/dev/null || true; }
trap cleanup EXIT

echo "== 1. 组装暂存目录（镜像 Pi 端布局）=="
mkdir -p "$STAGE/scripts/pi4" "$STAGE/patches"
[ -d "$CRTOS/scripts/pi4" ] && cp -a "$CRTOS/scripts/pi4/." "$STAGE/scripts/pi4/"
[ -d "$OV/scripts" ]        && cp -a "$OV/scripts/."        "$STAGE/scripts/"
[ -d "$OV/patches" ]        && cp -a "$OV/patches/."        "$STAGE/patches/"
[ -d "$OV/configs" ]        && mkdir -p "$STAGE/configs" && cp -a "$OV/configs/." "$STAGE/configs/"
if [ -d "$OV/nuttx/apps" ]; then
  mkdir -p "$STAGE/nuttx/apps"
  cp -a "$OV/nuttx/apps/." "$STAGE/nuttx/apps/"
fi

# 同步过来的文件统一去 CR（Windows 检出可能是 CRLF），避免 Pi 上 $'\r' 报错
find "$STAGE" -type f \( -name '*.sh' -o -name '*.c' -o -name '*.h' \
  -o -name '*.py' -o -name '*.defconfig' -o -name 'Makefile' -o -name 'Make.defs' \) \
  -exec sed -i 's/\r$//' {} + 2>/dev/null || true

echo "== 2. 打包（tar 保留 UTF-8 文件名）=="
# 关键：在暂存目录内用「相对路径」作为 -f，避免 Git Bash 的 C:\ 路径被 tar 当成远程主机名
TARBALL_NAME="kfusion-pi4-sync.tgz"
( cd "$STAGE" && tar czf "../$TARBALL_NAME" . )
echo "   包大小: $(wc -c < "$TARBALL") 字节"
( cd "$STAGE" && tar tzf "../$TARBALL_NAME" ) | sed 's/^/   /'

echo "== 3. 传输到 Pi =="
pscp -batch -hostkey "$PI_HOSTKEY" -pw "$PI_PW" "$TARBALL" "$PI_HOST:/tmp/kfusion-pi4-sync.tgz"

echo "== 4. 在 Pi 上解包 + 修权限 + 建 ASCII 软链 =="
plink -ssh -batch -hostkey "$PI_HOSTKEY" -pw "$PI_PW" "$PI_HOST" "export LANG=C.UTF-8 LC_ALL=C.UTF-8
set -e
mkdir -p '$PI_ROOT'
tar xzf /tmp/kfusion-pi4-sync.tgz -C '$PI_ROOT'
chown -R root:root '$PI_ROOT/scripts' '$PI_ROOT/patches' 2>/dev/null || true
chmod 755 '$PI_ROOT'/scripts/*.sh '$PI_ROOT'/scripts/pi4/*.sh '$PI_ROOT'/patches/*.sh 2>/dev/null || true
# ASCII 快捷软链（方便不带中文输入时直接跑）
cd '$PI_ROOT/scripts/pi4' 2>/dev/null && {
  ln -sf 'preflight_pi4__平台探测.sh' preflight_pi4.sh
  ln -sf 'deploy_crtos_pi4__部署cRTOS.sh' deploy_crtos_pi4.sh
  ln -sf 'measure_pi4_crtos__对比测量.sh' measure_pi4_crtos.sh
  ln -sf 'measure_inmate_serial__串口测inmate.sh' measure_inmate_serial.sh
  ln -sf 'install_jailhouse_pi4__安装Jailhouse.sh' install_jailhouse_pi4.sh
  ln -sf 'organize_pi4_root__整理Pi4目录.sh' organize_pi4_root.sh
  ln -sf 'ivshmem_probe__跨域共享内存.py' ivshmem_probe.py
}
cd '$PI_ROOT/scripts' 2>/dev/null && {
  ln -sf 'build_nuttx_inmate__构建NuttX_inmate.sh' build_nuttx_inmate.sh
  ln -sf 'deploy_nuttx_cell__部署NuttX_cell.sh' deploy_nuttx_cell.sh
  ln -sf 'reproduce_pi4_crtos__一键复现.sh' reproduce_pi4_crtos.sh
}
cd '$PI_ROOT/patches' 2>/dev/null && ln -sf 'apply_patches__应用补丁.sh' apply_patches.sh
echo '--- scripts/ ---'; ls -l '$PI_ROOT/scripts'
echo '--- scripts/pi4/ ---'; ls -l '$PI_ROOT/scripts/pi4'
"
echo ""
echo "同步完成。Pi 上运行（任选其一）："
echo "  cd $PI_ROOT/scripts/pi4 && bash preflight_pi4.sh          # ASCII 软链"
echo "  cd $PI_ROOT/scripts/pi4 && bash preflight_pi4__平台探测.sh  # 原名"
