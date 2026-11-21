#!/usr/bin/env bash
# test_let_ready_gate__就绪门禁用例.sh — D 组宿主「计算就绪门禁」正确性用例
#
# 背景：旧实现用单个 bool kf_let_ready，任意组体跑完都置真；output_commit 只读这个布尔，
# 无法区分「谁算的 / 哪一拍 / 算完多久」，会把上一拍遗留的结果当本拍结果发布。
# 现在改成 (生产者组, 周期号, 完成时刻) 三元组 + 原子取用。
#
# 本脚本只回答一件事：**该跳过时确实跳过，且能说清为什么跳过**。
#   用例 1 正常      → 门禁全绿、版本每拍 +1
#   用例 2 生产者超时 → skip>0 且 stale>0（不是"未就绪"，是"只有旧值"）、版本不递增
#   用例 3 连续漏做   → 注入 N 拍漏做，stale 与 skip 计数与注入量一致
#
# 用法：KO=/path/to/kf_task.ko bash test_let_ready_gate__就绪门禁用例.sh
# 环境：KF_CPU（默认 3）、KF_PERIOD_US（默认 1000）、KF_FAST=1 跳过计时等待（烟测）
set -uo pipefail

KO="${KO:?需要 KO=/path/to/kf_task.ko}"
CPU="${KF_CPU:-3}"
PERIOD="${KF_PERIOD_US:-1000}"
P=/sys/module/kf_task/parameters
FAIL=0

rd() { cat "$P/$1" 2>/dev/null || echo NA; }
num() { local v; v="$(rd "$1")"; case "$v" in ''|*[!0-9-]*) echo 0 ;; *) echo "$v" ;; esac; }

load() {
  sudo rmmod kf_task 2>/dev/null
  sudo insmod "$KO" cpu="$CPU" period_us="$PERIOD" rt_priority=80 \
      let_log_cap=200000 hrtimer_hard=1 "$@" || { echo "❌ insmod 失败"; exit 1; }
}
unload() { sudo rmmod kf_task 2>/dev/null; }

check() {  # check <名称> <0|1> <实测说明>
  if [ "$2" = "1" ]; then
    echo "  ✅ $1   $3"
  else
    echo "  ❌ $1   $3"
    FAIL=$((FAIL + 1))
  fi
}

dump() {  # 打印关键计数，作为失败时的证据
  local k
  for k in periods missing_groups unpaired_finish let_skip_commit let_hold_last \
           let_ready_stale let_ready_not_ready let_ready_consumed \
           let_ready_too_old let_ready_group_mismatch let_ready_age_max_ns \
           test_prod_skipped test_skip_prod_left; do
    printf '     %-26s %s\n' "$k" "$(rd "$k")"
  done
}

echo "══ 就绪门禁用例（KO=$KO cpu=$CPU period=${PERIOD}us）══"

# ---------------------------------------------------------------- 用例 1
echo
echo "[用例 1] 正常：门禁应全绿、版本每拍 +1"
load
sleep 2
v0="$(num g_cmd_ver)"
sleep 1
v1="$(num g_cmd_ver)"
delta=$((v1 - v0))
dump
check "skip=0" "$([ "$(num let_skip_commit)" = "0" ] && echo 1 || echo 0)" "let_skip_commit=$(rd let_skip_commit)"
check "stale=0" "$([ "$(num let_ready_stale)" = "0" ] && echo 1 || echo 0)" "let_ready_stale=$(rd let_ready_stale)"
check "consumed=0" "$([ "$(num let_ready_consumed)" = "0" ] && echo 1 || echo 0)" "let_ready_consumed=$(rd let_ready_consumed)"
check "group_mismatch=0" "$([ "$(num let_ready_group_mismatch)" = "0" ] && echo 1 || echo 0)" "group_mismatch=$(rd let_ready_group_mismatch)"
check "版本速率" "$([ "$delta" -ge 900 ] && [ "$delta" -le 1100 ] && echo 1 || echo 0)" "ver 1s 增量=$delta（期望 ~1000）"
check "结果年龄落在 LET 窗口内" \
      "$([ "$(num let_ready_age_max_ns)" -lt $((PERIOD * 1000)) ] && echo 1 || echo 0)" \
      "age_max=$(rd let_ready_age_max_ns)ns（期望 < $((PERIOD * 1000))）"
unload

# ---------------------------------------------------------------- 用例 2
echo
echo "[用例 2] 生产者超时（stall=600us > 提交相位 400us）：应 skip 且判为 stale"
load test_stall_prod_us=600
sleep 2
v0="$(num g_cmd_ver)"
sleep 1
v1="$(num g_cmd_ver)"
delta=$((v1 - v0))
dump
check "skip>0" "$([ "$(num let_skip_commit)" -gt 0 ] && echo 1 || echo 0)" "let_skip_commit=$(rd let_skip_commit)"
check "stale>0（能区分旧值）" "$([ "$(num let_ready_stale)" -gt 0 ] && echo 1 || echo 0)" "let_ready_stale=$(rd let_ready_stale)"
check "版本不再递增" "$([ "$delta" -lt 100 ] && echo 1 || echo 0)" "ver 1s 增量=$delta（期望 ~0）"
check "consumed=0" "$([ "$(num let_ready_consumed)" = "0" ] && echo 1 || echo 0)" "let_ready_consumed=$(rd let_ready_consumed)"
check "group_mismatch=0" "$([ "$(num let_ready_group_mismatch)" = "0" ] && echo 1 || echo 0)" "group_mismatch=$(rd let_ready_group_mismatch)"
unload

# ---------------------------------------------------------------- 用例 3
echo
echo "[用例 3] 连续漏做 50 拍生产者：注入量、skip、stale 应互相一致"
load
sleep 1
echo 50 | sudo tee "$P/test_skip_prod_left" >/dev/null
sleep 2
dump
skipped="$(num test_prod_skipped)"
check "注入全部生效" "$([ "$skipped" = "50" ] && echo 1 || echo 0)" "test_prod_skipped=$skipped（期望 50）"
check "每次漏做都判 stale" "$([ "$(num let_ready_stale)" -ge 40 ] && echo 1 || echo 0)" "let_ready_stale=$(rd let_ready_stale)（期望 >=40）"
check "skip 与 stale 同量级" \
      "$([ "$(( $(num let_skip_commit) - $(num let_ready_stale) ))" -le 10 ] && echo 1 || echo 0)" \
      "skip=$(rd let_skip_commit) stale=$(rd let_ready_stale)"
check "consumed=0（无重复发布）" "$([ "$(num let_ready_consumed)" = "0" ] && echo 1 || echo 0)" "let_ready_consumed=$(rd let_ready_consumed)"
check "group_mismatch=0（无错组结果）" "$([ "$(num let_ready_group_mismatch)" = "0" ] && echo 1 || echo 0)" "group_mismatch=$(rd let_ready_group_mismatch)"
unload

echo
if [ "$FAIL" = "0" ]; then
  echo "══ 全部通过 ══"
else
  echo "══ 失败 $FAIL 项 ══"
fi
exit "$([ "$FAIL" = "0" ] && echo 0 || echo 1)"
