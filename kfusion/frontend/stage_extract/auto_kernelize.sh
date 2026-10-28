#!/bin/sh
# auto_kernelize.sh — 一条命令：C 源 → 提取 → 自动构造计划(经独立验证器) → 生成用户态/内核态源码
#
# 这是 Gate 1「自动化」验收的可执行形态：用户只给一份 C 周期程序，不手写 DAG、
# 不改计算体，即可产出用户态/内核态源码。
#
# 用法：
#   bash auto_kernelize.sh <src.c> [out_dir] [period_us] [target] [exec_us_file]
#   target: linux-kernel | nuttx | let | both
#   exec_us_file: 可选，@file 形式的每阶段执行时间（µs），由 measure_exec.py 产出
#
# 闭环示例（消除 exec 占位）：先不带 exec_us_file 生成并实测，再用测出的值重生——
#   bash auto_kernelize.sh ctl.c out1 1000 linux-kernel
#   insmod out1/gen/plc_cycle.ko; cat /sys/module/plc_cycle/parameters/exec_probe
#   rmmod plc_cycle
#   cat /sys/module/plc_cycle/parameters/exec_probe | python3 measure_exec.py > exec_us.json
#   bash auto_kernelize.sh ctl.c out2 1000 linux-kernel exec_us.json   # 用实测值
#
# 前置：clang-16 + llvm-16-dev（Pi4 已装）；python3。提取器首次会自动构建。
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
KF=$(cd "$HERE/../.." && pwd)          # .../kfusion
SRC=${1:?用法: auto_kernelize.sh <src.c> [out_dir] [period_us] [target] [exec_us_file]}
OUT=${2:-$HERE/build/auto}
PERIOD=${3:-1000}
TARGET=${4:-linux-kernel}
EXEC_US=${5:-}                          # 可选：每阶段执行时间 µs 的 JSON/@file
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
EXEC_ARG=""
if [ -n "$EXEC_US" ]; then
	if [ -f "$EXEC_US" ]; then
		EXEC_ARG="--exec-us @$EXEC_US"
	else
		EXEC_ARG="--exec-us $EXEC_US"
	fi
fi
python3 "$HERE/ast_to_plan.py" "$OUT/extract.json" --period-us "$PERIOD" --out-dir "$OUT" $EXEC_ARG

# 4) 生成源码
echo "== [4/4] 生成 $TARGET 源码 =="
python3 "$KF/backend/synthesis/emit.py" \
	--plan "$OUT/plan.json" --dag "$OUT/dag.json" \
	--contract "$OUT/contract.json" --source "$SRC" \
	--out-dir "$OUT/gen" --target "$TARGET"

echo ""
echo "== 完成：中间产物 $OUT/{extract.json,dag.json,contract.json,plan.json}，源码在 $OUT/gen =="
