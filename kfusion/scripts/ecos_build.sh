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
#     --source kfusion/examples/ecos/chain_window__四段窗口示例.c \
#     --out-dir build/ecos_chain_window \
#     --target linux-kernel        # 或 nuttx / userspace-driver（默认）
#
#   --kdir /lib/modules/$(uname -r)/build   # 给了才真的跑 Kbuild 出 .ko
#   --call stage_pid=stage_pid(1000)        # 阶段函数带参数时给调用表达式
#   --split-by-domain                       # 跨域：拆成 root + nuttx 两端各出一份产物
#                                           #（两端共用同一份 ecos_xipc.h，必须同时重编）
#
# 退出码：0 成功（产物是否已构建看 manifest 的 built）｜2 参数/环境问题｜3 契约或方案不合法
# 本机没有内核树时只生成源码，且会明确写 built=0 —— 不把"生成源码"报成"构建成功"。

set -u -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"
PLAN_PY="$HERE/fusion_plan__融合规划.py"
CODEGEN="$HERE/fusion_codegen__生成融合代码.py"
CONTRACT_PY="$HERE/ecos/contract_validate.py"

DAG=""; CONTRACT=""; PLATFORMS=""; PLATFORM=""; SOURCE=""; OUT_DIR=""
EXEC_US=""; C=""; TARGET="userspace-driver"; STEP_US=""; STEP_US_OVERRIDE=""; PERIOD_US=""
KDIR=""; CALLS=(); DIGEST_VARS=(); PROBE=0; SPLIT=0; TARGET_SET=0

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
        --step-us)          STEP_US_OVERRIDE="${2:-}"; shift 2 ;;
        --target)           TARGET="${2:-}"; TARGET_SET=1; shift 2 ;;
        --kdir)             KDIR="${2:-}"; shift 2 ;;
        --call)             CALLS+=("${2:-}"); shift 2 ;;
        --digest-var)       DIGEST_VARS+=("${2:-}"); shift 2 ;;
        --probe)            PROBE=1; shift ;;
        --split-by-domain)  SPLIT=1; shift ;;
        --strict)           shift ;;   # 本入口恒为严格，保留以兼容审查给的命令行
        -h|--help)          sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)                  die "未知参数 $1" 2 ;;
    esac
done

# 拆分模式下两端目标由**域**决定（root→linux-kernel、nuttx→nuttx），
# 再给 --target 就是自相矛盾：宁可报错，也不要静默忽略一个用户以为生效的开关。
if [ "$SPLIT" = "1" ] && [ "$TARGET_SET" = "1" ]; then
    die "--split-by-domain 与 --target 互斥（拆分后两端目标由域决定）" 2
fi
[ "$SPLIT" = "1" ] && TARGET="split"

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

# ---- 节拍参数必须来自契约/DAG，不能用代码生成器的缺省值 ----
# 缺省会静默给出 period=1000 / step=200，跟真实契约不一致时产物"看着对、跑起来错"。
read -r K_PERIOD K_STEP <<EOF
$("$PY" - "$DAG" "$CONTRACT" <<'PY'
import json, sys
dag = json.load(open(sys.argv[1], encoding="utf-8"))
con = json.load(open(sys.argv[2], encoding="utf-8"))
task = con.get("task") or {}
ctr = con.get("timing_constraints") or {}
meta_task = (dag.get("meta") or {}).get("task") or {}
per = (ctr.get("period_us") or task.get("period_us")
       or (dag.get("phase") or {}).get("period_us") or meta_task.get("period_us"))
step = meta_task.get("step_us") or (dag.get("phase") or {}).get("step_us")
print("%s %s" % (per if per is not None else "", step if step is not None else ""))
PY
)
EOF
same_num() { awk -v a="$1" -v b="$2" 'BEGIN { exit (a+0 == b+0) ? 0 : 1 }'; }

if [ -n "$PERIOD_US" ] && [ -n "$K_PERIOD" ] && ! same_num "$PERIOD_US" "$K_PERIOD"; then
    die "--period-us=$PERIOD_US 与契约/DAG 的 $K_PERIOD 不一致（拒绝用两套节拍）" 2
fi
if [ -n "$STEP_US_OVERRIDE" ] && [ -n "$K_STEP" ] && ! same_num "$STEP_US_OVERRIDE" "$K_STEP"; then
    die "--step-us=$STEP_US_OVERRIDE 与 DAG 的 $K_STEP 不一致" 2
fi
if [ -n "$PERIOD_US" ]; then PERIOD_SRC="命令行（已与契约一致）"
elif [ -n "$K_PERIOD" ]; then PERIOD_US="$K_PERIOD"; PERIOD_SRC="契约/DAG"
else die "契约/DAG 里没有 period_us：无法确定节拍，拒绝生成" 3; fi

if [ -n "$STEP_US_OVERRIDE" ]; then STEP_US="$STEP_US_OVERRIDE"; STEP_SRC="命令行（已与 DAG 一致）"
elif [ -n "$K_STEP" ]; then STEP_US="$K_STEP"; STEP_SRC="DAG"
else STEP_US="200"; STEP_SRC="**缺省 200**（DAG 未给 step_us，仅影响 flat 对照模式的相位间隔）"; fi
log "   节拍：period=${PERIOD_US} µs（${PERIOD_SRC}），step=${STEP_US} µs（${STEP_SRC}）"

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

RESULT=""; BUILT=0
if [ "$SPLIT" = "1" ]; then
    # ---- 跨域：一份跨域方案 → 每域一份子方案 → 两端各自合成 ----
    # 为什么必须走这条路：PR10-B 的硬约束是"一个周期体只能属于一个 cell"，
    # 所以跨域部署的正确产物形态是**两个后端各一份**，共用同一个环传数。
    # 以前这条路只存在于手写的 xdom_build.sh + 两条手工 emit 命令里，
    # 严格入口不支持它 ⇒ "论文用的产物"和"严格入口产出的产物"是两条链。
    SPLIT_PY="$HERE/ecos/split_plan__按域拆分方案.py"
    SPLIT_DIR="$OUT_DIR/split"
    log "④ 跨域拆分（每域一份子方案 + 接口描述 interface.json）"
    rm -rf "$SPLIT_DIR"
    "$PY" "$SPLIT_PY" "$PLAN" --dag "$DAG" --out-dir "$SPLIT_DIR" \
        || die "拆分失败（方案里有跨域组 / 跨域边缺 evidence）" 3

    SYNTH="$HERE/../backend/synthesis/emit.py"
    XIPC_H="$HERE/../runtime/xshm/ecos_xipc.h"
    mapfile -t DOMS < <("$PY" - "$SPLIT_DIR/interface.json" <<'PY' | tr -d '\r'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for x in d.get("domains") or []:
    print(x)
PY
)
    [ "${#DOMS[@]}" -gt 0 ] || die "拆分结果里没有域" 3
    RESULTS=()
    for d in "${DOMS[@]}"; do
        [ -f "$SPLIT_DIR/$d/plan.json" ] || die "缺 $d/plan.json" 3
        case "$d" in
            root)  TGT="linux-kernel" ;;
            nuttx) TGT="nuttx" ;;
            *)     die "未知域 $d（只认 root / nuttx）" 2 ;;
        esac
        log "⑤ 后端合成：$d → $TGT"
        emit_args=(--plan "$SPLIT_DIR/$d/plan.json" --dag "$DAG" --source "$SOURCE"
                   --out-dir "$OUT_DIR/$d" --target "$TGT"
                   --contract "$CONTRACT" --xdom "$SPLIT_DIR/$d/meta.json")
        [ -n "$EXEC_US" ] && emit_args+=(--exec-us "$EXEC_US")
        for c in "${CALLS[@]:-}"; do [ -n "$c" ] && emit_args+=(--call "$c"); done
        for v in "${DIGEST_VARS[@]:-}"; do [ -n "$v" ] && emit_args+=(--digest-var "$v"); done
        [ "$PROBE" = "1" ] && emit_args+=(--probe)
        "$PY" "$SYNTH" "${emit_args[@]}" || die "后端合成失败（$d，见上面的拒绝原因）" 3
        # 阶段实现与环头文件：两端口径必须**同一份**，否则 ABI 对不上
        cp "$SOURCE" "$OUT_DIR/$d/stage_impl.c" || die "拷 stage_impl.c 失败" 2
        [ -f "$XIPC_H" ] && cp "$XIPC_H" "$OUT_DIR/$d/ecos_xipc.h"
        RESULTS+=("$OUT_DIR/$d")
    done
    RESULT="${RESULTS[*]}"
    cat >"$OUT_DIR/README.build.txt" <<EOF
跨域产物（--split-by-domain）：$OUT_DIR/root 与 $OUT_DIR/nuttx 必须**同时**重编部署。
环上发的是 µs 时间戳（协议 v3），两端共用同一份 ecos_xipc.h —— 只编一端会 ABI 不一致，
表现为数据错乱而不是报错。

构建与部署（在目标板上，需要内核树 / NuttX 树）：
  bash kfusion/scripts/ecos/xdom_build__构建跨域两端.sh <本目录的 tgz>
上板后先 enable jailhouse，再载 inmate cell，最后 insmod root 的 .ko。

注意：本机没有目标侧工具链，这里只生成源码；built=0 表示**没有**构建。
EOF
    log "   构建说明 → $OUT_DIR/README.build.txt"
else
case "$TARGET" in
userspace-driver)
    log "④ 代码生成：用户态对照驱动（--dag 现场复检 + --require-verified）"
    DRIVER="$OUT_DIR/driver.c"
    codegen_args=("$PLAN" -o "$DRIVER" --stage-source "$SOURCE"
                  --dag "$DAG" --platforms "$PLATFORMS" --platform "$PLATFORM"
                  --contract "$CONTRACT" --require-verified
                  --step-us "$STEP_US")
    [ -n "$PERIOD_US" ] && codegen_args+=(--period-us "$PERIOD_US")
    [ -n "$C" ] && codegen_args+=(--release-cost-us "$C")
    [ -n "$EXEC_US" ] && codegen_args+=(--exec-us "$EXEC_US")
    for c in "${CALLS[@]:-}"; do [ -n "$c" ] && codegen_args+=(--call "$c"); done
    "$PY" "$CODEGEN" "${codegen_args[@]}" || die "代码生成失败" 3
    RESULT="$DRIVER"
    ;;
linux-kernel|nuttx)
    log "④ 后端合成（$TARGET）：共享周期体 + 宿主"
    SYNTH="$HERE/../backend/synthesis/emit.py"
    emit_args=(--plan "$PLAN" --dag "$DAG" --source "$SOURCE"
               --out-dir "$OUT_DIR/$TARGET" --target "$TARGET"
               --contract "$CONTRACT")
    [ -n "$EXEC_US" ] && emit_args+=(--exec-us "$EXEC_US")
    for c in "${CALLS[@]:-}"; do [ -n "$c" ] && emit_args+=(--call "$c"); done
    for v in "${DIGEST_VARS[@]:-}"; do [ -n "$v" ] && emit_args+=(--digest-var "$v"); done
    [ "$PROBE" = "1" ] && emit_args+=(--probe)
    "$PY" "$SYNTH" "${emit_args[@]}" || die "后端合成失败（见上面的拒绝原因）" 3
    RESULT="$OUT_DIR/$TARGET"

    if [ "$TARGET" = "linux-kernel" ] && [ -n "$KDIR" ]; then
        command -v make >/dev/null 2>&1 || die "有 --kdir 但没有 make" 2
        log "⑤ Kbuild（KDIR=$KDIR）"
        ( cd "$OUT_DIR/linux-kernel" && make -C "$KDIR" M="$PWD" modules ) \
            || die "Kbuild 失败（vermagic / 内核头 / stage_impl.o 缺失都可能）" 3
        BUILT=1
    else
        cat >&2 <<'EOF'
[ecos] ⚠ 只生成了源码，**没有**构建 .ko / 应用
       原因：本机没有目标侧工具链（内核树 / NuttX 树）。
       构建步骤见产物目录里的 README.build.txt。
       注意：「生成了源码」不等于「构建成功」——不要把这两件事混着报。
EOF
    fi
    ;;
*)
    die "未知 target=$TARGET（可选 userspace-driver | linux-kernel | nuttx）" 2
    ;;
esac
fi

# 溯源清单：以后写论文时能说清"这个产物是哪个 DAG/契约/方案生成的"
MANIFEST="$OUT_DIR/manifest.txt"
{
    echo "ecos_build manifest"
    echo "dag       $DAG"
    echo "contract  $CONTRACT"
    echo "platform  $PLATFORM"
    echo "target    $TARGET"
    echo "split     $SPLIT（1 = 跨域按域拆分，产物在 root/ 与 nuttx/）"
    echo "built     $BUILT"
    echo "source    $SOURCE"
    echo "plan      $PLAN"
    echo "result    $RESULT"
    if command -v sha256sum >/dev/null 2>&1; then
        echo "sha256(dag)      $(sha256sum "$DAG" | cut -d' ' -f1)"
        echo "sha256(contract) $(sha256sum "$CONTRACT" | cut -d' ' -f1)"
        echo "sha256(plan)     $(sha256sum "$PLAN" | cut -d' ' -f1)"
    fi
} >"$MANIFEST"
log "溯源清单 → $MANIFEST"

log "✅ 完成：$RESULT（target=$TARGET，built=$BUILT）"
