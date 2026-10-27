#!/bin/sh
# auto_kernelize.sh — 一条命令：C 源 → 提取 → 自动构造计划(经独立验证器) → 生成用户态/内核态源码
#
# 这是 Gate 1「自动化」验收的可执行形态：用户只给一份 C 周期程序，不手写 DAG、
# 不改计算体，即可产出用户态/内核态源码。
#
# 用法：
#   bash auto_kernelize.sh <src.c> [out_dir] [period_us] [target]
#   target: linux-kernel | nuttx | let | both
#
# 前置：clang-16 + llvm-16-dev（Pi4 已装）；python3。提取器首次会自动构建。
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
KF=$(cd "$HERE/../.." && pwd)          # .../kfusion
SRC=${1:?用法: auto_kernelize.sh <src.c> [out_dir] [period_us] [target]}
OUT=${2:-$HERE/build/auto}
PERIOD=${3:-1000}
TARGET=${4:-linux-kernel}
CC=${CC:-clang-16}

if [ ! -f "$SRC" ]; then
	echo "错误：找不到源文件 $SRC" >&2
	exit 1
fi
mkdir -p "$OUT"

# 1) 提取器（缺则构建）
if [ ! -x "$HERE/build/stage_extract" ]; then
	echo "== [1/4] 构建 stage_extract =="
	bash "$HERE/build_on_pi.sh"
fi

# 2) C → bitcode → 提取
echo "== [2/4] 提取周期入口/阶段/全局 =="
"$CC" -O0 -g -emit-llvm -c "$SRC" -o "$OUT/in.bc"
"$HERE/build/stage_extract" "$OUT/in.bc" > "$OUT/extract.json"

# 3) 自动构造 dag/contract/plan（plan 由独立验证器 plan_verify 签署）
echo "== [3/4] 自动构造计划（经验证器） =="
python3 "$HERE/ast_to_plan.py" "$OUT/extract.json" --period-us "$PERIOD" --out-dir "$OUT"

# 4) 生成源码
echo "== [4/4] 生成 $TARGET 源码 =="
python3 "$KF/backend/synthesis/emit.py" \
	--plan "$OUT/plan.json" --dag "$OUT/dag.json" \
	--contract "$OUT/contract.json" --source "$SRC" \
	--out-dir "$OUT/gen" --target "$TARGET"

echo ""
echo "== 完成：中间产物 $OUT/{extract.json,dag.json,contract.json,plan.json}，源码在 $OUT/gen =="
