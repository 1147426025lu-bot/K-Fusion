#!/usr/bin/env bash
# xdom_build__构建跨域两端.sh —— 在 **Pi 上**构建 PR10-C 的两端（root .ko + NuttX app）。
#
#   bash /root/xdom_build__构建跨域两端.sh /root/ecos_xdom.tgz
#
# 退出码：0 两端都构建成功｜3 构建失败
#
# 两个刻意的选择：
# 1. NuttX 端放进**已有的** fiveloop app 目录，并把 MODULE 改成
#    $(CONFIG_BENCHMARK_FIVELOOP)。生成器写的是 EXAMPLES_FIVE_STAGE_LOOP，
#    那个符号在 .config 里没开；与其改 Kconfig + 重新 context，不如复用已打开的符号。
#    （代价：app 目录名与模块名不一致，README 里写清楚了。）
#
#    ⚠ PROGNAME 一旦设成 fiveloop，这个 app 就成了 NuttX 的 builtin：
#    注册表会引用 <PROGNAME 去横线>_main = fiveloop_main，而源码里的入口
#    函数必须同名，否则链接期 undefined reference（生成器已把两者绑死，
#    这里不再另起名字）。
#
# 2. 开机入口设成 fiveloop_main（= PROGNAME 派生的注册表符号）。
#    实验完用 xipc_restore__恢复开机入口.sh 还回 NSH。
#
# ⚠ 改 .config 会让 NuttX 全树的 .depend 失效（.depend 依赖 .config），
#    于是 make 要重跑 pass1dep/pass2dep 重新生成 .ddc。-j 下两个 make
#   可能在同一目录里互删 .ddc，报 "cat: xxx.ddc: No such file"（踩过）。
#    所以下面**依赖重建用 -j1**，只有最后的链接阶段才并行。
set -u

TGZ="${1:-/root/ecos_xdom.tgz}"
SRC=/root/ecos_xdom
B=/root/kfusion-pi4/build/nuttx-build
TC=/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/bin
export PATH="$TC:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

log() { printf '[xdom] %s\n' "$*"; }
die() { printf '[xdom] ❌ %s\n' "$*" >&2; exit 3; }

[ -f "$TGZ" ] || die "找不到 $TGZ"
[ -d "$B/apps" ] || die "找不到 NuttX 树 $B/apps"

rm -rf "$SRC"; mkdir -p "$SRC"
tar xzf "$TGZ" -C "$SRC" || die "解包失败"

# ---------- ① root 端：内核模块 ----------
log "① 构建 root 端 .ko"
make -C /root/linux-5.15.86 M="$SRC/root" modules >/tmp/xdom_root.log 2>&1 \
    || { grep -E 'error|Error' /tmp/xdom_root.log | tail -15 >&2; die "root 端构建失败"; }
ls -la "$SRC/root"/*.ko >/dev/null 2>&1 || die "没产出 .ko"
log "   → $(ls "$SRC/root"/*.ko)"

# ---------- ② NuttX 端 ----------
log "② 注入 NuttX app（复用 fiveloop 目录与 CONFIG_BENCHMARK_FIVELOOP）"
APP="$B/apps/benchmarks/fiveloop"
rm -rf "$APP"; mkdir -p "$APP"
cp "$SRC"/nuttx/* "$APP"/   # 含 Makefile/Kconfig/README
# 生成器写的 MODULE 符号没开；换成已打开的 CONFIG_BENCHMARK_FIVELOOP
sed -i 's|^MODULE    = .*|MODULE    = $(CONFIG_BENCHMARK_FIVELOOP)|' "$APP/Makefile"
grep -q 'CONFIG_BENCHMARK_FIVELOOP' "$APP/Makefile" || die "Makefile 改 MODULE 失败"
# ⚠ PROGNAME 与源码里的入口名必须一致（NuttX 注册表按 PROGNAME 引用
#   <PROGNAME 去横线>_main）。生成器已把两者绑死，这里**不要**再单独改
#   入口名；要换就换 PROGNAME 并重新生成（否则链接期 undefined reference）。
PROG=$(sed -n 's|^PROGNAME  = \([A-Za-z0-9_]*\).*|\1|p' "$APP/Makefile" | head -1)
[ -n "$PROG" ] || die "Makefile 里读不出 PROGNAME"
# ⚠ 用 sed 不用 tr：tr 把 '-' 当选项，'tr - \'_\' \'_\'' 会报 extra operand
SYM=$(printf '%s' "$PROG" | sed 's/-/_/g')_main
grep -q "int ${SYM}(" "$APP/ecos_nuttx_main.c" \
    || die "源码入口名与 PROGNAME($PROG) 对不上（期望 int ${SYM}( ）"
log "   PROGNAME=$PROG → 入口符号 $SYM"
find "$APP" -name '*.o' -delete
touch "$APP"/*.c "$APP"/*.h

cd "$B/nuttx" || die "进不去 $B/nuttx"
# 开机入口：串口只能看不能敲，参数没法手输，让 app 自启动
# 入口名必须与注册表符号一致 → 用上面从 PROGNAME 派生的 $SYM
python3 - "$PWD/.config" "$SYM" <<'PYEOF'
import io, sys
p, sym = sys.argv[1], sys.argv[2]
want = {'CONFIG_INIT_ENTRYPOINT': '"%s"' % sym,
        'CONFIG_INIT_ENTRYNAME':  '"fiveloop"',
        'CONFIG_INIT_ARGS':       '"\\"autostart\\""'}
lines = io.open(p, encoding='utf-8', errors='replace').read().split('\n')
for i, ln in enumerate(lines):
    for k, v in want.items():
        if ln.startswith(k + '='):
            lines[i] = k + '=' + v
io.open(p, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines))
print('   INIT_ENTRYPOINT → %s' % sym)
PYEOF

log "③ 重建 NuttX"
# ⚠ 依赖重建用 -j1：改了 .config → 全树 .depend 失效 → 要重跑
#   pass1dep/pass2dep 重新生成 .ddc。-j 下两个 make 可能在同一目录互删
#   .ddc，报 "cat: xxx.ddc: No such file or directory"（踩过）。
make -C "$B/nuttx" -j1 >/tmp/xdom_dep.log 2>&1 \
    || { grep -E 'error|Error [0-9]' /tmp/xdom_dep.log | tail -15 >&2; die "nuttx 依赖重建失败"; }
make -C "$B/apps" TOPDIR="$B/nuttx" -j3 >/tmp/xdom_apps.log 2>&1 \
    || { grep -E 'error|Error' /tmp/xdom_apps.log | tail -15 >&2; die "apps 构建失败"; }
make -C "$B/nuttx" -j3 >/tmp/xdom_nuttx.log 2>&1 \
    || { grep -E 'error|Error' /tmp/xdom_nuttx.log | tail -15 >&2; die "nuttx 构建失败"; }
# 校验用**符号**而不是字符串：入口是函数名，strings 在 bin 里找不到它
# （第一版就是拿 strings 查函数名，结果把好好的镜像误判成旧固件）。
aarch64-none-elf-nm "$B/nuttx/nuttx" 2>/dev/null | grep -q "T ${SYM}$" \
    || die "镜像里没有 ${SYM} 符号（还是旧固件？）"
log "   ✅ nuttx.bin $(stat -c%s "$B/nuttx/nuttx.bin") B"

log "完成"
log "  root .ko : $SRC/root/$(basename "$(ls "$SRC/root"/*.ko)")"
log "  下一步："
log "    bash /root/kfusion-pi4/scripts/deploy_nuttx_cell.sh   # 起 NuttX 端"
log "    insmod $SRC/root/*.ko                                 # root 端（= 环 owner）"
log "    cat /sys/module/$(basename "$(ls "$SRC/root"/*.ko)" .ko)/parameters/g_act"
log "  实验完：bash /root/xipc_restore__恢复开机入口.sh"
