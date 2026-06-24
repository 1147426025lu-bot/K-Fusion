#!/bin/bash
# ============================================================================
# ignite_baseline_cyclic__手写基线.sh — 构建/加载论文用手写 hrtimer 基线 .ko
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../deploy/../plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/../plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT/test"

PROBE_CPU="${JITTER_PROBE_CPU:-3}"
INTERVAL_US="${CYCLICTEST_INTERVAL_US:-1000}"
ACTION="${1:-load}"

cp "$PROJECT_ROOT/src/plc_baseline_cyclic__手写基线.c" "$PROJECT_ROOT/test/plc_baseline_cyclic.c"

cat > Makefile <<'EOF'
obj-m += baseline_cyclic_mod.o
baseline_cyclic_mod-objs := plc_baseline_cyclic.o
KDIR := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
EOF

make clean >/dev/null 2>&1 || true
make all
plc_require_file baseline_cyclic_mod.ko "baseline_cyclic_mod.ko"

if [ "$ACTION" = "build" ]; then
    echo "✅ 已构建 baseline_cyclic_mod.ko"
    exit 0
fi

plc_check_sudo 1
if lsmod | grep -q '^baseline_cyclic_mod'; then
    sudo -n rmmod baseline_cyclic_mod 2>/dev/null || true
    sleep 1
fi
if lsmod | grep -q '^official_cycletest_mod'; then
    plc_die "$PLC_E_KMOD" "请先卸载 official_cycletest_mod"
fi

sudo -n dmesg -c >/dev/null
sudo -n insmod baseline_cyclic_mod.ko probe_cpu="$PROBE_CPU" interval_us="$INTERVAL_US"
echo "✅ baseline_cyclic_mod loaded cpu=$PROBE_CPU interval_us=$INTERVAL_US"
echo "   stats: cat /sys/kernel/debug/baseline_stats"
