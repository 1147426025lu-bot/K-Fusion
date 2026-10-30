#!/bin/bash
# ============================================================================
# iso_ab__隔离档位短测.sh — 用 1 分钟短测比较隔离档位（用于选"最优隔离"）
# ============================================================================
# 为什么用交替重复而不是各跑一次：
#   单次 60 s 的 max 由个别事件决定，先后顺序与环境漂移会伪装成"档位差异"。
#   协议：l0 → l1 → l4 → l0 → l1 → l4（两轮交替），比较用各轮同一档位的分布，
#   而不是跨档位比单次最大值。
#
# l0 = 回退到只有启动参数（isolcpus/nohz_full），用于回答"运行时调优到底值不值"
#
# 用法: sudo bash iso_ab__隔离档位短测.sh [输出根目录]
#   env: DRIVER(默认 /tmp/soakB/driver) MINUTES(默认 1) SCHED(默认 fifo99)
# ============================================================================
set -u

OUT="${1:-/home/pi/kfusion_results/iso/ab}"
DRIVER="${DRIVER:-/tmp/soakB/driver}"
MINUTES="${MINUTES:-1}"
SCHED="${SCHED:-fifo99}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KF="$(cd "$SELF_DIR/../.." && pwd)"
APPLY="$SELF_DIR/iso_apply__隔离档位.sh"
SOAK="$KF/scripts/soak_ecos__长测.py"

[ -f "$DRIVER" ] || { echo "✗ 驱动不存在：$DRIVER" >&2; exit 2; }
[ "$(id -u)" = "0" ] || exec sudo -n bash "$0" "$@"

mkdir -p "$OUT"
for round in 1 2; do
    for lv in l0 l1 l4; do
        d="$OUT/${lv}_r${round}"
        mkdir -p "$d"
        echo "══ round $round / 档位 $lv ══"
        case "$lv" in
            l0) bash "$APPLY" teardown >/dev/null 2>&1 ;;
            l1) bash "$APPLY" teardown >/dev/null 2>&1; bash "$APPLY" l1 >/dev/null 2>&1 ;;
            l4) bash "$APPLY" teardown >/dev/null 2>&1; bash "$APPLY" l4 >/dev/null 2>&1 ;;
        esac
        python3 "$SOAK" --driver "$DRIVER" --minutes "$MINUTES" --sched "$SCHED" \
            --label "$lv" --level "$lv" --out-dir "$d" 2>&1 | sed 's/^/   /'
    done
done
echo "完成 → $OUT"
