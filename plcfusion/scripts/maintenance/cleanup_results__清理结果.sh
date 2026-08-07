#!/bin/bash
# 删除 <15min 短测数据、greedy 探索日志、孤立 bin/png
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

delete_if_short_log() {
    local f="$1"
    local base
    base="$(basename "$f")"
    # 保留 ≥15min
    if [[ "$base" =~ _([0-9]+)min ]]; then
        local mins="${BASH_REMATCH[1]}"
        if [ "$mins" -lt 15 ] 2>/dev/null; then
            rm -f "$f"
            return 0
        fi
        return 1
    fi
    # 秒级 / quick
    if [[ "$base" =~ _([0-9]+)s\. ]] || [[ "$base" =~ quick ]] || [[ "$base" =~ _20s ]]; then
        rm -f "$f"
        return 0
    fi
    return 1
}

echo "🧹 清理 results/raw ..."
for f in results/raw/*; do
    [ -e "$f" ] || continue
    delete_if_short_log "$f" || true
done
# 短测关联 monitor/watchdog/stats
for pat in '*_1min*' '*_2min*' '*_3min*' '*_15s*' '*_20s*' '*120s*' '*quick*'; do
    rm -f results/raw/$pat 2>/dev/null || true
done

echo "🧹 清理 results/png ..."
rm -f results/png/*_1min* results/png/*_2min* results/png/*_3min* results/png/*short* 2>/dev/null || true

echo "🧹 清理 results/stability (greedy/short) ..."
rm -f results/stability/greedy_* 2>/dev/null || true
rm -f results/stability/spike_matrix_short_* results/stability/soak_matrix_short_* 2>/dev/null || true
rm -f results/stability/confirm_l2_* results/stability/env_static_* results/stability/fast_hrtimer_* 2>/dev/null || true
rm -f results/jitter_matrix_short_* 2>/dev/null || true

echo "🧹 清理 results/logs ..."
rm -rf results/logs

echo "🧹 清理误放目录 scripts/deploy/results ..."
rm -rf scripts/deploy/results

echo "🧹 清理遗留 results/png results/raw ..."
rm -rf results/png results/raw results/logs 2>/dev/null || true
rmdir results/stability 2>/dev/null || true

echo "✅ 清理完成"
