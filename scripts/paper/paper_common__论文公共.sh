#!/bin/bash
# ============================================================================
# paper_common__论文公共.sh — 论文实验共享函数
# ============================================================================
paper_root() {
    cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

paper_results_dir() {
    local sub="${1:-paper}"
    local d
    d="$(paper_root)/results/paper"
    mkdir -p "$d/$sub"
    echo "$d/$sub"
}

paper_csv_header() {
    local f="$1"
    if [ ! -f "$f" ]; then
        echo "run_id,timestamp,baseline,measure_kind,isolation,run_idx,duration_min,abs_max_ns,min_ns,max_ns,cycles,spike_resync,exit_code,raw_log,notes" >"$f"
    fi
}

paper_parse_abs_max() {
    local log="$1"
    local abs_max min_ns max_ns cycles resync
    abs_max=$(grep -E 'BaselineSummary:|FusedSummary:|Max:' "$log" 2>/dev/null | tail -1 | sed -n 's/.*abs_max_ns=\([0-9]*\).*/\1/p')
    if [ -z "$abs_max" ]; then
        abs_max=$(grep -E 'Max:' "$log" 2>/dev/null | tail -1 | sed -n 's/.*Max:[[:space:]]*\([0-9]*\).*/\1/p')
    fi
    if [ -z "$abs_max" ]; then
        min_ns=$(grep -E 'FusedSummary:|BaselineSummary:' "$log" | tail -1 | sed -n 's/.*min_ns=\(-[0-9]*\).*/\1/p')
        max_ns=$(grep -E 'FusedSummary:|BaselineSummary:' "$log" | tail -1 | sed -n 's/.*max_ns=\([0-9]*\).*/\1/p')
        if [ -n "$min_ns" ] && [ -n "$max_ns" ]; then
            abs_max=$max_ns
            if [ "${min_ns#-}" -gt "$abs_max" ] 2>/dev/null; then
                abs_max="${min_ns#-}"
            fi
        fi
    fi
    cycles=$(grep -E 'cycles=' "$log" 2>/dev/null | tail -1 | sed -n 's/.*cycles=\([0-9]*\).*/\1/p')
    resync=$(grep -oE 'spike_resync=[0-9]+' "$log" 2>/dev/null | tail -1 | sed 's/spike_resync=//' || true)
    echo "${abs_max:-}|${min_ns:-}|${max_ns:-}|${cycles:-0}|${resync:-0}"
}

paper_append_csv() {
    local csv="$1" run_id="$2" baseline="$3" kind="$4" iso="$5" idx="$6" mins="$7"
    local log="$8" rc="$9" notes="${10:-}"
    local parsed abs_max min_ns max_ns cycles resync
    IFS='|' read -r abs_max min_ns max_ns cycles resync <<< "$(paper_parse_abs_max "$log")"
    echo "$(date -Iseconds),${run_id},${baseline},${kind},${iso},${idx},${mins},${abs_max:-},${min_ns:-},${max_ns:-},${cycles:-},${resync:-},${rc},${log},${notes}" >>"$csv"
}

paper_env_setup() {
    local kind="${1:-soak}"
    local _saved_script_dir="${SCRIPT_DIR:-}"
    local deploy
    deploy="$(dirname "${BASH_SOURCE[0]}")/../deploy"
    if [ "$kind" = "stress" ]; then
        export PLC_PROFILE="$deploy/profiles/profile_stress_l2__背景加压.env.sh"
        export MEASURE_KIND=stress
    else
        export PLC_PROFILE="$deploy/profiles/profile_soak_l2_best__安静浸泡.env.sh"
        export MEASURE_KIND=soak
    fi
    # shellcheck source=../deploy/env_setup__测量环境.sh
    source "$deploy/env_setup__测量环境.sh"
    [ -n "$_saved_script_dir" ] && SCRIPT_DIR="$_saved_script_dir"
    env_setup_host >/dev/null 2>&1 || true
    env_mark_setup_done
    env_pre_measure_idle "${PRE_IDLE_SEC:-60}"
}

paper_env_teardown() {
    local deploy
    deploy="$(dirname "${BASH_SOURCE[0]}")/../deploy"
    # shellcheck source=../deploy/env_setup__测量环境.sh
    source "$deploy/env_setup__测量环境.sh"
    env_teardown_host
}

paper_stress_start() {
    [ "${STRESS_LOAD_ENABLE:-0}" = "1" ] || return 0
    bash "$(dirname "${BASH_SOURCE[0]}")/../tune/rt_background_load__背景加压.sh" start
}

paper_stress_stop() {
    bash "$(dirname "${BASH_SOURCE[0]}")/../tune/rt_background_load__背景加压.sh" stop 2>/dev/null || true
}
