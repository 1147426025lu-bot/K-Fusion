#!/bin/bash
# PR-10E 阶段 B：把 tracing 内核装成「一次性可试、失败自动回落」的形态
#
# 设计约束（来自评审）：
#   · 不覆盖当前稳定内核（kernel8.img 保持不动）
#   · 必须有回退路径（tryboot 失败由固件自动回落 config.txt）
#   · 不自动重启（重启由人决定，串口在看）
#
# 用法：
#   bash pr10e_install_trace_kernel__安装tracing内核.sh            # 真装
#   bash pr10e_install_trace_kernel__安装tracing内核.sh --dry-run  # 只检查不动盘
set -u

TREE=${TREE:-/root/linux-5.15.86-trace}
BOOT=${BOOT:-/mnt/bootp1}
VER=5.15.86-trace
IMG="$TREE/arch/arm64/boot/Image"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

ok()   { echo "  ✓ $*"; }
bad()  { echo "  ✗ $*"; }
warn() { echo "  ! $*"; }
info() { echo "    $*"; }

echo "=============================================================="
echo " PR-10E 阶段 B：tracing 内核安装（$( [ $DRY = 1 ] && echo 演练 || echo 实装 )）"
echo "=============================================================="

# ---------- [1/6] 前置检查 ----------
echo "[1/6] 前置检查"
rc=0
if [ ! -f "$IMG" ]; then bad "Image 不存在：$IMG"; rc=1; else
    sz=$(stat -c %s "$IMG")
    ok "Image 存在：$IMG（$((sz/1024/1024)) MB, $sz B）"
    # Pi4 的 kernel8.img 通常 20–35 MB；过小说明编歪了
    if [ "$sz" -lt 15000000 ]; then bad "Image 偏小（<15 MB），疑似配置跑偏"; rc=1; fi
fi
if ! mountpoint -q "$BOOT"; then bad "$BOOT 不是挂载点（先 mount -o rw /dev/mmcblk0p1 $BOOT）"; rc=1; fi
# 启动分区常被挂在 ro：真装前需可写，演练不碰
if [ $DRY = 0 ] && mountpoint -q "$BOOT"; then
    if ! touch "$BOOT/.wtest" 2>/dev/null; then
        mount -o remount,rw "$BOOT" 2>/dev/null
        if ! touch "$BOOT/.wtest" 2>/dev/null; then
            bad "$BOOT 不可写，且 remount rw 失败"; rc=1
        else
            ok "已 remount rw：$BOOT"
        fi
    fi
    rm -f "$BOOT/.wtest" 2>/dev/null
fi
if [ ! -f "$BOOT/kernel8.img" ]; then bad "$BOOT/kernel8.img 不存在 —— 没有稳定内核可回落"; rc=1; else
    ok "稳定内核在：kernel8.img（$(stat -c %s "$BOOT/kernel8.img") B），可回落"
fi

# 空间检查（2026-10-28 补：此前漏查，导致 image 被截断写入、tryboot.txt 为 0 字节）
if [ -f "$IMG" ] && mountpoint -q "$BOOT"; then
    need=$(stat -c %s "$IMG")
    avail=$(df -B1 --output=avail "$BOOT" 2>/dev/null | tail -1 | tr -d ' ')
    if [ -n "$avail" ] && [ "$avail" -lt "$need" ]; then
        bad "启动分区空间不足：需要 $((need/1024/1024)) MB，可用 $((avail/1024/1024)) MB"
        bad "  → 不会尝试写入（否则 image 会被截断、tryboot.txt 变成 0 字节）"
        info "分区总容量：$(df -h "$BOOT" | tail -1 | awk '{print $2}')"
        info "现有内核占用：$(stat -c %s "$BOOT/kernel8.img" 2>/dev/null | awk '{printf "%.1f MB", $1/1048576}')"
        info "候选方案：① 压缩内核（gzip 后约 10.5 MB，但需固件支持 gzip 启动，无法事前验证）"
        info "          ② 换更大 SD 卡 / 扩容 p1（改分区表，风险高）"
        info "          ③ 放弃 tracing 内核，改用 CLOCK_THREAD_CPUTIME_ID 做每拍 CPU-time 分解"
        rc=1
    else
        ok "启动分区空间足够：需 $((need/1024/1024)) MB，可用 $((avail/1024/1024)) MB"
    fi
fi
if [ ! -f "$BOOT/config.txt" ]; then bad "$BOOT/config.txt 不存在"; rc=1; fi

# 核对 LOCALVERSION 真的生效（否则会和原内核撞模块目录）
if grep -q "^CONFIG_LOCALVERSION=\"$VER\"" "$TREE/.config" 2>/dev/null; then
    ok "CONFIG_LOCALVERSION 已钉死为 \"$VER\""
elif grep -q '^CONFIG_LOCALVERSION="-trace"' "$TREE/.config" 2>/dev/null; then
    ok "CONFIG_LOCALVERSION=\"-trace\"（配合 5.15.86 得 $VER）"
else
    bad "CONFIG_LOCALVERSION 不是 -trace —— 模块目录会撞车，停"; rc=1
fi

# 核对 tracer 全部编译进内核（不是"以为开了"）
missing=""
for k in FTRACE TRACING FUNCTION_TRACER SCHED_TRACER OSNOISE_TRACER TIMERLAT_TRACER; do
    grep -q "^CONFIG_$k=y" "$TREE/.config" 2>/dev/null || missing="$missing $k"
done
if [ -n "$missing" ]; then bad "以下 tracer 未生效：$missing"; rc=1; else
    ok "6 项 tracer 均为 =y"
fi

# 探测 tryboot 支持（固件里应有该字符串）
if [ -f "$BOOT/start4.elf" ] && strings "$BOOT/start4.elf" 2>/dev/null | grep -q tryboot; then
    ok "固件支持 tryboot"
else
    warn "未能确认固件支持 tryboot —— 若被忽略，本次会照常启动原内核（不变砖）"
fi

if [ $rc -ne 0 ]; then echo; echo "前置检查未通过，中止（未改动任何文件）"; exit 2; fi
if [ $DRY = 1 ]; then echo; echo "演练结束：前置检查通过，未改动任何文件。"; exit 0; fi

# ---------- [2/6] 记录指纹 ----------
echo "[2/6] 记录指纹"
IMG_SHA=$(sha256sum "$IMG" | awk '{print $1}')
CFG_SHA=$(sha256sum "$TREE/.config" | awk '{print $1}')
ok "Image sha256 = $IMG_SHA"
ok ".config sha256 = $CFG_SHA"

# ---------- [3/6] 装模块 ----------
echo "[3/6] 安装模块到 /lib/modules/$VER"
# 只装模块，不跑 depmod 之外的副作用；不触碰 /lib/modules/5.15.86
if [ -d "$TREE" ]; then
    make -C "$TREE" ARCH=arm64 INSTALL_MOD_PATH=/ modules_install >/tmp/inst_mod.log 2>&1
    if [ $? -eq 0 ] && [ -d "/lib/modules/$VER" ]; then
        ok "模块已装：/lib/modules/$VER（$(find /lib/modules/$VER -name '*.ko' | wc -l) 个 ko）"
    else
        bad "modules_install 失败，见 /tmp/inst_mod.log"; tail -5 /tmp/inst_mod.log; exit 3
    fi
else
    bad "源码树不存在"; exit 3
fi

# ---------- [4/6] 放内核镜像（不覆盖 kernel8.img）----------
echo "[4/6] 放置 kernel8-trace.img"
cp "$IMG" "$BOOT/kernel8-trace.img"
sync
if [ -f "$BOOT/kernel8-trace.img" ]; then
    ok "已放置 $BOOT/kernel8-trace.img（$(( $(stat -c %s "$BOOT/kernel8-trace.img")/1024/1024 )) MB）"
else
    bad "复制失败"; exit 4
fi
ok "kernel8.img 未改动（sha256=$(sha256sum "$BOOT/kernel8.img" | awk '{print $1}')）"

# ---------- [5/6] 生成 tryboot.txt ----------
echo "[5/6] 生成 tryboot.txt"
# 内容 = 完整 config.txt + 指定 kernel。一次性生效，失败由固件回落 config.txt。
{
    echo "# PR-10E 阶段 B：一次性试启动 tracing 内核（tryboot）"
    echo "# 由 pr10e_install_trace_kernel__安装tracing内核.sh 生成"
    echo "# 与 config.txt 的唯一差异是下面这行 kernel=；其余原样继承以最小化变量"
    echo "kernel=kernel8-trace.img"
    cat "$BOOT/config.txt"
} > "$BOOT/tryboot.txt"
ok "已写 $BOOT/tryboot.txt"
echo "     —— 与 config.txt 的差异："
diff <(cat "$BOOT/config.txt") <(grep -v '^kernel=kernel8-trace.img' "$BOOT/tryboot.txt" | grep -v '^# PR-10E' | grep -v '^# 由 pr10e' | grep -v '^# 与 config') \
     | sed 's/^/       /' || true

# ---------- [6/6] 收尾与人工步骤 ----------
echo "[6/6] 完成"
cat > "$BOOT/../trace-kernel-manifest.txt" 2>/dev/null <<EOF || true
image=kernel8-trace.img sha256=$IMG_SHA
config_sha256=$CFG_SHA
localversion=$VER
built=$(date -u +%Y-%m-%dT%H:%M:%SZ)
kernel8_sha256=$(sha256sum "$BOOT/kernel8.img" | awk '{print $1}')
EOF

cat <<EOF

==============================================================
 安装完成。**未重启**，等你确认。
==============================================================
下一步（人工执行，串口盯着）：

  1) 同步落盘
       sync

  2) 一次性试启动 tracing 内核
       reboot "0 tryboot"

  3) 起来后先只做功能检查（不跑实验）
       uname -r                      # 期待 5.15.86-trace
       ls /sys/kernel/tracing        # 出现即 tracefs 可用
       ls /sys/kernel/tracing/events >/dev/null && echo tracefs-ok
       which rtla || echo "rtla 未装（可用 tracefs 直接操作）"
       grep -E 'osnoise|timerlat' /sys/kernel/tracing/available_tracers

  4) 若长时间无输出/无 SSH：断电重启即回落到 kernel8.img（tryboot 是一次性的）
     仍不行则用备份镜像整分区写回：
       dd if=/root/kernel-backup-20261028-1217/p1-full.img of=/dev/mmcblk0p1 bs=1M

  5) 跑完实验后回 stable 复测（评审阶段 D 要求），防把 tracing 开销当原生延迟
==============================================================
EOF
