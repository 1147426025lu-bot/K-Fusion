#!/bin/bash
# reproduce_pi4_crtos__一键复现.sh — 在 Raspberry Pi 4 上一键复现 cRTOS-like(NuttX inmate) 实验
#
# 运行位置: Pi4 本机（root）
# 前置: 见 build_nuttx_inmate__构建NuttX_inmate.sh 头部（工具链/源码/defconfig 已 pscp 上传）；
#       jailhouse 已 enable（root cell "Raspberry-Pi4" running）。
#
# 流程: 环境检查 → 构建 NuttX inmate → 编译 cell → create/load/start
#       → 提示在"接串口的主机"上跑 inmate 抖动测量
#       →（可选）root cell 基线测量
#
# 用法:
#   sudo bash reproduce_pi4_crtos__一键复现.sh [--skip-build] [--root-soak MIN]
# 环境变量: 见子脚本（NUTTX_SRC / JH_HDR / CELL_SRC / DEFCONFIG_SRC ...）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_OVERLAY="$(cd "$HERE/.." && pwd)"         # crtos/jailhouse_overlay_pi4
PI4_SCRIPTS="$(cd "$HERE/../../scripts/pi4" 2>/dev/null && pwd || echo "")"   # crtos/scripts/pi4

# 兼容：经 pscp 传输后非 ASCII 文件名可能变乱码，故用通配查找子脚本
BUILD_SH="$(ls "$HERE"/build_nuttx_inmate*.sh 2>/dev/null | head -1 || true)"
DEPLOY_SH="$(ls "$HERE"/deploy_nuttx_cell*.sh 2>/dev/null | head -1 || true)"
[ -n "$BUILD_SH" ]  || { echo "FAIL: 未找到 build_nuttx_inmate*.sh" >&2; exit 1; }
[ -n "$DEPLOY_SH" ] || { echo "FAIL: 未找到 deploy_nuttx_cell*.sh" >&2; exit 1; }

SKIP_BUILD=0
ROOT_SOAK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    --root-soak)  ROOT_SOAK="${2:-0}"; shift ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
  shift
done

echo "############ 0. 环境检查 ############"
command -v jailhouse >/dev/null || { echo "FAIL: 缺 jailhouse CLI" >&2; exit 1; }
jailhouse cell list 2>/dev/null | grep -qi 'Raspberry-Pi4' \
  || { echo "FAIL: root cell 未激活。先: sudo jailhouse enable /etc/jailhouse/rpi4.cell" >&2; exit 1; }
echo "OK: jailhouse root cell 已激活"

if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "############ 1. 构建 NuttX inmate ############"
  bash "$BUILD_SH"
else
  echo "############ 1. 跳过构建（--skip-build）############"
  [ -f "${NUTTX_SRC:-/root/nuttx-build/nuttx}/nuttx.bin" ] \
    || { echo "FAIL: 无 nuttx.bin" >&2; exit 1; }
fi

echo "############ 2. 编译 cell + 部署启动 ############"
bash "$DEPLOY_SH"

echo "############ 3. inmate 抖动测量（在接串口的主机侧执行）############"
echo "inmate 输出走物理 mini-UART(GPIO 14/15)，只能从「接串口的那台主机」驱动。请在主机上执行："
echo "  Windows(PowerShell)："
echo "    powershell -ExecutionPolicy Bypass -File <repo>/crtos/scripts/pi4/measure_inmate_serial__串口测inmate.ps1 \\"
echo "        -Port COM11 -IntervalUs 1000 -Loops 60000"
echo "  Linux/macOS："
echo "    bash <repo>/crtos/scripts/pi4/measure_inmate_serial__串口测inmate.sh /dev/ttyUSB0 1000 60000"
echo "得到日志后，可用（在 Pi 或仓库侧）："
echo "  INMATE_LOG=<日志> bash <repo>/crtos/scripts/pi4/measure_pi4_crtos__对比测量.sh 1"
echo "  # 其中 REALM=inmate"

if [ "$ROOT_SOAK" -gt 0 ] 2>/dev/null; then
  echo "############ 4. root cell 基线测量（${ROOT_SOAK} min）############"
  if [ -n "$PI4_SCRIPTS" ] && [ -f "$PI4_SCRIPTS/measure_pi4_crtos__对比测量.sh" ]; then
    REALM=root_cell bash "$PI4_SCRIPTS/measure_pi4_crtos__对比测量.sh" "$ROOT_SOAK" || true
  else
    echo "WARN: 未找到 measure_pi4_crtos__对比测量.sh（跳过）" >&2
  fi
fi

echo "############ 完成 ############"
echo "回顾：inmate(cRTOS 域) 15min abs_max ≈ 5µs；同板 root cell Linux（正确 FIFO99+绑核）15min ≈ 96µs。"
echo "结果对照见 crtos/results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md"
