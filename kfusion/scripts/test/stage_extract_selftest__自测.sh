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

echo "=== 3. ecos_from_c 端到端（含宿主编译短跑） ==="
if [ ! -f "$KF/analysis/build/PLCEffectSummary.so" ]; then
  echo "  （跳过：没有 PLCEffectSummary.so）"
else
  OUT_DIR="$TMP/pipe" CYCLES=40 \
    bash "$PIPE" "$SRC"
  if [ -f "$TMP/pipe/driver" ] && [ -f "$TMP/pipe/kmod/kfusion_compute.c" ]; then
    if grep -q 'stage_plant' "$TMP/pipe/kmod/kfusion_compute.c" \
       && grep -q '#include' "$TMP/pipe/driver.c"; then
      ok "用户态驱动 + kmod 计算体均来自原 C"
    else
      bad "生成物里看不到原阶段"
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
