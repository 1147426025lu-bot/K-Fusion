#!/bin/bash
# apply_patches__应用补丁.sh — 把 jailhouse-inmate 所需补丁应用到 NuttX 构建树（幂等）
#
# 用法: bash apply_patches__应用补丁.sh [NUTTX_SRC_DIR]
#   默认 NUTTX_SRC_DIR=${NUTTX_SRC:-/root/nuttx-build/nuttx}
#
# 原理：补丁以"改好的整文件"形式存放在本目录
#   patches/arm64_head.S.jailhouse-inmate.patched
# 应用 = 覆盖目标文件；带 JAILHOUSE-PATCH 标记做幂等判断，可安全重复执行。
set -euo pipefail

NUTTX="${1:-${NUTTX_SRC:-/root/nuttx-build/nuttx}}"
HERE="$(cd "$(dirname "$0")" && pwd)"

DST_DIR="$NUTTX/arch/arm64/src/common"
[ -d "$DST_DIR" ] || { echo "FAIL: 未找到 NuttX 源码树: $DST_DIR" >&2; exit 1; }

SRC="$HERE/arm64_head.S.jailhouse-inmate.patched"
DST="$DST_DIR/arm64_head.S"
[ -f "$SRC" ] || { echo "FAIL: 缺补丁文件 $SRC" >&2; exit 1; }

if grep -q 'JAILHOUSE-PATCH' "$DST" 2>/dev/null; then
  echo "OK: arm64_head.S 已打过补丁（跳过）"
  exit 0
fi

cp "$SRC" "$DST"
if grep -q 'JAILHOUSE-PATCH' "$DST"; then
  echo "OK: arm64_head.S 补丁已应用（mpidr 亲和性检查 -> nop）"
else
  echo "FAIL: 补丁应用后校验失败：$DST" >&2
  exit 1
fi
