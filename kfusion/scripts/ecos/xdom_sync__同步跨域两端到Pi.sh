#!/usr/bin/env bash
# xdom_sync__同步跨域两端到Pi.sh —— 把 PR10-C 拆分出的两端产物打包上传（PC 侧）。
#
#   bash kfusion/scripts/ecos/xdom_sync__同步跨域两端到Pi.sh [RUN]
#
# 输入：build/xdom_stage/{nuttx,root}/  （由 ecos_build --split-by-domain 或
#       手工 split_plan + emit 产生；每份都已含 ecos_xdom.{h,c}）
#
# 规则同 PR10-A：**整目录一律 tar**（pscp 会把中文名传成 GBK 乱码），
# 头文件只有一份真源（kfusion/runtime/xshm/ecos_xipc.h），这里只负责搬运。
set -euo pipefail

PI=192.168.1.3
HK=SHA256:ECSWgs0cMn9Qk6FbrJecWoCkhHPY0JH/5JPbfWZAy3Y
PLINK="plink -l root -pw root -hostkey $HK $PI"
PSCP="pscp -pw root -hostkey $HK"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAGE="$ROOT/build/xdom_stage"
TGZ="$ROOT/build/xdom_stage.tgz"
SRC="$ROOT/kfusion/examples/ecos/five_stage_loop__闭环五阶段示例.c"

[ -d "$STAGE/nuttx" ] || { echo "缺 $STAGE/nuttx —— 先 split_plan + emit --xdom" >&2; exit 1; }
[ -d "$STAGE/root" ]  || { echo "缺 $STAGE/root" >&2; exit 1; }
[ -f "$SRC" ] || { echo "缺阶段源码 $SRC" >&2; exit 1; }

# ecos_xipc.h 是**唯一真源**（kfusion/runtime/xshm/），两端各拷一份进去。
# 不手工维护第二份——那正是"offset 差 4 字节"类 bug 的来源。
HDR="$ROOT/kfusion/runtime/xshm/ecos_xipc.h"
[ -f "$HDR" ] || { echo "缺 $HDR" >&2; exit 1; }
cp "$HDR" "$STAGE/root/ecos_xipc.h"
cp "$HDR" "$STAGE/nuttx/ecos_xipc.h"

# 两端都要一份 stage_impl.c（同一份源码，各自编译）
cp "$SRC" "$STAGE/root/stage_impl.c"
cp "$SRC" "$STAGE/nuttx/stage_impl.c"

# 自检：桥必须在，且两端方向相反（放错方向会"跑得动、数不对"）
for d in nuttx root; do
    for f in ecos_xdom.c ecos_xdom.h ecos_fused_body.c ecos_fused_body.h; do
        [ -f "$STAGE/$d/$f" ] || { echo "$d 缺 $f" >&2; exit 1; }
    done
done
grep -q 'ECOS_XDOM_OWNER 1' "$STAGE/root/ecos_xdom.c"  || { echo "root 端不是 owner" >&2; exit 1; }
grep -q 'ECOS_XDOM_OWNER 0' "$STAGE/nuttx/ecos_xdom.c" || { echo "nuttx 端不是 peer" >&2; exit 1; }

# Pi 侧构建脚本也塞进包里（ASCII 名）。⚠ 不要单独 pscp 中文名脚本：
# pscp 会按 GBK 落盘，远端文件名变成乱码字节，调用就是 No such file。
cp "$ROOT/kfusion/scripts/ecos/xdom_build__构建跨域两端.sh" "$STAGE/xdom_build.sh"

echo "[sync] 打包两端"
( cd "$STAGE" && tar czf "$TGZ" . )
$PSCP "$TGZ" root@$PI:/root/ecos_xdom.tgz >/dev/null

if [ "${1:-}" = "RUN" ]; then
    echo "[sync] 远端构建"
    $PLINK "rm -rf /root/xdom_boot && mkdir -p /root/xdom_boot && \
            tar xzf /root/ecos_xdom.tgz -C /root/xdom_boot && \
            bash /root/xdom_boot/xdom_build.sh /root/ecos_xdom.tgz"
else
    echo "[sync] 只上传。远端构建："
    echo "       rm -rf /root/xdom_boot && mkdir -p /root/xdom_boot"
    echo "       tar xzf /root/ecos_xdom.tgz -C /root/xdom_boot"
    echo "       bash /root/xdom_boot/xdom_build.sh /root/ecos_xdom.tgz"
fi
