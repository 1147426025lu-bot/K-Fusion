#!/bin/bash
# deploy_crtos_pi4__部署cRTOS.sh — Pi4 Jailhouse 部署（适配预构建镜像）
# 镜像: github.com/hearotop/jailhouse-RasPI4-images（Debian + 5.15.86 + Jailhouse）
# 用法(需 root): sudo bash deploy_crtos_pi4__部署cRTOS.sh <detect|enable|demo-inmate|linux-demo|status|teardown>
# 说明:
#   detect       只读：列出镜像内置 cell 配置与 demo payload
#   enable       启用 hypervisor（root cell = /etc/jailhouse/rpi4.cell）
#   demo-inmate  CPU1 裸机 demo（最小验证：证明分区生效；输出在串口）
#   linux-demo   CPU2-3 第二个 Linux（非 RTOS；验证双系统）
#   teardown     停/销 cell 并 disable
# 注意: 镜像默认不执行 enable；`jailhouse enable` 会把 root cell 的 CPU/内存按配置上交 hypervisor。
set -euo pipefail

ROOT_CELL="${ROOT_CELL:-/etc/jailhouse/rpi4.cell}"
INMATE_CELL="${INMATE_CELL:-/etc/jailhouse/rpi4-inmate-demo.cell}"
LINUX_CELL="${LINUX_CELL:-/etc/jailhouse/rpi4-linux-demo.cell}"
DEMOS="${DEMOS:-/usr/libexec/jailhouse/demos}"
CONSOLE="${CONSOLE:-console=ttyAMA0}"   # mini UART（GPIO 6/8/10）

say() { echo "== $* =="; }
cell_list()  { sudo jailhouse cell list 2>/dev/null || true; }
find_cell() {  # 按关键字在 cell list 里找名字
  sudo jailhouse cell list 2>/dev/null | awk 'NR>1{print $2}' | grep -i -m1 "$1" || true
}

CMD="${1:-status}"
case "$CMD" in
  detect)
    say "detect（只读）"
    echo "-- cell 配置 --"; ls -1 /etc/jailhouse/*.cell 2>/dev/null || echo "(无)"
    echo "-- demo payload --"; ls -1 "$DEMOS" 2>/dev/null || echo "(无 $DEMOS)"
    echo "-- 当前 cell --"; cell_list
    ;;

  enable)
    say "Jailhouse enable (root cell: $ROOT_CELL)"
    [ -f "$ROOT_CELL" ] || { echo "缺 $ROOT_CELL" >&2; exit 1; }
    sudo jailhouse enable "$ROOT_CELL"
    cell_list
    echo "提示: enable 后 root cell 仍运行；下一步可用 demo-inmate 做最小验证。"
    ;;

  demo-inmate)
    say "加载裸机 demo（CPU1，最小验证）"
    BIN="$DEMOS/inmate-demo.bin"
    [ -f "$BIN" ] || { echo "缺 $BIN" >&2; exit 1; }
    sudo jailhouse cell create "$INMATE_CELL"
    NAME="$(find_cell 'inmate\|demo')"
    NAME="${NAME:-inmate-demo}"
    sudo jailhouse cell load "$NAME" "$BIN"
    sudo jailhouse cell start "$NAME"
    echo "cell=$NAME 已启动；输出走串口(mini UART)。查看: jailhouse cell list / dmesg | tail"
    echo "停止: jailhouse cell shutdown $NAME && jailhouse cell destroy $NAME"
    ;;

  linux-demo)
    say "加载第二 Linux（CPU2-3，非 RTOS）"
    KERNEL="$DEMOS/linux-demo.bin"; INITRD="$DEMOS/rootfs.cpio"
    [ -f "$KERNEL" ] || { echo "缺 $KERNEL" >&2; exit 1; }
    [ -f "$INITRD" ] || { echo "缺 $INITRD" >&2; exit 1; }
    DTB="$(ls "$DEMOS"/*linux-demo*.dtb "$DEMOS"/*.dtb 2>/dev/null | head -1 || true)"
    sudo jailhouse cell create "$LINUX_CELL"
    NAME="$(find_cell 'linux\|demo')"
    NAME="${NAME:-linux-demo}"
    if [ -n "$DTB" ]; then
      sudo jailhouse cell linux "$LINUX_CELL" "$KERNEL" -d "$DTB" -r "$INITRD" -c "$CONSOLE"
    else
      echo "WARN: demos 目录无 dtb，尝试不带 -d（若失败请按镜像 docs/rpi4-dual-system.zh.md 提供 dtb）" >&2
      sudo jailhouse cell linux "$LINUX_CELL" "$KERNEL" -r "$INITRD" -c "$CONSOLE"
    fi
    cell_list
    echo "第二 Linux 输出走串口；通过串口 console 登录该 guest。"
    ;;

  status)
    say "Jailhouse status"
    cell_list
    ;;

  teardown)
    say "Teardown"
    for n in $(sudo jailhouse cell list 2>/dev/null | awk 'NR>1{print $2}' | grep -iv 'rootcell\|root cell' || true); do
      sudo jailhouse cell shutdown "$n" 2>/dev/null || true
      sudo jailhouse cell destroy "$n" 2>/dev/null || true
      echo "已停止/销毁: $n"
    done
    sudo jailhouse disable 2>/dev/null || true
    echo "已尝试 teardown（恢复单系统）"
    ;;

  *)
    echo "用法: sudo bash $0 <detect|enable|demo-inmate|linux-demo|status|teardown>" >&2
    exit 1
    ;;
esac
