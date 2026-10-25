#!/usr/bin/env bash
# 把跨域 IPC 的最小运行时同步到 Pi4 并构建两端。
#
# 在 **Pi 上**运行（root）：
#   bash /root/xipc_build__构建跨域IPC两端.sh /root/ecos_xipc.tgz
#
# 退出码：0 两端都构建成功｜3 root 侧编译失败｜4 NuttX 侧构建失败
#
# 为什么合成一个脚本：两端必须用**同一份** ecos_xipc.h。手动 cp 两次迟早会有一边
# 落后一版，而症状是"两边都跑得动、数据就是不对"，非常难查。
set -u

TGZ="${1:-/root/ecos_xipc.tgz}"
SRC=/root/ecos_xipc
B=/root/kfusion-pi4/build/nuttx-build
TC=/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/bin
export PATH="$TC:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

log() { printf '[xipc] %s\n' "$*"; }
die() { printf '[xipc] ❌ %s\n' "$*" >&2; exit "${2:-3}"; }

[ -f "$TGZ" ] || die "找不到 $TGZ"
[ -d "$B/apps" ] || die "找不到 NuttX 构建树 $B/apps"

rm -rf "$SRC"; mkdir -p "$SRC"
tar xzf "$TGZ" -C "$SRC" || die "解包失败"

# ---------- ① root 侧：普通 Linux 用户态程序 ----------
# 头只有一份，就在 app 目录里（打包时从 kfusion/runtime/xshm/ 复制进来），
# 这里只是把它加进 include 路径，不复制第二份。
log "① 编译 root 侧（gcc）"
gcc -O2 -std=c99 -Wall -Wextra -I"$SRC/xipc" -o "$SRC/root_side" "$SRC/root_side.c" \
    || die "root 侧编译失败"
log "   → $SRC/root_side"

# ---------- ② NuttX 侧：作为 builtin app 注入 ----------
log "② 注入 NuttX app 到 $B/apps/examples/xipc"
rm -rf "$B/apps/examples/xipc"
cp -a "$SRC/xipc" "$B/apps/examples/xipc" || die "复制 app 失败"
[ -f "$B/apps/examples/xipc/ecos_xipc.h" ] || die "app 目录缺 ecos_xipc.h（同步脚本没带上）"

# ⚠ cp -a 保留 mtime，而 PC 侧打包时的时间戳与板子时钟不同源（还差 8 小时时区），
#   make 可能认为"源码没变"直接跳过编译——症状是改了代码、构建显示成功、
#   跑起来还是老行为（这一轮就中了：session 握手没生效，日志里少一行）。
#   所以强制刷新 mtime 并删掉该 app 的 .o。
find "$B/apps/examples/xipc" -name '*.o' -delete
touch "$B/apps/examples/xipc"/*.c "$B/apps/examples/xipc"/*.h

# apps/examples/Kconfig 在 NuttX 里是自动生成的，新增 app 必须手工加一行。
# 注意要插在 `endmenu # Examples` **之前**：追加到文件末尾会落在菜单外，
# 配置项照样能定义，但 menuconfig 里看不到，事后没人找得到它。
KCFG="$B/apps/examples/Kconfig"
if ! grep -q 'examples/xipc/Kconfig' "$KCFG" 2>/dev/null; then
    sed -i "\|^endmenu # Examples|i source \"$B/apps/examples/xipc/Kconfig\"" "$KCFG"
    grep -q 'examples/xipc/Kconfig' "$KCFG" || die "没能把 xipc 接进 examples/Kconfig"
    log "   已把 xipc 接进 examples/Kconfig（endmenu 之前）"
fi

# 打开配置项（.config 里没有就追加）
cd "$B/nuttx" || die "进不去 $B/nuttx"
if ! grep -q '^CONFIG_EXAMPLES_XIPC=' .config 2>/dev/null; then
    printf 'CONFIG_EXAMPLES_XIPC=y\n' >> .config
    log "   已打开 CONFIG_EXAMPLES_XIPC=y"
fi

# ⚠ 开机入口改成 xipc 自己。
#   1) fiveloop 是上一轮实验的启动 app，开机就死循环轮询 IVSHMEM 的固定地址
#      0x1faf1080 —— 那正好落在本协议的 r2i 环里。留着它既占满 CPU1，也在不停
#      读我们正在写的环。改入口而不是关掉 CONFIG_BENCHMARK_FIVELOOP，是因为关掉
#      配置项后旧 .o 还会赖在 libapps.a 里（踩过），换入口最干净。
#   2) 这块板子的串口只有 Pi→适配器一个方向（能看不能敲），所以不能靠 NSH 手输
#      参数。让 xipc 自启动，再由 root 侧用 MODE 消息在运行时切模式。
#   3) NuttX 把 CONFIG_INIT_ARGS 当成 **argv[0]**（`argv[] = { CONFIG_INIT_ARGS,
#      NULL }`），所以 argc 是 1，xipc 走默认值（忙等 + 不推 TICK）。
#
# 用 python 做精确替换：这几行含引号和反斜杠，sed 的转义层数很容易数错，
# 而"改错了但没报错"会让镜像里跑的还是老入口。
python3 - "$PWD/.config" <<'PYEOF'
import io, sys
p = sys.argv[1]
want = {
    'CONFIG_INIT_ENTRYPOINT': '"xipc_main"',
    'CONFIG_INIT_ENTRYNAME':  '"xipc"',
    'CONFIG_INIT_ARGS':       '"\\"autostart\\""',
}
lines = io.open(p, encoding='utf-8', errors='replace').read().split('\n')
changed = []
for i, ln in enumerate(lines):
    for k, v in want.items():
        if ln.startswith(k + '='):
            new = k + '=' + v
            if ln != new:
                changed.append('%s → %s' % (ln, new))
                lines[i] = new
if changed:
    io.open(p, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines))
    for c in changed:
        print('   ' + c)
else:
    print('   入口已是 xipc，无需改动')
PYEOF

log "③ 重建（apps 库 + nuttx.bin）"
make -C "$B/apps" TOPDIR="$B/nuttx" -j3 > /tmp/xipc_apps.log 2>&1 \
    || { tail -20 /tmp/xipc_apps.log >&2; die "apps 构建失败"; }
make -C "$B/nuttx" -j3 > /tmp/xipc_nuttx.log 2>&1 \
    || { grep -E 'error|Error [0-9]' /tmp/xipc_nuttx.log | tail -15 >&2; die "nuttx 构建失败"; }

# 校验：镜像里必须有**本版特有**的标记，不能用 'xipc START' 这种从第一版就有的串
# ——那样"链接还是旧镜像"会静默通过（踩过）。
if grep -qa 'xipc: session 0x' "$B/nuttx/nuttx.bin"; then
    log "   ✅ nuttx.bin 含本版 xipc（$(stat -c%s "$B/nuttx/nuttx.bin") B）"
else
    die "nuttx.bin 里没有本版标记（'xipc: session 0x'）—— 链接还是旧镜像"
fi

log "完成"
log "  root 侧：$SRC/root_side"
log "  inmate 侧：NSH 里跑 \`xipc\`"
