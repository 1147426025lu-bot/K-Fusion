#!/usr/bin/env bash
# ecos_build.sh — ECoS 严格构建入口（P0-3 / PR9 闭环的前半段）
#
# 与 plc_kernelize__内核化.sh 的分工：
#   plc_kernelize  是 **legacy 兼容链**：没有契约时警告后继续，保护已能跑通的
#                  cyclictest 工具链不被新门禁打断。
#   ecos_build    是 **论文/ECoS 链**：反过来——没有契约、没有合法 DAG、
#                  没有通过独立验证的方案，一律不生成产物。
#
# 门禁顺序（任一失败即停，绝不"先出东西再补检查"）：
#   1. 契约存在     → 缺了就退（旧版兼容行为不在这里）
#   2. 契约合法     → 结构 / 窗口 / stage 名必须与 DAG 对得上
#   3. 规划出方案   → 不可行就如实报不可行（rc=3），不降级成"最优非法方案"
#   4. 独立复检产物 → 对**落盘后的** plan.json 再验一遍（防手改/防写盘走样）
#   5. 代码生成     → 传 --dag 现场复检 + --require-verified
#
# 用法：
#   bash kfusion/scripts/ecos_build.sh \
#     --dag  kfusion/scripts/test/fusion/chain_window.dag.json \
#     --contract kfusion/contracts/examples/chain_window.contract.json \
#     --platforms kfusion/contracts/examples/platform_caps.json \
#     --platform nuttx --release-cost-us 21 \
#     --exec-us '{"s1":0.6,"s2":0.67,"s3":0.28,"s4":0.5}' \
#     --source kfusion/examples/ecos/five_stage_loop__闭环五阶段示例.c \
#     --out-dir build/ecos_chain_window
#
# 退出码：0 成功｜2 参数/环境问题｜3 契约或方案不合法｜4 后端未实现

set -u -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"
PLAN_PY="$HERE/fusion_plan__融合规划.py"
CODEGEN="$HERE/fusion_codegen__生成融合代码.py"
CONTRACT_PY="$HERE/ecos/contract_validate.py"

DAG=""; CONTRACT=""; PLATFORMS=""; PLATFORM=""; SOURCE=""; OUT_DIR=""
EXEC_US=""; C=""; TARGET="userspace-driver"; STEP_US="200"; PERIOD_US=""

log()  { printf '[ecos] %s\n' "$*"; }
die()  { printf '[ecos] ❌ %s\n' "$1" >&2; exit "${2:-3}"; }

need() { [ -n "${2:-}" ] || die "缺少 $1（严格入口不接受缺省）" 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dag)              DAG="${2:-}"; shift 2 ;;
        --contract)         CONTRACT="${2:-}"; shift 2 ;;
        --platforms)        PLATFORMS="${2:-}"; shift 2 ;;
        --platform)         PLATFORM="${2:-}"; shift 2 ;;
        --source)           SOURCE="${2:-}"; shift 2 ;;
        --out-dir)          OUT_DIR="${2:-}"; shift 2 ;;
        --exec-us)          EXEC_US="${2:-}"; shift 2 ;;
        --release-cost-us)  C="${2:-}"; shift 2 ;;
        --period-us)        PERIOD_US="${2:-}"; shift 2 ;;
        --step-us)          STEP_US="${2:-}"; shift 2 ;;
        --target)           TARGET="${2:-}"; shift 2 ;;
        --strict)           shift ;;   # 本入口恒为严格，保留以兼容审查给的命令行
        -h|--help)          sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)                  die "未知参数 $1" 2 ;;
    esac
done

need --dag "$DAG"; need --contract "$CONTRACT"; need --platforms "$PLATFORMS"
need --platform "$PLATFORM"; need --source "$SOURCE"; need --out-dir "$OUT_DIR"
[ -f "$DAG" ]      || die "--dag 不存在：$DAG" 2
[ -f "$CONTRACT" ] || die "--contract 不存在：$CONTRACT" 2
[ -f "$SOURCE" ]   || die "--source 不存在：$SOURCE" 2
mkdir -p "$OUT_DIR" || die "建不了输出目录 $OUT_DIR" 2

log "① 契约校验（缺契约/窗口自相矛盾/stage 名写错 → 停）"
if ! "$PY" "$CONTRACT_PY" "$DAG" "$CONTRACT"; then
    die "契约不合法，拒绝生成产物" 3
fi

log "② 规划（约束优化；不可行就报不可行）"
PLAN="$OUT_DIR/plan.json"
plan_args=("$DAG" --platforms "$PLATFORMS" --platform "$PLATFORM" -o "$PLAN")
[ -n "$C" ] && plan_args+=(--release-cost-us "$C")
[ -n "$EXEC_US" ] && plan_args+=(--exec-us "$EXEC_US")
plan_args+=(--contract "$CONTRACT")
"$PY" "$PLAN_PY" "${plan_args[@]}"
rc=$?
case "$rc" in
    0) : ;;
    3) die "在给定契约下**无可行方案**（不是程序出错：是约束真的不可满足）" 3 ;;
    *) die "规划失败 rc=$rc" 3 ;;
esac

log "③ 对落盘后的 plan.json 再独立复检（防手改 / 防写盘走样）"
verify_args=("$DAG" --platforms "$PLATFORMS" --platform "$PLATFORM"
             --verify "$PLAN" --contract "$CONTRACT" --quiet)
[ -n "$C" ] && verify_args+=(--release-cost-us "$C")
[ -n "$EXEC_US" ] && verify_args+=(--exec-us "$EXEC_US")
"$PY" "$PLAN_PY" "${verify_args[@]}" || die "方案复检未通过，拒绝生成" 3

log "④ 代码生成（--dag 现场复检 + --require-verified）"
DRIVER="$OUT_DIR/driver.c"
codegen_args=("$PLAN" -o "$DRIVER" --stage-source "$SOURCE"
              --dag "$DAG" --platforms "$PLATFORMS" --platform "$PLATFORM"
              --contract "$CONTRACT" --require-verified
              --step-us "$STEP_US")
[ -n "$PERIOD_US" ] && codegen_args+=(--period-us "$PERIOD_US")
[ -n "$C" ] && codegen_args+=(--release-cost-us "$C")
"$PY" "$CODEGEN" "${codegen_args[@]}" || die "代码生成失败" 3

# 溯源清单：以后写论文时能说清"这个产物是哪个 DAG/契约/方案生成的"
MANIFEST="$OUT_DIR/manifest.txt"
{
    echo "ecos_build manifest"
    echo "dag       $DAG"
    echo "contract  $CONTRACT"
    echo "platform  $PLATFORM"
    echo "target    $TARGET"
    echo "source    $SOURCE"
    echo "plan      $PLAN"
    if command -v sha256sum >/dev/null 2>&1; then
        echo "sha256(dag)      $(sha256sum "$DAG" | cut -d' ' -f1)"
        echo "sha256(contract) $(sha256sum "$CONTRACT" | cut -d' ' -f1)"
        echo "sha256(plan)     $(sha256sum "$PLAN" | cut -d' ' -f1)"
    fi
} >"$MANIFEST"
log "溯源清单 → $MANIFEST"

if [ "$TARGET" = "linux-kernel" ]; then
    cat >&2 <<'EOF'
[ecos] ⛔ linux-kernel 后端尚未实现（PR9-C 的剩余部分）
       本轮只做了「可信方案验证 + 用户态/RTOS 驱动生成」这一段。
       缺的是：把融合期体拆成 ring-0 代码 + 宿主 hrtimer 释放，
       并复用现有 LLVM 内核化 Pass 产出 .ko（不能把 clock_nanosleep/main
       的驱动原样塞进内核）。
       在补齐之前，本入口不会假装能出 .ko —— 静默降级是这类工具最糟的行为。
EOF
    exit 4
fi

log "✅ 完成：$DRIVER（target=$TARGET）"
