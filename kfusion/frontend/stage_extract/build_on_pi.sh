#!/bin/sh
# build_on_pi.sh — 在 Pi4 上构建 stage_extract（LLVM C API）。
# 前置：clang-16 + llvm-16-dev 已装（Debian bullseye 自带，llvm-config-16 可查）。
# 用法：bash kfusion/frontend/stage_extract/build_on_pi.sh
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
OUT="${OUT:-$HERE/build}"
CC=${CC:-clang-16}
LLVMCONFIG=${LLVMCONFIG:-llvm-config-16}

if ! command -v "$CC" >/dev/null 2>&1; then
	echo "错误：找不到 $CC" >&2
	exit 1
fi
if ! command -v "$LLVMCONFIG" >/dev/null 2>&1; then
	echo "错误：找不到 $LLVMCONFIG（需要 llvm-16-dev）" >&2
	exit 1
fi
if [ ! -f /usr/lib/llvm-16/include/llvm-c/Core.h ]; then
	echo "错误：缺少 llvm-c/Core.h（需要 llvm-16-dev）" >&2
	exit 1
fi

mkdir -p "$OUT"
# shellcheck disable=SC2086
"$CC" "$HERE/stage_extract__阶段提取.c" \
	$("$LLVMCONFIG" --cflags) -O2 -Wall -Wextra \
	-o "$OUT/stage_extract" \
	$("$LLVMCONFIG" --ldflags --libs --system-libs)

echo "built: $OUT/stage_extract"
