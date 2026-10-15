#!/bin/bash
# preflight_pi4__平台探测.sh — 只读探测 Pi4 预构建 Jailhouse 镜像是否就绪（安全，不改动系统）
# 适配镜像: github.com/hearotop/jailhouse-RasPI4-images (Debian + Linux 5.15.86 + Jailhouse)
# 用法: bash preflight_pi4__平台探测.sh
set -uo pipefail

echo "=== cRTOS / Jailhouse Pi4 preflight (预构建镜像版) ==="
echo "时间: $(date -Iseconds)"
FAIL=0
ok()   { echo "OK   $*"; }
warn() { echo "WARN $*" >&2; }
bad()  { echo "FAIL $*" >&2; FAIL=1; }

# --- 架构 / 板型 ---
ARCH="$(uname -m)"
if [ "$ARCH" = "aarch64" ]; then ok "arch=$ARCH"; else bad "arch=$ARCH (需要 aarch64)"; fi

MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unknown)"
echo "      model: $MODEL"
case "$MODEL" in
  *"Raspberry Pi 4"*) ok "board 是 Pi4 系列（cRTOS 论文基线板）";;
  *"Raspberry Pi 5"*) bad "board 是 Pi5，本方案针对 Pi4";;
  *) warn "board 非预期: $MODEL";;
esac

# --- 内核（镜像为 5.15.86）---
KREL="$(uname -r)"
echo "      kernel: $KREL"
case "$KREL" in
  5.15*) ok "内核 5.15.x（与镜像构建记录一致）";;
  *) warn "内核 $KREL 与镜像记录(5.15.86)不同，确认是否烧错镜像";;
esac

# --- jailhouse 内核模块（镜像声称自动加载）---
if lsmod 2>/dev/null | grep -q '^jailhouse'; then
  ok "jailhouse 内核模块已加载"
elif sudo -n modprobe jailhouse 2>/dev/null && lsmod | grep -q '^jailhouse'; then
  ok "jailhouse 模块已手动加载成功"
else
  bad "jailhouse 模块未加载（dmesg 里查 jailhouse 报错）"
fi

# --- jailhouse CLI ---
if command -v jailhouse >/dev/null 2>&1; then
  ok "jailhouse CLI: $(command -v jailhouse) ($("jailhouse" --version 2>/dev/null | head -1))"
else
  bad "缺 jailhouse CLI"
fi

# --- cell 配置（镜像内置三份）---
for c in rpi4.cell rpi4-linux-demo.cell rpi4-inmate-demo.cell; do
  if [ -f "/etc/jailhouse/$c" ]; then ok "cell 配置: /etc/jailhouse/$c"; else bad "缺 /etc/jailhouse/$c"; fi
done

# --- demo payload ---
DEMOS="/usr/libexec/jailhouse/demos"
if [ -d "$DEMOS" ]; then
  ls -1 "$DEMOS" 2>/dev/null | sed 's/^/      demo: /'
  [ -f "$DEMOS/inmate-demo.bin" ] && ok "裸机 demo payload: inmate-demo.bin" || warn "缺 inmate-demo.bin"
  [ -f "$DEMOS/rootfs.cpio" ]     && ok "linux-demo rootfs: rootfs.cpio"   || warn "缺 rootfs.cpio"
else
  warn "无 $DEMOS（linux/inmate demo payload 缺失）"
fi

# --- 内存预留（镜像在设备树预留 0x10000000-0x1fffffff, 256MiB）---
if grep -qi '10000000' /proc/iomem 2>/dev/null; then
  ok "检测到 0x10000000 起的预留区（/proc/iomem）"
else
  warn "未在 /proc/iomem 看到 0x10000000 预留（可能显示为 Reserved；dmesg 可进一步确认）"
fi
dmesg 2>/dev/null | grep -i -m3 'jailhouse\|reserved' | sed 's/^/      dmesg: /'

# --- EL2 ---
if dmesg 2>/dev/null | grep -qi 'EL2\|hypervisor mode'; then
  ok "dmesg 检测到 EL2/hyp 线索"
else
  warn "无法从 dmesg 确认 EL2；Pi4 默认 CPU 从 EL2 启动，enable 时即验证"
fi

# --- 网络 / SSH（供 Windows 侧连接）---
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "      ip: ${IP:-unknown}   user: $(whoami)"
[ -n "$IP" ] && ok "有 IP，可从 Windows 侧 ssh ${USER}@${IP}" || warn "无 IP（DHCP 未就绪）"

# --- 网络工具（测量阶段用）---
for t in cyclictest taskset chrt; do
  command -v "$t" >/dev/null 2>&1 && ok "tool: $t" || warn "缺 $t（root cell 测量侧需要，可 apt 安装 rt-tests）"
done

echo ""
echo "说明: 预构建镜像无需内核头文件/上游源码，脚本不再检查它们。"
if [ "$FAIL" -eq 0 ]; then
  echo "preflight OK — 下一步: sudo bash deploy_crtos_pi4__部署cRTOS.sh enable"
else
  echo "preflight 有失败项，先解决再继续。"
  exit 1
fi
