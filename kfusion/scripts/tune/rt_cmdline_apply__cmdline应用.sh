#!/bin/bash
# 应用 level-4 启动参数（需 reboot）
set -euo pipefail

CMDLINE_FILES=(/boot/firmware/cmdline.txt /boot/cmdline.txt)
STAMP="$(date +%Y%m%d_%H%M%S)"

replace_isolcpus() {
    local line="$1"
    line=$(echo "$line" | sed -E 's/\bisolcpus=[^ ]+//g')
    line=$(echo "$line" | sed -E 's/\bnohz_full=[^ ]+//g')
    line=$(echo "$line" | sed -E 's/\brcu_nocbs=[^ ]+//g')
    line=$(echo "$line" | sed -E 's/\birqaffinity=[^ ]+//g')
    line="${line} isolcpus=domain,managed_irq,3 nohz_full=3 rcu_nocbs=3 irqaffinity=0-2"
    echo "$line" | tr -s ' '
}

for f in "${CMDLINE_FILES[@]}"; do
    [ -f "$f" ] || continue
    if [ ! -w "$f" ] && ! sudo -n test -w "$f" 2>/dev/null; then
        echo "⚠️ 无写权限: $f"
        continue
    fi
    current=$(tr -d '\n' <"$f")
    backup="${f}.bak.${STAMP}"
    sudo_cmd() { sudo -n "$@" 2>/dev/null || "$@"; }
    sudo_cmd cp "$f" "$backup"
    new=$(replace_isolcpus "$current")
    echo "$new" | sudo_cmd tee "$f" >/dev/null
    echo "✅ 已更新 $f"
    echo "   备份: $backup"
    echo "   新内容: $new"
done

echo ""
echo "⚠️ 请 reboot 后生效: sudo reboot"
echo "   reboot 后运行: bash scripts/tune/rt_cmdline_suggest__cmdline建议.sh 验证"
