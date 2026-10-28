#!/bin/sh
# run_on_pi.sh — 把 C 源编成 bitcode 再跑 stage_extract，打印 JSON。
# 用法：bash kfusion/frontend/stage_extract/run_on_pi.sh <源.c> [入口名]
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
BIN="$HERE/build/stage_extract"
SRC=${1:?用法: run_on_pi.sh <源.c> [入口名]}
ENTRY=${2:-}
CC=${CC:-clang-16}

if [ ! -x "$BIN" ]; then
	echo "未构建：先 bash $HERE/build_on_pi.sh" >&2
	exit 1
fi

BC=$(mktemp /tmp/stage_extract.XXXXXX.bc)
trap 'rm -f "$BC"' EXIT INT TERM
# -O0：保留显式 call（-O1 会把小阶段内联掉，调用图就看不见了）
"$CC" -O0 -g -emit-llvm -c "$SRC" -o "$BC"
if [ -n "$ENTRY" ]; then
	"$BIN" "$BC" "$ENTRY"
else
	"$BIN" "$BC"
fi
