#!/bin/bash
# ============================================================================
# rt_background_load__背景加压.sh — housekeeping CPU 背景负载（hackbench）
# ============================================================================
# 用法:
#   bash scripts/tune/rt_background_load__背景加压.sh start
#   bash scripts/tune/rt_background_load__背景加压.sh stop
#   bash scripts/tune/rt_background_load__背景加压.sh status
# 环境:
#   STRESS_LOAD_CPUS=0-2
#   STRESS_HACKBENCH_LOOPS=8    # -l 线程对数
#   STRESS_HACKBENCH_PIPE=1     # -p 管道通信（更重）
#   STRESS_HACKBENCH_FIFO=0     # -f 自旋而非 FIFO（与 -p 互斥时优先 -p）
# ============================================================================
set -euo pipefail

ACTION="${1:-status}"
PIDFILE="${STRESS_LOAD_PIDFILE:-/tmp/plc_rt_stress_load.pid}"
STATEFILE="${STRESS_LOAD_STATEFILE:-/tmp/plc_rt_stress_load.state}"
STOPFILE="${STRESS_LOAD_STOPFILE:-/tmp/plc_rt_stress_load.stop}"
LOGFILE="${STRESS_LOAD_LOGFILE:-/tmp/plc_rt_stress_load.log}"

STRESS_LOAD_CPUS="${STRESS_LOAD_CPUS:-0-2}"
STRESS_HACKBENCH_LOOPS="${STRESS_HACKBENCH_LOOPS:-8}"
STRESS_HACKBENCH_PIPE="${STRESS_HACKBENCH_PIPE:-1}"
STRESS_HACKBENCH_FIFO="${STRESS_HACKBENCH_FIFO:-0}"

require_hackbench() {
    if ! command -v hackbench >/dev/null 2>&1; then
        echo "❌ 未找到 hackbench（请安装 rt-tests 或 apt install rt-tests）" >&2
        exit 1
    fi
}

load_stop() {
    local pid
    touch "$STOPFILE" 2>/dev/null || true
    if [ -f "$PIDFILE" ]; then
        pid="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            for _ in 1 2 3 4 5; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.2
            done
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$PIDFILE"
    fi
    pkill -f "taskset -c ${STRESS_LOAD_CPUS} hackbench" 2>/dev/null || true
    rm -f "$STATEFILE" "$STOPFILE"
}

load_start() {
    require_hackbench
    load_stop

    local -a args=(-l "$STRESS_HACKBENCH_LOOPS")
    if [ "$STRESS_HACKBENCH_PIPE" = "1" ]; then
        args+=(-p)
    elif [ "$STRESS_HACKBENCH_FIFO" = "1" ]; then
        args+=(-f)
    fi

    rm -f "$STOPFILE"
    echo "🔥 [STRESS] taskset -c ${STRESS_LOAD_CPUS} hackbench ${args[*]} (持续循环)"
    : > "$LOGFILE"
    (
        while [ ! -f "$STOPFILE" ]; do
            taskset -c "$STRESS_LOAD_CPUS" hackbench "${args[@]}" >>"$LOGFILE" 2>&1 || true
            [ -f "$STOPFILE" ] && break
            sleep 0.05
        done
    ) &
    echo $! >"$PIDFILE"
    printf 'tool=hackbench cpus=%s loops=%s pipe=%s fifo=%s pid=%s\n' \
        "$STRESS_LOAD_CPUS" "$STRESS_HACKBENCH_LOOPS" \
        "$STRESS_HACKBENCH_PIPE" "$STRESS_HACKBENCH_FIFO" "$(cat "$PIDFILE")" >"$STATEFILE"
    echo "✅ 背景加压已启动 pid=$(cat "$PIDFILE") log=$LOGFILE"
}

load_status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "running pid=$(cat "$PIDFILE")"
        [ -f "$STATEFILE" ] && cat "$STATEFILE"
        return 0
    fi
    echo "stopped"
    return 1
}

case "$ACTION" in
    start) load_start ;;
    stop) load_stop; echo "✅ 背景加压已停止" ;;
    status) load_status ;;
    *)
        echo "用法: $0 {start|stop|status}" >&2
        exit 2
        ;;
esac
