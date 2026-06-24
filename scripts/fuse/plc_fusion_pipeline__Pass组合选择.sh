#!/bin/bash
# ============================================================================
# plc_fusion_pipeline__Pass组合选择.sh — 内核化 Pass 组合算法
# ============================================================================
# 功能: 根据 manifest + AST 画像 + IR 特征自动选择 pre/kernel/tail pipeline profile
# 算法:
#   0. load_ast_plan — 读 fusion_ast / plc_ast JSON → fusion_plan.json
#   1. select_profile — manifest 推断 mainline/generic；auto 时 AST + IR 微调
#   2. apply_profile  — profile → Pass 配方与环境变量
#   3. apply_ir_hints  — 无浮点 IR 时关 float_kill；记录分析依据
#   4. build_opt_passes — 拼 opt -passes 字符串
# 输入: manifest.env（可选已 source plc_fusion_analyze_ir__IR特征分析.sh 的 export）
# 输出（export）:
#   PLC_FUSION_PRE_PASSES / PLC_FUSION_KERNEL_PASS / PLC_FUSION_TAIL_PASSES
#   PLC_FUSION_PIPELINE / OPT_PASSES
# 环境:
#   FUSE_AST_PLAN=1       读 test/${FUSE_NAME}.fusion_plan.json（默认开）
#   FUSE_PIPELINE=auto|mainline|generic|minimal|debug|size|hotpath|wcet|custom
#   FUSE_WCET_MODE=1      auto 时 kthread 应用优先 hotpath（无 tail、轻 pre）
#   FUSE_HOT_PATH_FUNCTIONS  热路径函数（逗号分隔，供 Pass DCE 额外保留）
#   FUSE_AUTO_DEBUG=1     unknown_externs >= FUSE_DEBUG_THRESHOLD → debug
#   FUSE_AUTO_SIZE=1      IR/已有 .o 偏大且 unknown 低 → size
#   FUSE_DEBUG_THRESHOLD  默认 3
#   FUSE_SIZE_IR_LINES    默认 80000
#   FUSE_SIZE_OBJ_BYTES   默认 262144 (256KiB)
# 用法:
#   source scripts/plc_fusion_pipeline__Pass组合选择.sh manifests/foo.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"

if [ -z "$MANIFEST" ]; then
    plc_die "$PLC_E_USAGE" "缺少 manifest 参数" \
        "用法: source scripts/plc_fusion_pipeline__Pass组合选择.sh manifests/foo.env"
fi
plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

FUSE_DEBUG_THRESHOLD="${FUSE_DEBUG_THRESHOLD:-3}"
FUSE_DEBUG_PRE_THRESHOLD="${FUSE_DEBUG_PRE_THRESHOLD:-0}"
FUSE_SIZE_IR_LINES="${FUSE_SIZE_IR_LINES:-80000}"
FUSE_SIZE_OBJ_BYTES="${FUSE_SIZE_OBJ_BYTES:-262144}"
FUSE_AUTO_DEBUG="${FUSE_AUTO_DEBUG:-1}"
FUSE_AUTO_SIZE="${FUSE_AUTO_SIZE:-1}"

# --- 算法 0: AST 画像 → fusion_plan（可选）---
load_ast_plan() {
    PLC_FUSION_AST_LOADED=0
    if [ "${FUSE_AST_PLAN:-1}" != "1" ]; then
        export PLC_FUSION_AST_LOADED
        return 0
    fi
    # shellcheck source=plc_fusion_ast_plan__AST方案读取.sh
    source "$SCRIPT_DIR/plc_fusion_ast_plan__AST方案读取.sh" "$MANIFEST" || true
}

# --- 算法 1: manifest 基础 profile ---
base_profile() {
    if [ "${FUSE_WCET_MODE:-0}" = "1" ]; then
        echo wcet
        return
    fi
    if [ -n "${FUSE_KTHREAD_ENTRY:-}" ] && [ "${FUSE_RUN_MAIN:-0}" != "1" ]; then
        echo mainline
        return
    fi
    if [ "${FUSE_RUN_MAIN:-0}" = "1" ]; then
        echo generic
        return
    fi
    echo generic
}

# --- 算法 2: auto 时按 IR / 历史 .o 微调 ---
auto_tune_profile() {
    local base="$1"
    local unknown="${PLC_FUSION_IR_UNKNOWN_EXTERNS:-0}"
    local lines="${PLC_FUSION_IR_LINES:-0}"
    local obj="${PLC_FUSION_OBJ_BYTES:-0}"
    local reason=""

    # AST 安全门：融合 critical → minimal（优先于 IR 微调）
    if [ "${PLC_FUSION_AST_LOADED:-0}" = "1" ] \
        && [ "${PLC_FUSION_AST_FUSION_CRIT:-0}" -gt 0 ]; then
        reason="ast:fusion_crit=${PLC_FUSION_AST_FUSION_CRIT}"
        echo "minimal|$reason"
        return
    fi

    # pre.ll 阶段默认不因 unknown 切 debug（成熟应用误报多）；阈值 0=关闭
    if [ "${FUSE_AUTO_DEBUG:-1}" = "1" ] && [ "$FUSE_DEBUG_PRE_THRESHOLD" -gt 0 ] \
        && [ "$unknown" -ge "$FUSE_DEBUG_PRE_THRESHOLD" ]; then
        reason="pre_unknown=${unknown}>=${FUSE_DEBUG_PRE_THRESHOLD}"
        echo "debug|$reason"
        return
    fi

    if [ "${FUSE_AUTO_SIZE:-1}" = "1" ]; then
        if [ "$lines" -ge "$FUSE_SIZE_IR_LINES" ] || [ "$obj" -ge "$FUSE_SIZE_OBJ_BYTES" ]; then
            reason="lines=${lines} obj=${obj}"
            echo "size|$reason"
            return
        fi
    fi

    if [ "${FUSE_WCET_MODE:-0}" = "1" ] && [ "$base" = mainline ]; then
        reason="FUSE_WCET_MODE=1"
        echo "hotpath|$reason"
        return
    fi

    # AST profile 建议（manifest/IR 未更强覆盖时）
    if [ "${PLC_FUSION_AST_LOADED:-0}" = "1" ] \
        && [ -n "${PLC_FUSION_AST_SUGGEST_PROFILE:-}" ]; then
        reason="ast:${PLC_FUSION_AST_PLAN_REASON:-heuristic}"
        echo "${PLC_FUSION_AST_SUGGEST_PROFILE}|$reason"
        return
    fi

    echo "${base}|manifest"
}

select_profile() {
    local req="${FUSE_PIPELINE:-auto}"
    local tuned base reason

    if [ "$req" != auto ] && [ -n "$req" ]; then
        PLC_FUSION_PROFILE_REASON="manual FUSE_PIPELINE=$req"
        PLC_FUSION_PIPELINE="$req"
        return
    fi

    base="$(base_profile)"
    tuned="$(auto_tune_profile "$base")"
    reason="${tuned#*|}"
    req="${tuned%%|*}"

    PLC_FUSION_PROFILE_REASON="auto: $reason"
    PLC_FUSION_PIPELINE="$req"
}

# --- 算法 3: profile → Pass 配方 ---
apply_profile() {
    local profile="$1"
    PLC_FUSION_PRE_PASSES="function(mem2reg,instcombine,simplifycfg)"
    PLC_FUSION_TAIL_PASSES=""
    PLC_FUSION_KERNEL_PASS="plc-fusion"

    case "$profile" in
        mainline)
            PLC_FUSION_KERNEL_PASS="plc-kernelize-mainline"
            export PLC_FUSION_FLOAT_KILL="${PLC_FUSION_FLOAT_KILL:-1}"
            export PLC_FUSION_DCE="${PLC_FUSION_DCE:-1}"
            export PLC_FUSION_BLACKHOLE="${PLC_FUSION_BLACKHOLE:-1}"
            if [ "${FUSE_AUTO_OPT:-1}" = "1" ] && [ -z "${FUSE_GLOBALIZE_SYMBOLS:-}" ]; then
                PLC_FUSION_TAIL_PASSES="globaldce,globalopt"
            fi
            ;;
        generic)
            PLC_FUSION_KERNEL_PASS="plc-kernelize-generic"
            export PLC_FUSION_FLOAT_KILL="${PLC_FUSION_FLOAT_KILL:-1}"
            export PLC_FUSION_DCE="${PLC_FUSION_DCE:-1}"
            export PLC_FUSION_BLACKHOLE="${PLC_FUSION_BLACKHOLE:-1}"
            if [ "${FUSE_AUTO_OPT:-1}" = "1" ] && [ -z "${FUSE_GLOBALIZE_SYMBOLS:-}" ]; then
                PLC_FUSION_TAIL_PASSES="globaldce,globalopt"
            fi
            ;;
        minimal)
            PLC_FUSION_KERNEL_PASS="plc-kernelize-minimal"
            export PLC_FUSION_FLOAT_KILL=0
            export PLC_FUSION_DCE=0
            export PLC_FUSION_BLACKHOLE="${PLC_FUSION_BLACKHOLE:-1}"
            ;;
        debug)
            PLC_FUSION_KERNEL_PASS="plc-kernelize-debug"
            export PLC_FUSION_FLOAT_KILL="${PLC_FUSION_FLOAT_KILL:-0}"
            export PLC_FUSION_DCE=0
            export PLC_FUSION_BLACKHOLE=0
            ;;
        size)
            PLC_FUSION_KERNEL_PASS="plc-kernelize-size"
            export PLC_FUSION_FLOAT_KILL="${PLC_FUSION_FLOAT_KILL:-1}"
            export PLC_FUSION_DCE="${PLC_FUSION_DCE:-1}"
            export PLC_FUSION_BLACKHOLE="${PLC_FUSION_BLACKHOLE:-1}"
            if [ "${FUSE_AUTO_OPT:-1}" = "1" ]; then
                PLC_FUSION_TAIL_PASSES="globaldce,globalopt"
            fi
            ;;
        hotpath)
            PLC_FUSION_PRE_PASSES="function(mem2reg,instcombine)"
            PLC_FUSION_KERNEL_PASS="plc-kernelize-hotpath"
            export PLC_FUSION_FLOAT_KILL="${PLC_FUSION_FLOAT_KILL:-1}"
            export PLC_FUSION_DCE="${PLC_FUSION_DCE:-1}"
            export PLC_FUSION_BLACKHOLE="${PLC_FUSION_BLACKHOLE:-1}"
            PLC_FUSION_TAIL_PASSES=""
            ;;
        wcet)
            PLC_FUSION_PRE_PASSES="function(mem2reg,instcombine)"
            PLC_FUSION_KERNEL_PASS="plc-kernelize-wcet"
            export PLC_FUSION_FLOAT_KILL="${PLC_FUSION_FLOAT_KILL:-1}"
            export PLC_FUSION_DCE="${PLC_FUSION_DCE:-1}"
            export PLC_FUSION_BLACKHOLE="${PLC_FUSION_BLACKHOLE:-1}"
            if [ -n "${FUSE_HOT_PATH_FUNCTIONS:-}" ]; then
                export PLC_FUSION_WCET_HOT_FUNCTIONS="$FUSE_HOT_PATH_FUNCTIONS"
            elif [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
                export PLC_FUSION_WCET_HOT_FUNCTIONS="$FUSE_KTHREAD_ENTRY"
            fi
            if [ -n "${FUSE_COLD_PASS_SEQUENCE:-}" ] || [ -n "${FUSE_MODULE_PASS_SEQUENCE:-}" ]; then
                PLC_FUSION_TAIL_PASSES="$(wcet_tail_from_sequences)"
            elif [ -n "${FUSE_TAIL_PASSES:-}" ]; then
                PLC_FUSION_TAIL_PASSES="$FUSE_TAIL_PASSES"
            else
                export FUSE_COLD_PASS_SEQUENCE="${FUSE_COLD_PASS_SEQUENCE:-simplifycfg|sroa|instcombine|loop-mssa(loop-rotate,licm)|loop-unroll|gvn|adce|instcombine}"
                export FUSE_MODULE_PASS_SEQUENCE="${FUSE_MODULE_PASS_SEQUENCE:-globaldce}"
                PLC_FUSION_TAIL_PASSES="$(wcet_tail_from_sequences)"
            fi
            ;;
        custom)
            PLC_FUSION_KERNEL_PASS="${FUSE_KERNEL_PASS:-plc-fusion}"
            PLC_FUSION_PRE_PASSES="${FUSE_PRE_PASSES:-function(mem2reg,instcombine,simplifycfg)}"
            if [ -n "${FUSE_COLD_PASS_SEQUENCE:-}" ] || [ -n "${FUSE_MODULE_PASS_SEQUENCE:-}" ]; then
                PLC_FUSION_TAIL_PASSES="$(wcet_tail_from_sequences)"
            else
                PLC_FUSION_TAIL_PASSES="${FUSE_TAIL_PASSES:-}"
            fi
            ;;
        *)
            plc_die "$PLC_E_MANIFEST" "未知 FUSE_PIPELINE=$profile" \
                "可选: mainline|generic|minimal|debug|size|hotpath|wcet|custom|auto" \
                "见 manifests/manifest_template__清单模板.env 与 README.md"
            ;;
    esac
}

# --- 算法 4: IR 特征覆盖 float_kill（Pass 内也会再判）---
apply_ir_hints() {
    PLC_FUSION_IR_FLOAT_SKIP=0
    if [ "${PLC_FUSION_IR_HAS_FLOAT:-}" = "0" ]; then
        export PLC_FUSION_FLOAT_KILL=0
        PLC_FUSION_IR_FLOAT_SKIP=1
    fi
    # AST：周期/全局浮点 → 保持 float_kill（IR 无浮点时 IR 优先关）
    if [ "${PLC_FUSION_AST_LOADED:-0}" = "1" ] \
        && [ "${PLC_FUSION_AST_SUGGEST_FLOAT_KILL:-auto}" = "1" ] \
        && [ "${PLC_FUSION_IR_FLOAT_SKIP:-0}" != "1" ]; then
        export PLC_FUSION_FLOAT_KILL=1
    fi
    export PLC_FUSION_IR_FLOAT_SKIP
}

build_opt_passes() {
    OPT_PASSES="$PLC_FUSION_KERNEL_PASS"
    if [ -n "$PLC_FUSION_TAIL_PASSES" ]; then
        OPT_PASSES="${OPT_PASSES},${PLC_FUSION_TAIL_PASSES}"
    fi
}

resolve_low_jitter() {
    FUSE_LOW_JITTER="${FUSE_LOW_JITTER:-auto}"
    if [ "$FUSE_LOW_JITTER" = auto ]; then
        if [ "${PLC_FUSION_AST_SUGGEST_LOW_JITTER:-auto}" = "1" ]; then
            FUSE_LOW_JITTER=1
        elif [ "${PLC_FUSION_AST_SUGGEST_LOW_JITTER:-auto}" = "0" ]; then
            FUSE_LOW_JITTER=0
        elif [[ "${FUSE_NAME:-}" == plc_cc_* ]] || [[ "${FUSE_KTHREAD_ENTRY:-}" == plc_* ]]; then
            FUSE_LOW_JITTER=1
        elif [ "${FUSE_WCET_MODE:-0}" = "1" ]; then
            FUSE_LOW_JITTER=1
        else
            FUSE_LOW_JITTER=0
        fi
    fi
    export FUSE_LOW_JITTER
    if [ "$FUSE_LOW_JITTER" = "1" ]; then
        OPT_PASSES="${OPT_PASSES},plc-low-jitter"
        export PLC_FUSION_LOW_JITTER_FUNCTIONS="${FUSE_HOT_PATH_FUNCTIONS:-${FUSE_KTHREAD_ENTRY:-}}"
    fi
}

# RTSS 2025：FUSE_COLD_PASS_SEQUENCE（| 分隔原子）+ FUSE_MODULE_PASS_SEQUENCE
wcet_tail_from_sequences() {
    local cold="${FUSE_COLD_PASS_SEQUENCE:-}"
    local mod="${FUSE_MODULE_PASS_SEQUENCE:-}"
    python3 "$PROJECT_ROOT/scripts/plc_fusion_wcet_passes__Pass序列库.py" --format-tail "$cold" "$mod"
}

PLC_FUSION_PROFILE_REASON=""
PLC_FUSION_PIPELINE=""
load_ast_plan
select_profile
apply_profile "$PLC_FUSION_PIPELINE"
if [ -n "${FUSE_FLOAT_KILL:-}" ]; then
    export PLC_FUSION_FLOAT_KILL="$FUSE_FLOAT_KILL"
fi
apply_ir_hints
build_opt_passes
resolve_low_jitter

export PLC_FUSION_PIPELINE
export PLC_FUSION_PRE_PASSES
export PLC_FUSION_KERNEL_PASS
export PLC_FUSION_TAIL_PASSES
export OPT_PASSES
export PLC_FUSION_PROFILE_REASON

LOG="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}/${FUSE_NAME}.pipeline.log"
{
    echo "# PLCFusion pipeline $(date -Iseconds)"
    echo "profile=$PLC_FUSION_PIPELINE"
    echo "reason=${PLC_FUSION_PROFILE_REASON:-}"
    echo "pre=$PLC_FUSION_PRE_PASSES"
    echo "kernel=$PLC_FUSION_KERNEL_PASS"
    echo "tail=$PLC_FUSION_TAIL_PASSES"
    echo "opt=$OPT_PASSES"
    echo "float_kill=${PLC_FUSION_FLOAT_KILL:-}"
    echo "float_skip_ir=${PLC_FUSION_IR_FLOAT_SKIP:-0}"
    echo "low_jitter=${FUSE_LOW_JITTER:-0}"
    echo "low_jitter_funcs=${PLC_FUSION_LOW_JITTER_FUNCTIONS:-}"
    echo "dce=${PLC_FUSION_DCE:-}"
    echo "blackhole=${PLC_FUSION_BLACKHOLE:-}"
    echo "hot_path=${FUSE_HOT_PATH_FUNCTIONS:-${FUSE_KTHREAD_ENTRY:-}}"
    echo "wcet_mode=${FUSE_WCET_MODE:-0}"
    echo "cold_pass_sequence=${FUSE_COLD_PASS_SEQUENCE:-}"
    echo "module_pass_sequence=${FUSE_MODULE_PASS_SEQUENCE:-}"
    echo "ir_unknown=${PLC_FUSION_IR_UNKNOWN_EXTERNS:-}"
    echo "ir_lines=${PLC_FUSION_IR_LINES:-}"
    echo "obj_bytes=${PLC_FUSION_OBJ_BYTES:-}"
    echo "ast_loaded=${PLC_FUSION_AST_LOADED:-0}"
    echo "ast_json=${PLC_FUSION_AST_JSON:-}"
    echo "ast_plan=${PLC_FUSION_AST_PLAN_JSON:-}"
    echo "ast_suggest_profile=${PLC_FUSION_AST_SUGGEST_PROFILE:-}"
    echo "ast_plan_reason=${PLC_FUSION_AST_PLAN_REASON:-}"
    if [ -f "${PLC_FUSION_AST_PLAN_JSON:-}" ] && command -v python3 >/dev/null; then
        python3 - "${PLC_FUSION_AST_PLAN_JSON}" <<'PY' | while read -r line; do echo "$line"; done
import json, sys
p = json.load(open(sys.argv[1]))
for c in p.get("candidates") or []:
    print(f"ast_candidate_{c['rank']}={c['profile']} ({c.get('reason','')})")
PY
    fi
} > "$LOG"

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    echo "profile=$PLC_FUSION_PIPELINE"
    echo "reason=${PLC_FUSION_PROFILE_REASON:-}"
    echo "pre=$PLC_FUSION_PRE_PASSES"
    echo "kernel=$PLC_FUSION_KERNEL_PASS"
    echo "tail=$PLC_FUSION_TAIL_PASSES"
    echo "opt=$OPT_PASSES"
    echo "log=$LOG"
fi
