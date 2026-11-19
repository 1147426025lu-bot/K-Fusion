#!/bin/bash
# ============================================================================
# build_hand_module__编译手写中断模块.sh — 论文 C 组手写硬中断模块的板编译
#
# 从仓库源码 kfusion/src/kf_hand_let__手写硬中断读写.c 生成 kf_task.ko，
# 放到 /home/pi/kf_hand_build/。模块名必须是 kf_task（复用 sysfs 参数路径）。
# ============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/kfusion/src/kf_hand_let__手写硬中断读写.c"
BUILD="${KF_HAND_BUILD:-/home/pi/kf_hand_build}"

mkdir -p "$BUILD"
cp "$SRC" "$BUILD/kf_task.c"
cat > "$BUILD/Makefile" <<'EOF'
obj-m := kf_task.o
KDIR := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
EOF
cd "$BUILD"
make clean >/dev/null 2>&1 || true
make
test -f kf_task.ko || { echo "编译失败：无 kf_task.ko"; exit 1; }
echo "✅ C组手写模块已编译：$BUILD/kf_task.ko"
ls -la "$BUILD/kf_task.ko"
