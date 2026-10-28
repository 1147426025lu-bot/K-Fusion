#!/usr/bin/env bash
# ecos_from_c__从C编译.sh — Gate 1：一份周期 C → 骨架 → DAG → 融合方案 → 用户态驱动 [→ kmod 源]
#
# 验收（方案原文）：
#   给一个新的受支持 C 周期程序，流水线自动抽出阶段与状态，
#   生成用户态驱动和 Linux 内核模块源；不手写 DAG、不复制第二份计算体。
#
# 用法：
#   bash kfusion/scripts/ecos_from_c__从C编译.sh \
#        kfusion/examples/ecos/five_stage_loop__闭环五阶段示例.c
#
# 环境变量：
#   CLANG OPT PYTHON  OUT_DIR  BIND (默认 setpoint=1000)
#   SKIP_HOST=1  只生成不编译用户态
#   CYCLES      用户态试跑周期数（默认 80，避免自测睡太久）
set -euo pipefail

KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${1:?usage: ecos_from_c__从C编译.sh <periodic.c>}"
SRC="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"
CLANG="${CLANG:-clang-16}"
OPT="${OPT:-opt-16}"
PY="${PYTHON:-python3}"
SO="${PLCEFFECTSUMMARY_SO:-$KF/analysis/build/PLCEffectSummary.so}"
CAPS="$KF/contracts/examples/platform_caps.json"
PROBE="$KF/include/plc_probe__统一测量探针.h"
EXTRACT="$KF/frontend/stage_extract/stage_extract.py"
OUT="${OUT_DIR:-$KF/analysis/build/gate1_from_c}"
BIND="${BIND:-setpoint=1000}"
CYCLES="${CYCLES:-80}"
PLATFORM="${PLATFORM:-linux_kernel}"

step() { echo; echo "──── $* ────"; }
die() { echo "❌ $*"; exit "${2:-1}"; }

mkdir -p "$OUT"

command -v "$CLANG" >/dev/null || die "缺 $CLANG" 2
command -v "$OPT" >/dev/null || die "缺 $OPT" 2
command -v "$PY" >/dev/null || die "缺 $PY" 2
[ -f "$SO" ] || die "缺 $SO（先编 PLCEffectSummary.so）" 2
[ -f "$SRC" ] || die "缺源 $SRC" 2
[ -f "$EXTRACT" ] || die "缺 $EXTRACT" 2

step "1) Clang AST → 阶段骨架（替代手写 stages.json / dag.json）"
"$PY" "$EXTRACT" "$SRC" \
  --clang "$CLANG" \
  --bind "$BIND" \
  -o "$OUT/stages.json" || {
    echo "抽取失败（K-C0 fail-closed 是预期行为之一）"
    exit 2
  }

step "2) C → LLVM IR（禁内联，保留阶段函数）"
"$CLANG" -S -emit-llvm -O2 -fno-inline -fno-inline-functions -fno-builtin \
  "$SRC" -o "$OUT/task.ll" || die "emit-llvm 失败" 3

step "3) IR → EffectSummary（效果不是人写的）"
PLC_EFFECT_SUMMARY_OUT="$OUT/effect_summary.json" \
  "$OPT" -load-pass-plugin "$SO" -passes=plc-effect-summary \
  "$OUT/task.ll" -S -o /dev/null || die "effect-summary 失败" 4
[ -f "$OUT/effect_summary.json" ] || die "未写出 effect_summary.json" 4

step "4) 摘要 + 自动骨架 → 契约"
"$PY" "$KF/scripts/effect_to_contract__摘要转契约.py" \
  --summary "$OUT/effect_summary.json" \
  --skeleton "$OUT/stages.json" \
  --out "$OUT/task.contract.json" || die "契约合成失败" 5

step "5) 契约门禁（$PLATFORM）"
"$PY" "$KF/scripts/contract_check__契约校验.py" "$OUT/task.contract.json" \
  --platforms "$CAPS" --platform "$PLATFORM" || die "契约拒绝 $PLATFORM" 6

step "6) DAG（数据边来自 IR，相位来自骨架）"
"$PY" "$KF/scripts/dag_build__任务DAG生成.py" "$OUT/effect_summary.json" \
  --stages "$OUT/stages.json" \
  -o "$OUT/task.dag.json" --dot "$OUT/task.dag.dot" || die "dag_build 失败" 7

step "6b) TEIR（状态版本 + 时间事件 + 允许域）"
"$PY" "$KF/ir/temporal_effect_ir.py" \
  --dag "$OUT/task.dag.json" \
  --summary "$OUT/effect_summary.json" \
  --stages "$OUT/stages.json" \
  --platforms "$CAPS" \
  -o "$OUT/task.teir.json" || die "TEIR 失败" 7

step "7) 单域融合规划（对照基线，不直接驱动生成）"
"$PY" "$KF/scripts/fusion_plan__融合规划.py" "$OUT/task.dag.json" \
  --platforms "$CAPS" --platform "$PLATFORM" \
  --release-cost-us 21 --quiet \
  -o "$OUT/plan.planner.json" || die "fusion_plan 失败" 8

step "7c) 选择性内核化 A/B/C/D（入选方案才是生成输入）"
"$PY" "$KF/scripts/ecos/candidate_generate.py" \
  --teir "$OUT/task.teir.json" \
  --c-user-us "${C_USER_US:-21}" \
  --c-kernel-us "${C_KERNEL_US:-21}" \
  --kernel-extra-us "${KERNEL_EXTRA_US:-0}" \
  -o "$OUT/candidates.json" \
  --chosen-plan "$OUT/chosen.plan.json" || die "A/B/C/D 无合法候选" 8

CHOSEN="$("$PY" -c "import json; print(json.load(open('$OUT/candidates.json'))['chosen'] or '')")"
DOMAIN="$("$PY" -c "import json; print((json.load(open('$OUT/candidates.json')).get('chosen_record') or {}).get('domain') or '')")"
echo "  入选 $CHOSEN  域 $DOMAIN"
if [ -z "$CHOSEN" ]; then
  die "无入选候选" 8
fi
if [ "$DOMAIN" = "mixed" ]; then
  die "混合部署尚未打通跨域数据交接，拒绝标记为可部署（选型 $CHOSEN）" 8
fi
cp "$OUT/chosen.plan.json" "$OUT/plan.json"

step "7b) 独立验证器（验证入选方案，不调用规划器）"
"$PY" "$KF/verify/plan_verify.py" \
  --teir "$OUT/task.teir.json" --plan "$OUT/plan.json" \
  -o "$OUT/verify.json" || die "TEIR 验证器拒绝入选方案" 8

step "7b2) DAG 全局验证器（写回 verification，供 emit 门禁）"
"$PY" "$KF/scripts/ecos/plan_verify.py" \
  --dag "$OUT/task.dag.json" --plan "$OUT/plan.json" \
  --platforms "$CAPS" --platform "$DOMAIN" \
  --stamp-plan -o "$OUT/verify_dag.json" || die "DAG 验证器拒绝入选方案" 8

"$PY" - "$OUT/candidates.json" "$OUT/deploy.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
rec = d.get("chosen_record") or {}
doc = {
    "chosen": d.get("chosen"),
    "domain": rec.get("domain"),
    "fused": rec.get("fused"),
    "cost_us": (rec.get("cost") or {}).get("total_us"),
    "why": rec.get("why"),
    "deployable": rec.get("domain") in ("linux_userspace", "linux_kernel"),
}
json.dump(doc, open(sys.argv[2], "w"), ensure_ascii=False, indent=2)
print("  deploy.json chosen=%s domain=%s" % (doc["chosen"], doc["domain"]))
PY

if [ -n "${TIME_CONTRACT:-}" ] && [ -f "${TIME_CONTRACT}" ]; then
  step "7d) 按需 LET（有时间契约才启用）"
  "$PY" "$KF/scripts/ecos/let_decide.py" \
    --teir "$OUT/task.teir.json" --time "$TIME_CONTRACT" \
    --c-us "${C_USER_US:-21}" \
    --let-event-us "${LET_EVENT_US:-21}" \
    -o "$OUT/let.json" || die "LET 决策无合法候选" 8
fi

# 从骨架拼 --call / --digest-var，避免驱动里再手写阶段表
CALLS=()
DIGEST=()
EXPECT=""
eval "$("$PY" - "$OUT/stages.json" <<'PY'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
calls = d.get("calls") or {}
for name, expr in calls.items():
    print("CALLS+=(--call %r)" % ("%s=%s" % (name, expr.replace(" ", ""))))
for g in d.get("globals_digest") or d.get("globals") or []:
    print("DIGEST+=(--digest-var %r)" % g)
order = ",".join(s["name"] for s in d.get("stages") or [])
print("EXPECT=%s" % order)
period = d.get("task", {}).get("period_us") or 1000
step = d.get("task", {}).get("step_us") or 200
print("PERIOD=%s" % period)
print("STEP=%s" % step)
src = d.get("source")
print("STAGE_SRC=%s" % src)
PY
)"

step "8) 生成用户态驱动（#include 原计算体，不复制）"
"$PY" "$KF/scripts/fusion_codegen__生成融合代码.py" "$OUT/plan.json" \
  -o "$OUT/driver.c" \
  --stage-source "$STAGE_SRC" \
  --probe-header "$PROBE" \
  --period-us "$PERIOD" --step-us "$STEP" \
  --cycles "$CYCLES" \
  --expect-order "$EXPECT" \
  "${CALLS[@]}" "${DIGEST[@]}" || die "codegen 失败" 9

EMIT_DIGEST=()
eval "$("$PY" - "$OUT/stages.json" <<'PY'
import io, json, sys, re
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
for g in d.get("globals") or []:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", g):
        print("EMIT_DIGEST+=(--digest-var %s)" % g)
PY
)"

if [ "$DOMAIN" = "linux_kernel" ]; then
  step "8b) hrtimer 内核宿主（入选域=linux_kernel）"
  "$PY" "$KF/backend/synthesis/emit.py" \
    --plan "$OUT/plan.json" --dag "$OUT/task.dag.json" \
    --source "$STAGE_SRC" \
    --out-dir "$OUT/kmod_hrtimer" \
    --target linux-kernel \
    --name five_stage_loop \
    "${CALLS[@]}" "${EMIT_DIGEST[@]}" || die "emit linux-kernel 失败" 9
  cp "$STAGE_SRC" "$OUT/kmod_hrtimer/stage_impl.c"
  KDIR="${KERNELDIR:-/root/linux-5.15.86}"
  if [ "${SKIP_KMOD:-0}" != "1" ] && [ -d "$KDIR" ]; then
    make -C "$KDIR" M="$OUT/kmod_hrtimer" modules || echo "⚠ 内核模块构建失败（源码仍保留）"
  else
    echo "跳过 .ko 构建（无 KERNELDIR 或 SKIP_KMOD=1）"
  fi
else
  step "8b) 入选用户态：不生成内核部署产物"
  rm -rf "$OUT/kmod_hrtimer"
  echo "  skip kmod_hrtimer（chosen=$CHOSEN domain=$DOMAIN）"
fi

if [ -n "${TIME_CONTRACT:-}" ] && [ -f "${TIME_CONTRACT}" ] && [ -f "$OUT/let.json" ]; then
  step "8c) LET 作业表（plc_let，钉扎快照/提交；由 let_decide 选型）"
  "$PY" - "$OUT/let.json" "$OUT/plan_let.json" <<'PY'
import json, sys
let = json.load(open(sys.argv[1]))
rec = let.get("chosen_record") or {}
plan = rec.get("plan")
if not plan:
    raise SystemExit("let.json 无 chosen plan")
json.dump(plan, open(sys.argv[2], "w"), ensure_ascii=False, indent=2)
print("LET chosen=%s uses_let=%s" % (
    let.get("chosen"), (rec.get("cost") or {}).get("uses_let")))
PY
  "$PY" "$KF/scripts/ecos/plan_verify.py" \
    --dag "$OUT/task.dag.json" --plan "$OUT/plan_let.json" \
    --platforms "$CAPS" --platform linux_userspace \
    --stamp-plan -o "$OUT/verify_let_dag.json" || die "LET 方案 DAG 验证失败" 8
  "$PY" "$KF/backend/synthesis/emit.py" \
    --plan "$OUT/plan_let.json" --dag "$OUT/task.dag.json" \
    --source "$STAGE_SRC" \
    --out-dir "$OUT/let_host" \
    --target let \
    --name five_stage_loop \
    "${CALLS[@]}" "${EMIT_DIGEST[@]}" || die "emit let 失败" 9
  cp "$STAGE_SRC" "$OUT/let_host/stage_impl.c" 2>/dev/null || true
fi

if [ "${SKIP_HOST:-0}" != "1" ]; then
  step "9) 宿主编译并短跑 plan/flat（digest 必须相同）"
  gcc -O2 -std=gnu11 -Wall -Werror -I"$KF/include" \
    "$OUT/driver.c" -o "$OUT/driver" -lrt || die "gcc 失败" 10
  "$OUT/driver" plan "$CYCLES" | tee "$OUT/driver.plan.log"
  "$OUT/driver" flat "$CYCLES" | tee "$OUT/driver.flat.log"
  cat "$OUT/driver.plan.log" "$OUT/driver.flat.log" > "$OUT/driver.log"
  "$PY" - "$OUT/driver.log" <<'PY' || die "digest 对照失败" 12
import sys, re
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
digs = re.findall(r"# digest: ([0-9a-f]+)", text)
if len(digs) < 2:
    print("!! 没有两条 digest（plan/flat）", file=sys.stderr)
    sys.exit(11)
if digs[0] != digs[1]:
    print("!! digest 不一致: %s vs %s —— 融合改变了采样序列" % (digs[0], digs[1]),
          file=sys.stderr)
    sys.exit(12)
print("digest match:", digs[0])
PY
else
  echo "跳过宿主编译（SKIP_HOST=1）"
fi

echo
echo "✅ 从 C 到 A/B/C/D 流水线完成"
echo "   骨架  $OUT/stages.json"
echo "   DAG   $OUT/task.dag.json"
echo "   TEIR  $OUT/task.teir.json"
echo "   入选  $OUT/chosen.plan.json  ($CHOSEN / $DOMAIN)"
echo "   部署  $OUT/deploy.json"
echo "   方案  $OUT/plan.json"
echo "   验证  $OUT/verify.json"
echo "   选型  $OUT/candidates.json"
if [ -f "$OUT/let.json" ]; then
  echo "   LET   $OUT/let.json"
fi
echo "   用户态 $OUT/driver.c"
if [ "$DOMAIN" = "linux_kernel" ]; then
  echo "   kmod   $OUT/kmod_hrtimer/"
else
  echo "   kmod   （未生成：入选用户态）"
fi
echo "   计算体始终是 $SRC ，没有第二份手写阶段函数"
