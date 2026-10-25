#!/usr/bin/env bash
# 把 NuttX 的开机入口从 xipc 恢复成 NSH（在 **Pi 上**运行）。
#
#   bash /root/xipc_restore__恢复开机入口.sh
#
# 为什么要单独一个脚本：xipc 自启动是为了让 root 侧驱动实验（这块板子的串口
# 只能看不能敲），但它把 init 占住了——留在板上就意味着开机没有 NSH。
# 实验做完要能一条命令把板子交回可交互状态。
set -u

B=/root/kfusion-pi4/build/nuttx-build
TC=/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/bin
export PATH="$TC:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

[ -d "$B/apps" ] || { echo "[restore] ❌ 找不到 $B/apps" >&2; exit 3; }

cd "$B/nuttx" || exit 3
python3 - "$PWD/.config" <<'PYEOF'
import io, sys
p = sys.argv[1]
want = {'CONFIG_INIT_ENTRYPOINT': '"nsh_main"', 'CONFIG_INIT_ENTRYNAME': '"nsh"'}
lines = io.open(p, encoding='utf-8', errors='replace').read().split('\n')
for i, ln in enumerate(lines):
    for k, v in want.items():
        if ln.startswith(k + '='):
            lines[i] = k + '=' + v
io.open(p, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines))
print('   INIT_ENTRYPOINT → nsh_main')
PYEOF

make -C "$B/apps" TOPDIR="$B/nuttx" -j3 >/tmp/restore_apps.log 2>&1 \
    || { tail -10 /tmp/restore_apps.log >&2; exit 3; }
make -C "$B/nuttx" -j3 >/tmp/restore_nuttx.log 2>&1 \
    || { grep -E 'error|Error [0-9]' /tmp/restore_nuttx.log | tail -10 >&2; exit 3; }

if grep -qa 'xipc: session 0x' "$B/nuttx/nuttx.bin"; then
    echo "[restore] ✅ 镜像 $(stat -c%s "$B/nuttx/nuttx.bin") B（xipc 仍在，但不再自启动）"
else
    echo "[restore] ✅ 镜像 $(stat -c%s "$B/nuttx/nuttx.bin") B"
fi
echo "[restore] 重新部署：bash /root/kfusion-pi4/scripts/deploy_nuttx_cell.sh"
