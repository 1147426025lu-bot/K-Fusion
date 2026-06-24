#!/bin/bash
# 建议启动参数（需 reboot 后生效）— 隔离 level 4
set -euo pipefail

CMDLINE_FILES=(/boot/firmware/cmdline.txt /boot/cmdline.txt)
CURRENT=""
TARGET="isolcpus=domain,managed_irq,3 nohz_full=3 rcu_nocbs=3 irqaffinity=0-2"

for f in "${CMDLINE_FILES[@]}"; do
    [ -f "$f" ] && CURRENT=$(tr -d '\n' <"$f") && break
done

echo "=== 当前 cmdline ==="
echo "$CURRENT"
echo ""
echo "=== 建议追加/替换（level 4，需 reboot）==="
echo "$TARGET"
echo ""
echo "说明："
echo "  isolcpus=domain,managed_irq,3  — 调度域 + managed IRQ 隔离"
echo "  irqaffinity=0-2                — 新 IRQ 默认不进 CPU3"
echo "  nohz_full=3                    — 需内核 CONFIG_NO_HZ_FULL=y 才真正 tickless"
echo ""
echo "⚠️ 当前内核: $(grep -E '^# CONFIG_NO_HZ_FULL|^CONFIG_NO_HZ_FULL' /boot/config-"$(uname -r)" 2>/dev/null || echo '未知')"
echo ""
echo "手动编辑示例（勿直接覆盖 console/root 等参数）："
echo "  sudo nano /boot/firmware/cmdline.txt"
echo "  将 isolcpus=3 ... 改为 isolcpus=domain,managed_irq,3 ... irqaffinity=0-2"
