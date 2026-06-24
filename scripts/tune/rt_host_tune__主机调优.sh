#!/bin/bash
# ============================================================
# 实时主机调优脚本 — V22.3 极致抖动消除
# 适用场景：PLCC 1000μs cycle, hrtimer PINNED_HARD, CPU3 probe
# 目标：abs_max < 2μs, outliers=0
# ============================================================
set -e

echo "🛠️ [TUNE] 开始实时调优..."

# 1. cpu_dma_latency=0 — 禁止 CPU 进入深 idle
#    默认值为 2000000000ns (2秒)，设为 0 强制 C0 状态
echo "1️⃣  cpu_dma_latency = 0..."
echo 0 | sudo -n tee /dev/cpu_dma_latency 2>/dev/null || echo 0 | tee /dev/cpu_dma_latency 2>/dev/null || true

# 2. 禁用 CPU3 所有 cpuidle state（强制 C0）
echo "2️⃣  禁用 CPU3 cpuidle states..."
for state in /sys/devices/system/cpu/cpu3/cpuidle/state*; do
    [ -f "$state/disable" ] && echo 1 | tee "$state/disable" 2>/dev/null || true
done

# 3. arch_timer (IRQ 13) 绑到 CPU0-2，避开隔离核 CPU3
echo "3️⃣  arch_timer IRQ 亲和性 → CPU0-2..."
if [ -w /proc/irq/13/smp_affinity ]; then
    echo 7 | tee /proc/irq/13/smp_affinity 2>/dev/null || true
elif sudo -n test -w /proc/irq/13/smp_affinity 2>/dev/null; then
    echo 7 | sudo -n tee /proc/irq/13/smp_affinity 2>/dev/null || true
else
    echo "   ⚠️ 无法写入 /proc/irq/13/smp_affinity"
fi

# 4. timer_migration=0 — 禁止 hrtimer 跨 CPU 迁移
echo "4️⃣  timer_migration = 0..."
echo 0 | tee /sys/devices/system/cpu/cpu*/timer_migration 2>/dev/null || true

# 5. 确认 CPU3 的 RCU callback 已隔离
echo "5️⃣  确认 RCU CPU3 nocb..."
if grep -q 'rcu_nocbs=3' /proc/cmdline; then
    echo "   ✅ rcu_nocbs=3 已生效"
else
    echo "   ⚠️ rcu_nocbs=3 未设置，尝试动态启用..."
    echo 3 | tee /sys/kernel/rcu_cpu_boost_nocbs 2>/dev/null || true
fi

# 6. 提高内核看门狗/softlockup 阈值，减少 CPU3 上不必要的看门狗 tick
echo "6️⃣  调整看门狗阈值..."
echo 120 > /proc/sys/kernel/watchdog_thresh 2>/dev/null || true
echo 0 > /proc/sys/kernel/nmi_watchdog 2>/dev/null || true

# 7. 确认 CPU3 完全隔离
echo "7️⃣  确认 CPU3 隔离状态..."
if grep -q 'isolcpus=3' /proc/cmdline; then
    echo "   ✅ isolcpus=3 已生效"
    # 查看 CPU3 上是否有可迁移的进程
    for pid in $(ls /proc/*/task/*/stat 2>/dev/null | cut -d/ -f3 | sort -u); do
        cpu=$(awk '{print $39}' /proc/$pid/stat 2>/dev/null)
        [ "$cpu" = "3" ] && cat /proc/$pid/cmdline 2>/dev/null | tr '\0' ' ' && echo " (PID=$pid 在 CPU3)"
    done | head -10
fi

# 8. 让全局 unbound workqueue 避开 CPU3，减少探针核上的后台杂务
echo "8️⃣  配置 workqueue 避开 CPU3..."
# cpumask 按十六进制位图解释：0x7 = CPU0-2，显式排除 CPU3
echo 7 | sudo -n tee /sys/devices/virtual/workqueue/cpumask 2>/dev/null || echo 7 | tee /sys/devices/virtual/workqueue/cpumask 2>/dev/null || true

echo "✅ [TUNE] 调优完成！"
echo ""
echo "📊 验证命令："
echo "  cat /sys/devices/system/cpu/cpu3/cpuidle/state0/disable"
echo "  cat /dev/cpu_dma_latency | hexdump"
echo "  grep 'cpu3' /proc/interrupts | head -3"
