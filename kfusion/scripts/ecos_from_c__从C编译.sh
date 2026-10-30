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
#   DEPLOY_OBJECTIVE  实测调度画像 sched_profile.json；给了就按 J(d,s) 选部署，
#                     并把选中的调度类写进 deploy.json。排不出名则 fail-closed
#                     拒绝生成（不退回模型排序）。
#   OBJ_ALPHA/BETA/GAMMA  目标函数权重（默认 1 / 0 / 0）
#   OBJ_MISS_BOUND        point|upper（默认 point）
#   OBJ_SCHEDS            参与决策的调度类，默认 cfs,fifo99
#   APPLY_SCHED=1         试跑时按选中的调度类加 chrt 前缀（默认 0，只记录不改行为）
#   CALIBRATION           PR-18B calibration json；给了只做 shadow 记账
#   APPLY_CALIBRATION=1   校准重选 accepted 才改 chosen（默认 0）
#   CALIBRATION_LOAD      校准匹配的负载（默认 0；不得用静默残差套加压）
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

step "5) 契约门禁（linux_userspace 为底线；内核是否可行交给选型）"
"$PY" "$KF/scripts/contract_check__契约校验.py" "$OUT/task.contract.json" \
  --platforms "$CAPS" --platform linux_userspace || die "契约拒绝 linux_userspace" 6
if [ "$PLATFORM" = "linux_kernel" ]; then
  if "$PY" "$KF/scripts/contract_check__契约校验.py" "$OUT/task.contract.json" \
       --platforms "$CAPS" --platform linux_kernel; then
    echo "  内核域契约可通过（是否部署仍由 A/B/C/D 决定）"
  else
    echo "  ⚠ 内核域契约未过：C/D 将非法，选型只能留用户态"
  fi
fi

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
"$PY" "$KF/scripts/ecos/pin_teir.py" "$OUT/task.teir.json" "$SRC" || die "pin TEIR 失败" 7

step "7) 单域融合规划（对照报告；失败不阻断 STKP）"
if "$PY" "$KF/scripts/fusion_plan__融合规划.py" "$OUT/task.dag.json" \
     --platforms "$CAPS" --platform "${PLATFORM:-linux_kernel}" \
     --release-cost-us 21 --quiet \
     -o "$OUT/plan.planner.json"; then
  echo "  写出对照 $OUT/plan.planner.json"
else
  echo "  ⚠ fusion_plan 对照失败（常见于内核域非法），继续 STKP / 用户态"
  rm -f "$OUT/plan.planner.json"
fi

step "7c) A–F 对照基线（失败不阻断 STKP）"
if ! "$PY" "$KF/scripts/ecos/candidate_generate.py" \
     --teir "$OUT/task.teir.json" \
     --c-user-us "${C_USER_US:-21}" \
     --c-kernel-us "${C_KERNEL_US:-21}" \
     --kernel-extra-us "${KERNEL_EXTRA_US:-0}" \
     -o "$OUT/candidates.json"; then
  echo "  ⚠ A–F 基线失败，STKP 仍独立求解"
fi

step "7c2) STKP 穷举求解（入选方案才是生成输入）"
STKP_ARGS=(
  --teir "$OUT/task.teir.json"
  --dag "$OUT/task.dag.json"
  --platforms "$CAPS"
  --c-user-us "${C_USER_US:-21}"
  --c-kernel-us "${C_KERNEL_US:-21}"
  --kernel-extra-us "${KERNEL_EXTRA_US:-0}"
  --handoff-cost-us "${HANDOFF_US:-0}"
  -o "$OUT/stkp.json"
  --chosen-plan "$OUT/chosen.plan.json"
)
if [ -n "${TIME_CONTRACT:-}" ] && [ -f "${TIME_CONTRACT}" ]; then
  STKP_ARGS+=(--contract "$TIME_CONTRACT")
fi
if [ -n "${STAGE_COST:-}" ] && [ -f "${STAGE_COST}" ]; then
  STKP_ARGS+=(--stage-cost "$STAGE_COST")
fi
if [ -n "${IOCTL_COST:-}" ] && [ -f "${IOCTL_COST}" ]; then
  STKP_ARGS+=(--ioctl-cost "$IOCTL_COST")
fi
if [ -n "${DEPLOY_OBJECTIVE:-}" ] && [ -f "${DEPLOY_OBJECTIVE}" ]; then
  STKP_ARGS+=(--deploy-objective "$DEPLOY_OBJECTIVE"
              --objective-alpha "${OBJ_ALPHA:-1}"
              --objective-beta "${OBJ_BETA:-0}"
              --objective-gamma "${OBJ_GAMMA:-0}"
              --objective-miss-bound "${OBJ_MISS_BOUND:-point}"
              --objective-scheds "${OBJ_SCHEDS:-cfs,fifo99}"
              --objective-out "$OUT/deploy_objective.json")
fi
if [ -n "${CALIBRATION:-}" ] && [ -f "${CALIBRATION}" ]; then
  STKP_ARGS+=(--calibration "$CALIBRATION"
              --calibration-load "${CALIBRATION_LOAD:-0}"
              --feedback-out "$OUT/feedback_shadow.json")
  if [ "${APPLY_CALIBRATION:-0}" = "1" ]; then
    STKP_ARGS+=(--apply-calibration)
  fi
  if [ "${CALIBRATION_FALLBACK:-0}" = "1" ]; then
    STKP_ARGS+=(--calibration-fallback)
  fi
fi
if [ -n "${FORCE_CHOSEN:-}" ]; then
  STKP_ARGS+=(--force-chosen "$FORCE_CHOSEN")
fi
"$PY" "$KF/scripts/ecos_plan_joint.py" "${STKP_ARGS[@]}" || {
  CLAIM="$("$PY" -c "import json; print(json.load(open('$OUT/stkp.json')).get('claim') or '')" 2>/dev/null || true)"
  if [ "$CLAIM" = "objective_fail_closed" ]; then
    die "部署目标 fail-closed：没有 (部署, 调度类) 同时具备实测 Q99 与 R_miss，拒绝生成（见 $OUT/deploy_objective.json）" 8
  fi
  die "STKP 无 deployment_ready 方案" 8
}

CLAIM="$("$PY" -c "import json; print(json.load(open('$OUT/stkp.json')).get('claim') or '')")"
if [ "$CLAIM" = "objective_fail_closed" ]; then
  die "部署目标 fail-closed：没有 (部署, 调度类) 同时具备实测 Q99 与 R_miss，拒绝生成（见 $OUT/deploy_objective.json）" 8
fi

CHOSEN="$("$PY" -c "import json; print(json.load(open('$OUT/stkp.json'))['chosen'] or '')")"
DOMAIN="$("$PY" -c "import json; print((json.load(open('$OUT/stkp.json')).get('chosen_record') or {}).get('domain') or '')")"
SCHED_CLASS="$("$PY" -c "import json; print(json.load(open('$OUT/stkp.json')).get('sched_class') or '')")"
echo "  STKP 入选 $CHOSEN  域 $DOMAIN  调度类 ${SCHED_CLASS:-默认为 CFS}"
if [ -z "$CHOSEN" ]; then
  die "无入选候选" 8
fi
ADAPTER="$("$PY" -c "import json; print((json.load(open('$OUT/stkp.json')).get('chosen_record') or {}).get('plan',{}).get('meta',{}).get('xdom_adapter') or '')")"
if [ "$DOMAIN" = "mixed" ] && [ "$ADAPTER" != "uku_ioctl" ]; then
  die "混合形态 $CHOSEN 无 UKU ioctl 适配器，拒绝部署" 8
fi
cp "$OUT/chosen.plan.json" "$OUT/plan.json"

step "7b) 独立验证器（验证入选方案，不调用规划器）"
"$PY" "$KF/verify/plan_verify.py" \
  --teir "$OUT/task.teir.json" --plan "$OUT/plan.json" \
  -o "$OUT/verify.json" || die "TEIR 验证器拒绝入选方案" 8

step "7b2) DAG 全局验证器（写回 verification，供 emit 门禁）"
DAG_PLAT="$DOMAIN"
if [ "$DOMAIN" = "mixed" ]; then
  DAG_PLAT=linux_userspace
fi
DAG_V=(
  --dag "$OUT/task.dag.json" --plan "$OUT/plan.json"
  --platforms "$CAPS" --platform "$DAG_PLAT"
  --stamp-plan -o "$OUT/verify_dag.json"
)
if [ -n "${TIME_CONTRACT:-}" ] && [ -f "${TIME_CONTRACT}" ]; then
  DAG_V+=(--contract "$TIME_CONTRACT")
fi
"$PY" "$KF/scripts/ecos/plan_verify.py" "${DAG_V[@]}" \
  || die "DAG 验证器拒绝入选方案" 8

"$PY" - "$OUT/stkp.json" "$OUT/deploy.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
rec = d.get("chosen_record") or {}
obj = d.get("deploy_objective") or {}
sched = d.get("sched_class")
# 调度类是部署的一部分：CFS→FIFO99 在同一 binary 上实测差 51µs（PR-11）。
# 只写进报告不给运行命令，等于决策没落地。
prefix = ["chrt", "-f", "99"] if sched == "fifo99" else []
doc = {
    "chosen": d.get("chosen"),
    "domain": rec.get("domain"),
    "fused": rec.get("fused"),
    "cost_us": (rec.get("cost") or {}).get("total_us"),
    "why": rec.get("why"),
    "adapter": (rec.get("plan") or {}).get("meta", {}).get("xdom_adapter"),
    "deployable": rec.get("domain") in ("linux_userspace", "linux_kernel")
        or (rec.get("plan") or {}).get("meta", {}).get("xdom_adapter") == "uku_ioctl",
    "sched_class": sched,
    "run_prefix": prefix,
    "objective": None if not obj else {
        "J_us": (obj.get("chosen") or {}).get("J_us"),
        "terms": (obj.get("chosen") or {}).get("terms"),
        "weights": obj.get("weights"),
        "model_best_before_objective": (d.get("model_best_before_objective") or {}).get("chosen"),
        "coverage": obj.get("coverage"),
    },
}
json.dump(doc, open(sys.argv[2], "w"), ensure_ascii=False, indent=2)
print("  deploy.json chosen=%s domain=%s sched=%s"
      % (doc["chosen"], doc["domain"], sched or "-"))
if sched:
    print("  调度类 @%s：%s" % (doc["chosen"], " ".join(prefix) if prefix else sched.upper()))
    cov = (doc.get("objective") or {}).get("coverage") or {}
    if cov and not cov.get("complete"):
        print("  ⚠ 目标覆盖 %d/%d 个可部署候选，未测形状未参与排名"
              % (cov.get("ranked_candidates") or 0,
                 cov.get("deployment_ready_candidates") or 0))
PY

step "7c3) 调度感知可行性（只认实测，不认模型）"
if [ -n "${DEPLOY_OBJECTIVE:-}" ] && [ -f "$OUT/stkp.json" ]; then
  DEADLINE_US="$("$PY" -c "import json;print(int((json.load(open('$OUT/task.teir.json')).get('task') or {}).get('deadline_us') or 0))")"
  if [ "${DEADLINE_US:-0}" != "0" ]; then
    "$PY" "$KF/scripts/pr14_sched_aware_verify__调度感知验证.py" \
      --stkp "$OUT/stkp.json" --profile "$DEPLOY_OBJECTIVE" \
      --deadline "$DEADLINE_US" --horizon-s "${OBJ_HORIZON_S:-3600}" \
      -o "$OUT/pr14.json" || echo "  ⚠ 有候选证据不足（见 pr14.json），继续但报告须标注"
    "$PY" - "$OUT/stkp.json" "$OUT/pr14.json" "$OUT/deploy.json" \
            "${REQUIRE_DEADLINE_EVIDENCE:-0}" <<'PY' || die "实测期限不满足（REQUIRE_DEADLINE_EVIDENCE=1）" 9
import json, sys
stkp = json.load(open(sys.argv[1]))
rep = json.load(open(sys.argv[2]))
dep_p = sys.argv[3]
gate = sys.argv[4] if len(sys.argv) > 4 else "0"
chosen = stkp.get("chosen")
row = None
for r in rep.get("rows") or []:
    if r.get("candidate_id") == chosen:
        row = r
        break
doc = json.load(open(dep_p))
if row is None:
    doc["deadline"] = {"verdict": "insufficient_evidence",
                       "why": "入选候选没有可行性记录"}
else:
    per = {m["sched"]: m.get("deadline_verdict") for m in row.get("measured") or []}
    used = (stkp.get("sched_class") or "")
    used_m = next((m for m in row.get("measured") or []
                   if m.get("sched") == used), None)
    if used_m is not None:
        verdict, why = used_m.get("deadline_verdict"), used_m.get("why")
    else:  # 没选调度类：取最差那一档，理由必须跟着同一档，不许串行
        verdict, why = row.get("deadline_verdict"), row.get("why")
    doc["deadline"] = {
        "verdict": verdict,
        "per_sched": per,
        "sched_used": used or None,
        "why": why,
        "all_sched": {m["sched"]: m.get("why") for m in row.get("measured") or []},
        "deadline_us": rep.get("deadline_us"),
        "horizon_s": rep.get("horizon_s"),
        "claim": rep.get("claim"),
    }
json.dump(doc, open(dep_p, "w"), ensure_ascii=False, indent=2)
v = (doc.get("deadline") or {}).get("verdict")
print("  实测期限：%s（%s）" % (v, (doc["deadline"].get("why") or "")[:80]))
if gate == "1" and v in ("infeasible_observed", "insufficient_evidence"):
    sys.exit(9)
PY
  else
    echo "  跳过：任务没有 deadline_us，无法判期限"
  fi
else
  echo "  跳过：未给 DEPLOY_OBJECTIVE（没有画像就没有实测期限判断）"
fi

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

step "7e) 共用时间/版本门禁（STKP 入选方案；采样/提交/数据版本）"
GATE_V=(--teir "$OUT/task.teir.json" --plan "$OUT/plan.json" -o "$OUT/verify_contract.json")
if [ -n "${TIME_CONTRACT:-}" ] && [ -f "${TIME_CONTRACT}" ]; then
  GATE_V+=(--contract "$TIME_CONTRACT")
fi
"$PY" "$KF/scripts/ecos/contract_gate.py" "${GATE_V[@]}" \
  || die "contract_gate 拒绝入选方案" 8

step "8) 生成用户态驱动（#include 原计算体，不复制）"
if [ "$DOMAIN" = "mixed" ]; then
  # 内核段计算体同样必须由 Pass 产出（UKU 的 kernel 组 = stage_pid 之类）
  step "8-uku-a) 内核计算体（Pass 产出 .o，供 UKU 模块链接）"
  "$PY" "$KF/scripts/build_kernel_stage__内核计算体.py" \
    --source "$STAGE_SRC" --plan "$OUT/plan.json" \
    --out-dir "$OUT/uku_stage" \
    --pass-so "${KFUSION_PASS_SO:-$KF/kfusion/build/KFusionPass.so}" \
    --clang "$CLANG" --opt "$OPT" --llc "${LLC_BIN:-llc}" \
    --extra-public "${KERNEL_STATE_SYMS:-g_in,g_integ,g_out,loop_reset,loop_main}" \
    || die "UKU 内核计算体构建失败（拒绝回落到直接编译原 C）" 9
  "$PY" "$KF/scripts/mixed_uku_codegen__混合交接.py" \
    --plan "$OUT/plan.json" --teir "$OUT/task.teir.json" \
    --source "$STAGE_SRC" --out-dir "$OUT/kmod_uku" \
    --stage-obj "$OUT/uku_stage/stage_impl.S" \
    --probe-header "$PROBE" --period-us "$PERIOD" --cycles "$CYCLES" \
    "${CALLS[@]}" "${DIGEST[@]}" || die "UKU codegen 失败" 9
  cp "$OUT/kmod_uku/driver.c" "$OUT/driver.c"
  cp "$OUT/kmod_uku/kfusion_uku.h" "$OUT/kfusion_uku.h"
  if [ -f "$OUT/kmod_uku/backend_capabilities.json" ]; then
    cp "$OUT/kmod_uku/backend_capabilities.json" "$OUT/backend_capabilities.json"
  fi
else
"$PY" "$KF/scripts/fusion_codegen__生成融合代码.py" "$OUT/plan.json" \
  -o "$OUT/driver.c" \
  --stage-source "$STAGE_SRC" \
  --probe-header "$PROBE" \
  --period-us "$PERIOD" --step-us "$STEP" \
  --cycles "$CYCLES" \
  --expect-order "$EXPECT" \
  "${CALLS[@]}" "${DIGEST[@]}" || die "codegen 失败" 9
fi

EMIT_DIGEST=()
eval "$("$PY" - "$OUT/stages.json" <<'PY'
import io, json, sys, re
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
for g in d.get("globals") or []:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", g):
        print("EMIT_DIGEST+=(--digest-var %s)" % g)
PY
)"

KDIR="${KERNELDIR:-/root/linux-5.15.86}"
if [ "$DOMAIN" = "linux_kernel" ]; then
  step "8b) hrtimer 内核宿主（入选域=linux_kernel）"
  "$PY" "$KF/backend/synthesis/emit.py" \
    --plan "$OUT/plan.json" --dag "$OUT/task.dag.json" \
    --source "$STAGE_SRC" \
    --out-dir "$OUT/kmod_hrtimer" \
    --target linux-kernel \
    --name five_stage_loop \
    "${CALLS[@]}" "${EMIT_DIGEST[@]}" || die "emit linux-kernel 失败" 9
  # 计算体必须由 Clang/LLVM + PLCFusionPass 产生，**不再复制原始 C**。
  # 复制 = kbuild 现场编译原 C，等于 .ko 里的计算体没经过任何内核化转换。
  step "8b-1) 内核计算体：task.ll → plc-fusion-remap → llc → stage_impl.o"
  "$PY" "$KF/scripts/build_kernel_stage__内核计算体.py" \
    --source "$STAGE_SRC" --plan "$OUT/plan.json" \
    --out-dir "$OUT/kmod_hrtimer" \
    --pass-so "${KFUSION_PASS_SO:-$KF/kfusion/build/KFusionPass.so}" \
    --clang "$CLANG" --opt "$OPT" --llc "${LLC_BIN:-llc}" \
    --extra-public "${KERNEL_STATE_SYMS:-g_in,g_integ,g_out,loop_reset,loop_main}" \
    || die "内核计算体构建失败（拒绝回落到直接编译原 C）" 9
  if [ "${SKIP_KMOD:-0}" != "1" ] && [ -d "$KDIR" ]; then
    make -C "$KDIR" M="$OUT/kmod_hrtimer" modules || echo "⚠ 内核模块构建失败（源码仍保留）"
  else
    echo "跳过 .ko 构建（无 KERNELDIR 或 SKIP_KMOD=1）"
  fi
elif [ "$DOMAIN" = "mixed" ]; then
  step "8b) UKU ioctl 内核计算模块"
  rm -rf "$OUT/kmod_hrtimer"
  if [ "${SKIP_KMOD:-0}" != "1" ] && [ -d "$KDIR" ]; then
    make -C "$KDIR" M="$OUT/kmod_uku" modules || echo "⚠ UKU 模块构建失败（源码仍保留）"
  else
    echo "跳过 UKU .ko（无 KERNELDIR 或 SKIP_KMOD=1）"
  fi
else
  step "8b) 入选用户态：不生成内核部署产物"
  rm -rf "$OUT/kmod_hrtimer" "$OUT/kmod_uku"
  echo "  skip kmod（chosen=$CHOSEN domain=$DOMAIN）"
fi

if [ -n "${TIME_CONTRACT:-}" ] && [ -f "${TIME_CONTRACT}" ] && [ -f "$OUT/let.json" ]; then
  step "8c) LET 作业表（plc_let；须通过同一 TIME_CONTRACT，且不得用无 LET 全融合绕过 STKP）"
  SKIP_LET_EMIT=0
  "$PY" - "$OUT/let.json" "$OUT/plan_let.json" "$OUT/plan.json" "$OUT/let_emit.flag" <<'PY'
import json, sys
let = json.load(open(sys.argv[1]))
rec = let.get("chosen_record") or {}
plan = rec.get("plan")
if not plan:
    raise SystemExit("let.json 无 chosen plan")
stkp = {}
try:
    stkp = json.load(open(sys.argv[3]))
except Exception:
    pass
stkp_g = [g.get("members") for g in (stkp.get("groups") or [])]
let_g = [g.get("members") for g in (plan.get("groups") or [])]
uses_let = bool((rec.get("cost") or {}).get("uses_let") or plan.get("let_events"))
flag = sys.argv[4]
# 无 LET 的全融合不得作为第二部署源；须与 STKP 同组，或带钉扎事件。
# LET 事件不能单独豁免窗口：deployable=false 则不生成。
if (let.get("chosen") == "full_fuse" or (not uses_let and let_g != stkp_g)
        or rec.get("deployable") is False):
    open(flag, "w").write("1\n")
    print("skip let_host: chosen=%s uses_let=%s" % (let.get("chosen"), uses_let))
    raise SystemExit(0)
json.dump(plan, open(sys.argv[2], "w"), ensure_ascii=False, indent=2)
open(flag, "w").write("0\n")
print("LET chosen=%s uses_let=%s" % (let.get("chosen"), uses_let))
PY
  SKIP_LET_EMIT="$(tr -d '[:space:]' < "$OUT/let_emit.flag" 2>/dev/null || echo 1)"
  if [ "${SKIP_LET_EMIT:-0}" = "1" ] || [ ! -f "$OUT/plan_let.json" ]; then
    echo "⚠ LET 方案不可部署或与 STKP 分组冲突：不生成 let_host"
    rm -rf "$OUT/let_host"
    rm -f "$OUT/plan_let.json"
  else
    LET_V=(
      --dag "$OUT/task.dag.json" --plan "$OUT/plan_let.json"
      --platforms "$CAPS" --platform linux_userspace
      --stamp-plan -o "$OUT/verify_let_dag.json"
    )
    if [ -n "${TIME_CONTRACT:-}" ] && [ -f "${TIME_CONTRACT}" ]; then
      LET_V+=(--contract "$TIME_CONTRACT")
    fi
    if "$PY" "$KF/scripts/ecos/plan_verify.py" "${LET_V[@]}" \
       && "$PY" "$KF/scripts/ecos/contract_gate.py" \
            --teir "$OUT/task.teir.json" --plan "$OUT/plan_let.json" \
            --contract "$TIME_CONTRACT" -o "$OUT/verify_let_contract.json"; then
      "$PY" "$KF/backend/synthesis/emit.py" \
        --plan "$OUT/plan_let.json" --dag "$OUT/task.dag.json" \
        --source "$STAGE_SRC" \
        --out-dir "$OUT/let_host" \
        --target let \
        --name five_stage_loop \
        "${CALLS[@]}" "${EMIT_DIGEST[@]}" || die "emit let 失败" 9
      cp "$STAGE_SRC" "$OUT/let_host/stage_impl.c" 2>/dev/null || true
    else
      echo "⚠ LET 入选方案未通过同一 TIME_CONTRACT，不生成 let_host（STKP plan 仍是部署源）"
      rm -rf "$OUT/let_host"
      rm -f "$OUT/plan_let.json"
    fi
  fi
fi

if [ "${SKIP_HOST:-0}" != "1" ]; then
  # 选中的调度类要不要真的作用到试跑？默认只记录（APPLY_SCHED=0），
  # 因为改调度类会改变测量口径；要复现决策就显式开。
  RUN_PREFIX=()
  if [ "${APPLY_SCHED:-0}" = "1" ] && [ "${SCHED_CLASS:-}" = "fifo99" ]; then
    if command -v chrt >/dev/null; then
      RUN_PREFIX=(chrt -f 99)
      echo "  ▶ 按部署决策加调度类：chrt -f 99"
    else
      echo "  ⚠ 选了 fifo99 但本机无 chrt，按 CFS 跑（报告里不要写成 FIFO）"
    fi
  fi
  if [ "$DOMAIN" = "mixed" ]; then
    step "9) UKU 用户态驱动（gold；mixed 需 insmod 后跑）"
    gcc -O2 -std=gnu11 -Wall -Werror -I"$KF/include" -I"$OUT" -I"$OUT/kmod_uku" \
      "$OUT/driver.c" -o "$OUT/driver" -lrt || die "gcc 失败" 10
    "${RUN_PREFIX[@]}" "$OUT/driver" gold "$CYCLES" | tee "$OUT/driver.gold.log"
  else
  step "9) 宿主编译并短跑 plan/flat（digest 必须相同）"
  gcc -O2 -std=gnu11 -Wall -Werror -I"$KF/include" \
    "$OUT/driver.c" -o "$OUT/driver" -lrt || die "gcc 失败" 10
  "${RUN_PREFIX[@]}" "$OUT/driver" plan "$CYCLES" | tee "$OUT/driver.plan.log"
  "${RUN_PREFIX[@]}" "$OUT/driver" flat "$CYCLES" | tee "$OUT/driver.flat.log"
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
  fi
else
  echo "跳过宿主编译（SKIP_HOST=1）"
fi

echo
echo "✅ 从 C 到 A/B/C/D 流水线完成"
echo "   骨架  $OUT/stages.json"
echo "   DAG   $OUT/task.dag.json"
echo "   TEIR  $OUT/task.teir.json"
echo "   入选  $OUT/chosen.plan.json  ($CHOSEN / $DOMAIN)"
echo "   调度类 ${SCHED_CLASS:-（未启用部署目标，按 CFS）}"
echo "   部署  $OUT/deploy.json"
echo "   方案  $OUT/plan.json"
echo "   验证  $OUT/verify.json"
echo "   选型  $OUT/candidates.json  (A–F 基线)"
echo "   STKP  $OUT/stkp.json"
if [ -f "$OUT/let.json" ]; then
  echo "   LET   $OUT/let.json"
fi
echo "   用户态 $OUT/driver.c"
if [ "$DOMAIN" = "linux_kernel" ]; then
  echo "   kmod   $OUT/kmod_hrtimer/"
elif [ "$DOMAIN" = "mixed" ]; then
  echo "   UKU    $OUT/kmod_uku/  adapter=$ADAPTER"
else
  echo "   kmod   （未生成：入选用户态）"
fi
echo "   计算体始终是 $SRC ，没有第二份手写阶段函数"
