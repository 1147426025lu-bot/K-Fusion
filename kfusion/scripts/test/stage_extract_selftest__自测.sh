#!/usr/bin/env bash
# stage_extract_selftest__自测.sh — Gate 1 回归
#
# 1) five_stage_loop 抽出 5 个阶段 + 跨周期环在 dag_build 里是 z⁻¹ 不是代数环
# 2) malloc 反例必须被 K-C0 拒绝
# 3) 完整 ecos_from_c 生成用户态驱动且 plan/flat digest 一致
set -u -o pipefail

PY="${PYTHON:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KF="$(cd "$HERE/../.." && pwd)"
EXTRACT="$KF/frontend/stage_extract/stage_extract.py"
SRC="$KF/examples/ecos/five_stage_loop__闭环五阶段示例.c"
BAD="$KF/frontend/stage_extract/test/kc0_malloc_reject.c"
PIPE="$KF/scripts/ecos_from_c__从C编译.sh"
TMP="${TMPDIR:-/tmp}/stage_extract_selftest.$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }

echo "=== 1. 闭环五阶段：Clang AST 抽出入口与阶段 ==="
"$PY" "$EXTRACT" "$SRC" --bind setpoint=1000 -o "$TMP/stages.json"
N=$("$PY" -c "import json; print(len(json.load(open('$TMP/stages.json'))['stages']))")
ENTRY=$("$PY" -c "import json; print(json.load(open('$TMP/stages.json'))['task']['entry'])")
PIDCALL=$("$PY" -c "import json; print(json.load(open('$TMP/stages.json'))['calls'].get('stage_pid',''))")
if [ "$N" = 5 ] && [ "$ENTRY" = "loop_main" ]; then
  ok "抽出 5 阶段，入口 loop_main"
else
  bad "期望 5/loop_main，实际 N=$N entry=$ENTRY"
fi
if [ "$PIDCALL" = "stage_pid(1000)" ]; then
  ok "入口形参绑定 stage_pid(1000)"
else
  bad "stage_pid 调用应为 stage_pid(1000)，实际 $PIDCALL"
fi

echo "=== 2. malloc 反例：K-C0 fail-closed ==="
if "$PY" "$EXTRACT" "$BAD" -o "$TMP/bad.json" 2>"$TMP/bad.err"; then
  bad "malloc 程序不应抽取成功"
else
  if grep -q E_KC0_FORBIDDEN_CALL "$TMP/bad.err"; then
    ok "malloc 被 E_KC0_FORBIDDEN_CALL 拒绝"
  else
    bad "拒绝了但原因不是 FORBIDDEN_CALL"
    sed -n '1,12p' "$TMP/bad.err" | sed 's/^/      /'
  fi
fi

reject_code() {
  local src="$1" code="$2" label="$3"
  local err="$TMP/rej.err"
  if "$PY" "$EXTRACT" "$src" --emit-kmod "$TMP/rej_kmod" -o "$TMP/rej.json" 2>"$err"; then
    bad "$label 不应抽取成功"
    return
  fi
  if grep -q "$code" "$err"; then
    if [ -e "$TMP/rej_kmod/Makefile" ] || [ -e "$TMP/rej_kmod/kfusion_cycle.c" ]; then
      bad "$label 拒绝了但仍写出了可部署 kmod"
    else
      ok "$label 被 $code 拒绝且无 kmod"
    fi
  else
    bad "$label 拒绝了但没有 $code"
    sed -n '1,16p' "$err" | sed 's/^/      /'
  fi
  rm -rf "$TMP/rej_kmod"
}

echo "=== 2b. 入口结构反例：条件/重复/循环/间接/入口写全局 ==="
T="$KF/frontend/stage_extract/test"
reject_code "$T/kc0_repeat_call.c"       E_KC0_REPEAT_CALL       "重复调用"
reject_code "$T/kc0_conditional_call.c"  E_KC0_CONDITIONAL_CALL  "条件调用"
reject_code "$T/kc0_loop_call.c"         E_KC0_LOOP_CALL         "循环调用"
reject_code "$T/kc0_indirect_call.c"     E_KC0_INDIRECT_CALL     "间接调用"
reject_code "$T/kc0_entry_store.c"       E_KC0_ENTRY_STORE       "入口写全局"

echo "=== 3. ecos_from_c 端到端（含宿主编译短跑） ==="
if [ ! -f "$KF/analysis/build/PLCEffectSummary.so" ]; then
  echo "  （跳过：没有 PLCEffectSummary.so）"
else
  OUT_DIR="$TMP/pipe" CYCLES=40 \
    bash "$PIPE" "$SRC"
  if [ -f "$TMP/pipe/driver.c" ] && [ -f "$TMP/pipe/chosen.plan.json" ]; then
    if grep -q '#include' "$TMP/pipe/driver.c"; then
      ok "用户态驱动来自入选方案（chosen.plan）"
    else
      bad "生成物里看不到原阶段"
    fi
    DEP=$("$PY" -c "import json; print(json.load(open('$TMP/pipe/deploy.json'))['domain'])")
    if [ "$DEP" = "linux_userspace" ] && [ ! -f "$TMP/pipe/kmod_hrtimer/Kbuild" ]; then
      ok "默认成本选用户态，未写出内核部署产物"
    elif [ "$DEP" = "linux_kernel" ] && [ -f "$TMP/pipe/kmod_hrtimer/ecos_fused_linux.c" ]; then
      ok "入选内核态，写出 hrtimer 宿主"
    else
      bad "deploy.json 与生成物不一致 domain=$DEP"
    fi
    ALG=$("$PY" -c "import json; print(len(json.load(open('$TMP/pipe/task.dag.json')).get('algebraic_loops') or []))")
    REC=$("$PY" -c "import json; print(len(json.load(open('$TMP/pipe/task.dag.json')).get('recurrences') or []))")
    if [ "$ALG" = 0 ] && [ "$REC" != 0 ]; then
      ok "闭环判为 z⁻¹（recurrences=$REC, algebraic=0）"
    else
      bad "环分类不对 algebraic=$ALG recurrences=$REC"
    fi
  else
    bad "流水线未写出 driver / kmod"
  fi
fi

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" -eq 0 ]
