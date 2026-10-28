#!/usr/bin/env bash
# deploy_and_load.sh — 在**目标机**上构建/加载/验证 ECoS 融合模块
#
# 这个脚本要跑在目标机上（不在 PC 上），因为它依赖：
#   · 与运行内核匹配的内核树（KDIR）
#   · root 权限（insmod/rmmod）
#
# 用法：
#   bash deploy_and_load.sh <模块目录> [KDIR] [period_us]
#
# 例：
#   bash deploy_and_load.sh /root/ecos_build/chain_window
#   bash deploy_and_load.sh /root/ecos_build/chain_window /root/linux-5.15.86 1000
#
# 它做四件事并**逐条如实报告**：
#   1. Kbuild 构建（失败即停，不继续"假设成功"）
#   2. insmod
#   3. 读观察点（periods / 各 --digest-var 参数），再读一次算周期速率
#   4. rmmod 并确认模块消失
#
# 退出码：0 全部成功｜2 参数/环境问题｜3 构建失败｜4 加载失败｜5 卸载失败

set -u -o pipefail

MOD_DIR="${1:-}"
KDIR="${2:-/lib/modules/$(uname -r)/build}"
PERIOD="${3:-1000}"

die() { printf '[deploy] ❌ %s\n' "$1" >&2; exit "${2:-3}"; }
log() { printf '[deploy] %s\n' "$*"; }

[ -n "$MOD_DIR" ] || die "缺少模块目录（生成产物目录）" 2
[ -d "$MOD_DIR" ] || die "目录不存在：$MOD_DIR" 2
[ -d "$KDIR" ] || die "内核树不存在：$KDIR（先 prepare 一份匹配运行内核的树）" 2
[ "$(id -u)" = "0" ] || die "需要 root（insmod/rmmod）" 2

KO="$(ls "$MOD_DIR"/*.ko 2>/dev/null | head -1 || true)"

log "① Kbuild：$MOD_DIR ← $KDIR"
if [ -z "$KO" ]; then
    make -C "$KDIR" M="$MOD_DIR" modules || die "Kbuild 失败" 3
    KO="$(ls "$MOD_DIR"/*.ko | head -1)"
fi
[ -f "$KO" ] || die "构建后仍找不到 .ko" 3
MOD="$(basename "$KO" .ko)"
log "   产物：$KO"
modinfo "$KO" | grep -E '^(vermagic|description)' | sed 's/^/   /'

log "② insmod $MOD.ko period_us=$PERIOD"
insmod "$KO" "period_us=$PERIOD" || die "加载失败（Unknown symbol 多半是内核没导出该符号）" 4

SYS="/sys/module/$MOD/parameters"
log "③ 观察点"
if [ -d "$SYS" ]; then
    # 速率：先不冻结，隔 2 s 看 periods 的增量
    A="$(cat "$SYS/periods" 2>/dev/null || echo '?')"
    sleep 2
    B="$(cat "$SYS/periods" 2>/dev/null || echo '?')"
    if [ "$A" != "?" ] && [ "$B" != "?" ]; then
        log "   periods: $A -> $B（2 s 内 +$((B - A))，名义 $((PERIOD / 1000 * 2000)) 次）"
    else
        log "   ⚠ 没有 periods 参数（生成时可能没带观察点）"
    fi

    # 快照：sysfs 分几次 cat 之间会跨周期，先冻结再读，保证同一拍
    if [ -w "$SYS/frozen" ]; then
        echo 1 >"$SYS/frozen"
        sleep 0.1
        SNAP="$(cat "$SYS/periods")"
        log "   冻结快照 @ periods=$SNAP（同拍）"
        for p in "$SYS"/*; do
            n="$(basename "$p")"
            case "$n" in periods|frozen|period_us|rt_priority) continue ;; esac
            log "     $n = $(cat "$p" 2>/dev/null)"
        done
        echo 0 >"$SYS/frozen"
    else
        log "   ⚠ 没有 frozen 参数（老产物）：下面各值是分次读的，可能不同拍"
        for p in "$SYS"/*; do
            n="$(basename "$p")"
            case "$n" in periods|period_us|rt_priority) continue ;; esac
            log "     $n = $(cat "$p" 2>/dev/null)"
        done
    fi
else
    log "   ⚠ 没有 sysfs 参数目录：模块可能没带观察点，只能证明能加载"
fi

log "④ rmmod"
dmesg | tail -2 | sed 's/^/   /'
rmmod "$MOD" || die "卸载失败" 5
sleep 1
if lsmod | grep -q "^$MOD "; then
    die "rmmod 返回 0 但模块还在" 5
fi
log "✅ 完成：构建 / 加载 / 观察 / 卸载 全部成功"
