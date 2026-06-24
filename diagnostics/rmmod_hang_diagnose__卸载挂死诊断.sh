#!/bin/bash
# 全面诊断 rmmod 卡死根因
set -eu
MOD="official_cycletest_mod"

echo "=== [诊断] rmmod 卡死根因分析 ==="
echo "时间: $(date)"
echo ""

# 1. 检查模块状态
echo "--- 模块状态 ---"
lsmod | grep "^${MOD}" || echo "模块未加载"
echo ""

# 2. 检查内核线程
echo "--- 内核线程 ---"
ps -eLo pid,comm,wchan,state 2>/dev/null | grep -E 'ai_plc_' || echo "无线程"
echo ""

# 3. 检查 shutdown 状态
echo "--- shutdown 状态 ---"
SHUTDOWN_SYSFS="/sys/module/${MOD}/parameters/shutdown_request"
if [ -f "$SHUTDOWN_SYSFS" ]; then
    echo "shutdown_request = $(cat $SHUTDOWN_SYSFS 2>/dev/null || echo '读取失败')"
else
    echo "shutdown_request 参数不存在"
fi
echo ""

# 4. 检查模块引用计数
echo "--- 引用计数 ---"
lsmod 2>/dev/null | awk -v m="$MOD" '$1==m {print "refcnt:", $3, "used_by:", $4}' || echo "未找到"
echo ""

# 5. 最近 dmesg 关键信息
echo "--- 最近 dmesg (最后 30 行 AI-PLC 相关) ---"
dmesg 2>/dev/null | tail -200 | grep -i 'AI-PLC\|shutdown\|cyclictest\|exit\|killed\|stopped\|SIGKILL' | tail -30
echo ""

# 6. 检查 hang 在哪个函数
echo "--- 线程调用栈 (如果可用) ---"
for tid in $(ps -eLo tid,comm 2>/dev/null | grep -E 'ai_plc_' | awk '{print $1}'); do
    echo "Thread TID=$tid:"
    cat /proc/$tid/wchan 2>/dev/null && echo "  wchan: $(cat /proc/$tid/wchan)" || echo "  无法读取"
    cat /proc/$tid/stack 2>/dev/null | head -5 || echo "  无内核栈"
    echo ""
done

# 7. 尝试直接 rmmod (带 strace 看卡在哪)
echo "--- 测试 rmmod (2 秒超时，不实际执行) ---"
echo "如果卡住会在以下超时后继续："
timeout 3 sudo -n rmmod "${MOD}" 2>&1 || echo "rmmod 超时或失败 (RC=$?)"

echo ""
echo "=== 诊断完成 ==="
