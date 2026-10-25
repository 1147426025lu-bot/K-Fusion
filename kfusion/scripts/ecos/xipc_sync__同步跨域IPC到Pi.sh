#!/usr/bin/env bash
# 同步跨域 IPC 最小运行时到 Pi4（PC 侧跑）。
#
#   bash kfusion/scripts/ecos/xipc_sync__同步跨域IPC到Pi.sh [RUN]
#
# 不带 RUN 只打包+上传；带 RUN 会把远端构建脚本也跑一遍。
#
# 两条硬约定（都是踩出来的）：
#   1. **整目录一律 tar**：pscp 会把中文名传成 GBK 乱码。
#   2. **只有一份头**：kfusion/runtime/xshm/ecos_xipc.h 是唯一真源，打包时复制进
#      app 目录；不手工维护第二份。
set -euo pipefail

PI=192.168.1.3
HK=SHA256:ECSWgs0cMn9Qk6FbrJecWoCkhHPY0JH/5JPbfWZAy3Y
PLINK="plink -l root -pw root -hostkey $HK $PI"
PSCP="pscp -pw root -hostkey $HK"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAGE="$ROOT/build/xipc_stage"
TGZ="$ROOT/build/xipc_stage.tgz"

HDR="$ROOT/kfusion/runtime/xshm/ecos_xipc.h"
APPSRC="$ROOT/crtos/jailhouse_overlay_pi4/nuttx/apps/examples/xipc"
RSRC="$ROOT/kfusion/runtime/xshm/root_side.c"

[ -f "$HDR" ] || { echo "缺 $HDR" >&2; exit 1; }
[ -f "$APPSRC/xipc_main.c" ] || { echo "缺 xipc_main.c" >&2; exit 1; }
[ -f "$RSRC" ] || { echo "缺 root_side.c" >&2; exit 1; }

rm -rf "$STAGE"; mkdir -p "$STAGE/xipc"
cp "$HDR" "$STAGE/xipc/ecos_xipc.h"            # 唯一真源 → app 目录
cp "$APPSRC"/Kconfig "$APPSRC"/Makefile "$APPSRC"/Make.defs "$STAGE/xipc/"
cp "$APPSRC/xipc_main.c" "$STAGE/xipc/"
cp "$RSRC" "$STAGE/root_side.c"
cp "$ROOT/kfusion/scripts/ecos/xipc_build__构建跨域IPC两端.sh" "$STAGE/xipc_build.sh"
# 恢复脚本也带上：实验做完要把开机入口还给 NSH，否则板上没有交互 shell
cp "$ROOT/kfusion/scripts/ecos/xipc_restore__恢复开机入口.sh" "$STAGE/xipc_restore.sh"

echo "[sync] 打包 $(cd "$STAGE" && find . -type f | wc -l) 个文件"
# tar 的 -f 用相对路径：Git Bash 下绝对盘符路径会被当成远程主机
( cd "$STAGE" && tar czf "$TGZ" . )

echo "[sync] 上传 → $PI:/root/ecos_xipc.tgz"
$PSCP "$TGZ" root@$PI:/root/ecos_xipc.tgz >/dev/null

# ⚠ 不要把中文名的脚本单独 scp 过去：pscp 会按 GBK 落盘，远端文件名变成乱码字节，
#   调用时就是 "No such file or directory"。一律走 tar（tar 头按字节存名）。
if [ "${1:-}" = "RUN" ]; then
    echo "[sync] 远端构建"
    $PLINK "rm -rf /root/xipc_boot && mkdir -p /root/xipc_boot && \
            tar xzf /root/ecos_xipc.tgz -C /root/xipc_boot && \
            bash /root/xipc_boot/xipc_build.sh /root/ecos_xipc.tgz"
else
    echo "[sync] 只上传。远端构建："
    echo "       rm -rf /root/xipc_boot && mkdir -p /root/xipc_boot"
    echo "       tar xzf /root/ecos_xipc.tgz -C /root/xipc_boot"
    echo "       bash /root/xipc_boot/xipc_build.sh /root/ecos_xipc.tgz"
fi
