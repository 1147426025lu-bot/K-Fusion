#!/bin/bash
# organize_pi4_root__整理Pi4目录.sh — 把 Pi /root 下本项目散落文件收拢到 /root/kfusion-pi4/
#
# 原则：
#   - 幂等（已移动过的目标自动跳过，可重复执行）
#   - 只挪不删（废弃件进 _old/）
#   - 绝不动 owjdxb/ 等第三方目录与 . 开头的系统文件
#   - 保留脚本默认路径：用符号链接让 /root/nuttx-build、/root/jh、
#     /root/rpi4-nuttx.c、/root/rpi4-nuttx-nsh-inmate.defconfig 继续可用
# 用法: bash organize_pi4_root__整理Pi4目录.sh
set -euo pipefail

BASE="${BASE:-/root/kfusion-pi4}"
R="/root"

echo "== 1. 建目录 =="
mkdir -p "$BASE"/{src,configs,build,out,scripts,patches,results,_old}
ls -la "$BASE"

echo "== 2. 源码包 -> src/ =="
for f in toolchain.tar.xz nuttx.tar.gz apps.tar.gz jailhouse-d6e64f90.tar.gz; do
  [ -e "$R/$f" ] && mv -v "$R/$f" "$BASE/src/" || true
done

echo "== 3. 配置 -> configs/（defconfig 改脚本默认名）=="
[ -e "$R/rpi4-nuttx.c" ] && mv -v "$R/rpi4-nuttx.c" "$BASE/configs/" || true
[ -e "$R/nsh-inmate.defconfig" ] && mv -v "$R/nsh-inmate.defconfig" "$BASE/configs/rpi4-nuttx-nsh-inmate.defconfig" || true

echo "== 4. 产物 -> out/ =="
for f in rpi4-nuttx.cell rpi4-nuttx.o; do
  [ -e "$R/$f" ] && mv -v "$R/$f" "$BASE/out/" || true
done

echo "== 5. 构建树 -> build/（+ 兼容软链）=="
[ -d "$R/nuttx-build" ] && [ ! -L "$R/nuttx-build" ] && mv -v "$R/nuttx-build" "$BASE/build/nuttx-build" || true
[ -d "$R/jh" ] && [ ! -L "$R/jh" ] && mv -v "$R/jh" "$BASE/build/jh" || true
[ -e "$BASE/build/nuttx-build" ] && [ ! -e "$R/nuttx-build" ] && ln -svn "$BASE/build/nuttx-build" "$R/nuttx-build" || true
[ -e "$BASE/build/jh" ] && [ ! -e "$R/jh" ] && ln -svn "$BASE/build/jh" "$R/jh" || true

echo "== 6. 结果 -> results/（15min 正在写的文件先不动）=="
for f in baseline_soak_60s.txt; do
  [ -e "$R/$f" ] && mv -v "$R/$f" "$BASE/results/" || true
done

echo "== 7. 废弃件 -> _old/（只挪不删）=="
for f in jailhouse-v0.12.tar.gz jh-headers pad.bin nsh-inmate-defconfig \
         build_nuttx_inmate.sh deploy_nuttx_cell.sh make_inmate.sh; do
  [ -e "$R/$f" ] && mv -v "$R/$f" "$BASE/_old/" || true
done

echo "== 8. 配置兼容软链 =="
[ -e "$BASE/configs/rpi4-nuttx.c" ] && [ ! -e "$R/rpi4-nuttx.c" ] \
  && ln -svn "$BASE/configs/rpi4-nuttx.c" "$R/rpi4-nuttx.c" || true
[ -e "$BASE/configs/rpi4-nuttx-nsh-inmate.defconfig" ] && [ ! -e "$R/rpi4-nuttx-nsh-inmate.defconfig" ] \
  && ln -svn "$BASE/configs/rpi4-nuttx-nsh-inmate.defconfig" "$R/rpi4-nuttx-nsh-inmate.defconfig" || true

echo "== 9. 写 README =="
cat > "$BASE/README.txt" <<'EOF'
# /root/kfusion-pi4 — Pi4 cRTOS(NuttX-inmate) 复现工作区
# 由 organize_pi4_root__整理Pi4目录.sh 生成（2026-10-15）
#
# 对应仓库: K-Fusion/crtos/jailhouse_overlay_pi4/
#
# src/      源码与工具链包（toolchain.tar.xz / nuttx.tar.gz / apps.tar.gz / jailhouse-*.tar.gz）
# configs/  cell 源(rpi4-nuttx.c) + inmate defconfig(rpi4-nuttx-nsh-inmate.defconfig)
# build/    构建树 nuttx-build/ 与 jailhouse 头文件 jh/
# out/      cell 二进制(rpi4-nuttx.cell) 与中间产物
# scripts/  同步自仓库的最新脚本
# patches/  arm64_head.S 补丁 + 幂等应用脚本
# results/  测量结果（baseline_soak_60s.txt / rootcell_15min.txt ...）
# _old/     已废弃件（只挪不删，可人工清理）
#
# 兼容软链（在 /root 下，指向本目录）：
#   /root/nuttx-build  -> build/nuttx-build
#   /root/jh           -> build/jh
#   /root/rpi4-nuttx.c -> configs/rpi4-nuttx.c
#   /root/rpi4-nuttx-nsh-inmate.defconfig -> configs/rpi4-nuttx-nsh-inmate.defconfig
#   这样脚本默认路径无需改动即可继续工作。
#
# 一键复现（root）:
#   bash /root/kfusion-pi4/scripts/reproduce_pi4_crtos__一键复现.sh --root-soak 1
#
# 【不要动】owjdxb/（镜像自带第三方服务）与 /root 下 . 开头的系统文件。
EOF
echo "README 写入 $BASE/README.txt"

echo "== 10. 结果 =="
echo "-- $BASE --"; ls -la "$BASE"
echo "-- /root 兼容软链 --"; ls -la "$R" | grep -- '->' || true
echo "-- 校验脚本默认路径 --"
ls -d "$R/nuttx-build/nuttx" "$R/jh/include/jailhouse" "$R/rpi4-nuttx.c" "$R/rpi4-nuttx-nsh-inmate.defconfig" 2>&1 || true
echo "OK"
