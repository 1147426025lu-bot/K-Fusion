#!/bin/bash
# ============================================================================
# plc_fuse__内核化主流程.sh — PLCFusion 融合主流程
# ============================================================================
# 功能: git/本地 C 源码 → Clang IR → 预清理 → 自动探测 → IR 分析 →
#       pipeline 选 profile → 内核化 Pass → LLC → 自动桩合并
# 输入: manifest.env（FUSE_NAME, FUSE_SOURCE, FUSE_EXTRA_SOURCES, FUSE_GIT_URL 等）
# 输出（test/）:
#   ${FUSE_NAME}.ll / _pre.ll / _kernel.ll / _kernel.o
#   ${FUSE_NAME}.detected.env / .pipeline.log / .ir_analysis.log
# 用法:
#   bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_cyclictest__主线压测.env
# 环境:
#   FUSE_AUTO_DETECT=1  FUSE_AUTO_STUBS=1  FUSE_AUTO_REFINE=1（缺符号时重跑 debug）
#   FUSE_STRICT=1       自动探测/桩合并失败时中止（默认 0 仅警告）
#   PLC_BUILD_LOG=      构建日志路径（失败时打印）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
BUILD_DIR="$PROJECT_ROOT/build"
TEST_DIR="$PROJECT_ROOT/test"
FUSION_SO="$(plc_fusion_pass_so "$PROJECT_ROOT" "$BUILD_DIR")"
PASS_TARGET="$(plc_fusion_pass_target)"
LOW_JITTER_SO="$BUILD_DIR/PLCLowJitterPass.so"
FUSE_STRICT="${FUSE_STRICT:-0}"
if [ "$FUSE_STRICT" = "1" ]; then
    export FUSE_STRICT_VALIDATE="${FUSE_STRICT_VALIDATE:-1}"
    export FUSE_AST_PREFLIGHT_STRICT="${FUSE_AST_PREFLIGHT_STRICT:-1}"
    export FUSE_AST_INDIRECT_STRICT="${FUSE_AST_INDIRECT_STRICT:-1}"
fi

MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"
plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest" \
    "复制 manifests/manifest_template__清单模板.env 并修改" \
    "用法: bash scripts/plc_fuse__内核化主流程.sh manifests/your_app.env"

plc_source_manifest "$MANIFEST"

# 平台 LLC / 隔离参数（覆盖 manifest 内 FUSE_LLC_*）
# shellcheck source=../platform/plc_source_platform__加载平台.sh
source "$(plc_scripts_root)/platform/plc_source_platform__加载平台.sh"
echo "🖥️  platform=${PLATFORM_ID:-?} (${PLATFORM_LABEL:-}) llc=${FUSE_LLC_ARCH}/${FUSE_LLC_ATTR}"
# 子脚本会再次 source manifest 并覆盖 FUSE_LLC_*；平台 LLC 单独保留
_PLATFORM_LLC_ARCH="${FUSE_LLC_ARCH:-aarch64}"
_PLATFORM_LLC_ATTR="${FUSE_LLC_ATTR:--fp-armv8,-neon}"
_PLATFORM_CLANG_TARGET="${FUSE_CLANG_TARGET:-}"

FUSE_DESC="${FUSE_DESC:-$FUSE_NAME}"
FUSE_GIT_DIR="${FUSE_GIT_DIR:-}"
FUSE_GIT_DEPTH="${FUSE_GIT_DEPTH:-1}"
FUSE_INCLUDE_DIRS="${FUSE_INCLUDE_DIRS:-}"
FUSE_CLANG_FLAGS="${FUSE_CLANG_FLAGS:--O2 -fno-builtin}"
CLANG_TARGET_ARGS=()
if [ -n "${_PLATFORM_CLANG_TARGET:-${FUSE_CLANG_TARGET:-}}" ]; then
    _host_m="$(uname -m 2>/dev/null || echo unknown)"
    case "$_host_m" in
        x86_64|amd64)
            CLANG_TARGET_ARGS=(-target "${_PLATFORM_CLANG_TARGET:-$FUSE_CLANG_TARGET}")
            ;;
    esac
fi
FUSE_GLOBALIZE_SYMBOLS="${FUSE_GLOBALIZE_SYMBOLS:-}"
FUSE_KTHREAD_ENTRY="${FUSE_KTHREAD_ENTRY:-}"
FUSE_LLC_ARCH="${FUSE_LLC_ARCH:-aarch64}"
FUSE_LLC_ATTR="${FUSE_LLC_ATTR:--fp-armv8,-neon}"
FUSE_LLC_RELOC="${FUSE_LLC_RELOC:-static}"
FUSE_WORK_DIR="${FUSE_WORK_DIR:-$TEST_DIR}"

AUTOTUNE_ENV="$FUSE_WORK_DIR/${FUSE_NAME}.autotune.env"
if [ -f "$AUTOTUNE_ENV" ] && [ "${FUSE_WCET_USE_AUTOTUNE:-1}" = "1" ]; then
    echo "    WCET autotune env: $AUTOTUNE_ENV"
    plc_load_kv_env_file "$AUTOTUNE_ENV"
fi

plc_ensure_dir "$FUSE_WORK_DIR"

# Pass 策略：ast-auto（一次定案） vs wcet-benchmark（可后续搜索）
# shellcheck source=plc_fusion_pipeline_policy__Pass策略解析.sh
source "$SCRIPT_DIR/plc_fusion_pipeline_policy__Pass策略解析.sh" "$MANIFEST"
echo "    pass_policy=${PLC_FUSION_PIPELINE_POLICY} ast_plan=${FUSE_AST_PLAN:-?} wcet_search=${FUSE_WCET_SEARCH:-?}"

if [ "${FUSE_AST_APPLY_SUGGEST:-0}" = "1" ] && [ "${PLC_FUSION_PIPELINE_POLICY:-}" != "fixed" ]; then
    echo "📝 [2a] manifest fill-empty（AST 建议）..."
    if bash "$PROJECT_ROOT/scripts/plc_ast_apply_manifest__应用manifest建议.sh" "$MANIFEST"; then
        plc_source_manifest "$MANIFEST"
    else
        plc_warn "manifest fill-empty 未完成（可继续融合）"
    fi
fi

OUT_LL="$FUSE_WORK_DIR/${FUSE_NAME}.ll"
OUT_PRE="$FUSE_WORK_DIR/${FUSE_NAME}_pre.ll"
OUT_KLL="$FUSE_WORK_DIR/${FUSE_NAME}_kernel.ll"
OUT_OBJ="$FUSE_WORK_DIR/${FUSE_NAME}_kernel.o"

CLANG_BIN="$(plc_resolve_tool CLANG_BIN clang-19 clang-18 clang-17 clang)"
OPT_BIN="$(plc_resolve_tool OPT_BIN opt-19 opt-18 opt-17 opt)"
LLC_BIN="$(plc_resolve_tool LLC_BIN llc-19 llc-18 llc-17 llc)"

echo "=== K-Fusion fuse: ${FUSE_DESC} ==="
echo "    manifest=$MANIFEST"
echo "    output=$OUT_OBJ"
[ -n "$FUSE_KTHREAD_ENTRY" ] && echo "    kthread_entry=$FUSE_KTHREAD_ENTRY (宿主须调用)"

echo "🛠️ [1/6] 编译 LLVM Pass..."
mkdir -p "$BUILD_DIR"
PASS_SRC="$PROJECT_ROOT/backend/pass/PLCFusionPass__内核化Pass.cpp"
if [ ! -f "$PASS_SRC" ]; then
    plc_die "$PLC_E_NOFILE" "Pass 源码缺失: $PASS_SRC" "确认在 K-Fusion/kfusion 目录执行"
fi
if [ ! -f "$FUSION_SO" ] || [ "$PASS_SRC" -nt "$FUSION_SO" ]; then
    if ! (cd "$BUILD_DIR" && cmake .. >/dev/null && make "$PASS_TARGET" -j"$(nproc)" >/dev/null); then
        plc_die "$PLC_E_BUILD" "${PASS_TARGET}.so 编译失败" \
            "检查 build/ 下 cmake 输出" \
            "需安装 llvm-19-dev 与兼容的 C++ 编译器"
    fi
    FUSION_SO="$(plc_fusion_pass_so "$PROJECT_ROOT" "$BUILD_DIR")"
fi
LOW_JITTER_SRC="$PROJECT_ROOT/backend/pass/PLCLowJitterPass__低抖动Pass.cpp"
if [ ! -f "$LOW_JITTER_SO" ] || [ "$LOW_JITTER_SRC" -nt "$LOW_JITTER_SO" ]; then
    if ! (cd "$BUILD_DIR" && cmake .. >/dev/null && make PLCLowJitterPass -j"$(nproc)" >/dev/null); then
        plc_die "$PLC_E_BUILD" "PLCLowJitterPass.so 编译失败"
    fi
fi
plc_require_file "$FUSION_SO" "Pass 插件" \
    "手动: cd build && cmake .. && make ${PASS_TARGET}"

if [ -n "${FUSE_SRC_ROOT:-}" ]; then
    if [[ "$FUSE_SRC_ROOT" = /* ]]; then
        SRC_ROOT="$FUSE_SRC_ROOT"
    else
        SRC_ROOT="$PROJECT_ROOT/$FUSE_SRC_ROOT"
    fi
else
    SRC_ROOT="$FUSE_WORK_DIR"
fi
if [ -n "${FUSE_GIT_URL:-}" ]; then
    echo "📥 [2/6] 准备上游 git: $FUSE_GIT_URL"
    if [ -z "$FUSE_GIT_DIR" ]; then
        plc_die "$PLC_E_MANIFEST" "设置了 FUSE_GIT_URL 但缺少 FUSE_GIT_DIR" \
            "示例: FUSE_GIT_DIR=rt-tests"
    fi
    SRC_ROOT="$FUSE_WORK_DIR/$FUSE_GIT_DIR"
    plc_git_sync "$FUSE_GIT_URL" "$SRC_ROOT" "$FUSE_GIT_DEPTH" "${FUSE_GIT_BRANCH:-}" "${FUSE_GIT_UPDATE:-0}"
elif [ -n "$FUSE_GIT_DIR" ]; then
    SRC_ROOT="$FUSE_WORK_DIR/$FUSE_GIT_DIR"
    if [ ! -d "$SRC_ROOT" ]; then
        plc_die "$PLC_E_NOFILE" "FUSE_GIT_DIR 不存在: $SRC_ROOT" \
            "设置 FUSE_GIT_URL 自动 clone，或手动放置源码"
    fi
else
    echo "📥 [2/6] 使用本地源码（无 git）"
fi

SOURCE_PATH="$SRC_ROOT/$FUSE_SOURCE"
if [ ! -f "$SOURCE_PATH" ]; then
    plc_die "$PLC_E_NOFILE" "源文件不存在: $SOURCE_PATH" \
        "检查 manifest 中 FUSE_SOURCE / FUSE_GIT_DIR / FUSE_INCLUDE_DIRS" \
        "确认 [2/6] clone 已成功"
fi

FUSE_PREFLIGHT="${FUSE_PREFLIGHT:-1}"
if [ "$FUSE_PREFLIGHT" = "1" ]; then
    echo "🔎 [2b] 源码预检..."
    bash "$SCRIPT_DIR/plc_fusion_preflight__源码预检.sh" "$MANIFEST" || \
        plc_warn "预检发现问题（见 test/${FUSE_NAME}.preflight.log）"
fi

# plc-cc：Clang 之前做 AST 静态分析（JSON → .plc_ast.env）
if [[ "${FUSE_NAME:-}" == plc_cc_* ]] && [ "${FUSE_PLC_CC_AST:-1}" = "1" ]; then
    echo "🔬 [2c] plc-cc AST 静态分析..."
    bash "$SCRIPT_DIR/plc_fuse_plc_cc_ast__AST探测.sh" "$MANIFEST" "$SOURCE_PATH"
    AST_ENV="$FUSE_WORK_DIR/${FUSE_NAME}.plc_ast.env"
    if [ -f "$AST_ENV" ]; then
        # shellcheck disable=SC1090
        source "$AST_ENV"
        if [ -z "${FUSE_KTHREAD_ENTRY:-}" ] && [ -n "${FUSE_DETECT_PLC_CC_ENTRY:-}" ]; then
            FUSE_KTHREAD_ENTRY="$FUSE_DETECT_PLC_CC_ENTRY"
            echo "    ast → FUSE_KTHREAD_ENTRY=$FUSE_KTHREAD_ENTRY"
        fi
        if [ -z "${FUSE_DCE_ROOTS:-}" ] && [ -n "${FUSE_DETECT_PLC_CC_ENTRY:-}" ]; then
            FUSE_DCE_ROOTS="$FUSE_DETECT_PLC_CC_ENTRY"
            echo "    ast → FUSE_DCE_ROOTS=$FUSE_DCE_ROOTS"
        fi
        if [ -z "${FUSE_HOT_PATH_FUNCTIONS:-}" ] && [ -n "${FUSE_DETECT_PLC_CC_ENTRY:-}" ]; then
            FUSE_HOT_PATH_FUNCTIONS="$FUSE_DETECT_PLC_CC_ENTRY"
            echo "    ast → FUSE_HOT_PATH_FUNCTIONS=$FUSE_HOT_PATH_FUNCTIONS"
        fi
        if [ "${FUSE_DETECT_PLC_CC_FLOAT_IN_CYCLE:-0}" = "1" ]; then
            echo "    ast ⚠️  周期函数含浮点（WCET/软浮点风险）"
        fi
    fi
fi

FUSE_EXTRA_SOURCES="${FUSE_EXTRA_SOURCES:-}"
FUSE_AUTO_DISCOVER_TU="${FUSE_AUTO_DISCOVER_TU:-1}"
if [ "$FUSE_AUTO_DISCOVER_TU" = "1" ] && [ -z "$FUSE_EXTRA_SOURCES" ]; then
    DISCOVERED_TU="$(bash "$SCRIPT_DIR/plc_fusion_discover_tu__TU自动发现.sh" "$MANIFEST" 2>/dev/null || true)"
    if [ -n "$DISCOVERED_TU" ]; then
        FUSE_EXTRA_SOURCES="$DISCOVERED_TU"
        echo "    auto FUSE_EXTRA_SOURCES=$FUSE_EXTRA_SOURCES"
    fi
fi
SOURCE_PATHS=("$SOURCE_PATH")
for rel in $FUSE_EXTRA_SOURCES; do
    extra="$SRC_ROOT/$rel"
    if [ ! -f "$extra" ]; then
        plc_die "$PLC_E_NOFILE" "FUSE_EXTRA_SOURCES 文件不存在: $extra" \
            "路径相对 FUSE_GIT_DIR（或本地源码根）" \
            "示例: FUSE_EXTRA_SOURCES='src/lib/histogram.c'"
    fi
    SOURCE_PATHS+=("$extra")
done

INCLUDE_ARGS=()
for inc in $FUSE_INCLUDE_DIRS; do
    inc_path="$SRC_ROOT/$inc"
    if [ ! -d "$inc_path" ]; then
        plc_warn "include 目录不存在，跳过: $inc_path"
        continue
    fi
    INCLUDE_ARGS+=(-I "$inc_path")
done

IR_TMP_DIR="$FUSE_WORK_DIR/.${FUSE_NAME}_ir_tmp"
rm -rf "$IR_TMP_DIR"
mkdir -p "$IR_TMP_DIR"

echo "🔮 [3/6] Clang → LLVM IR (${#SOURCE_PATHS[@]} TU)..."
IR_PARTS=()
idx=0
for src in "${SOURCE_PATHS[@]}"; do
    part="$IR_TMP_DIR/tu_${idx}.ll"
    clang_failed=0
    echo "    [$((idx + 1))/${#SOURCE_PATHS[@]}] $src"
    # shellcheck disable=SC2086
    if [ -n "${PLC_BUILD_LOG:-}" ]; then
        if ! "$CLANG_BIN" "${CLANG_TARGET_ARGS[@]}" $FUSE_CLANG_FLAGS \
            -S -emit-llvm \
            "$src" \
            "${INCLUDE_ARGS[@]}" \
            -o "$part" 2>>"$PLC_BUILD_LOG"; then
            clang_failed=1
        fi
    elif ! "$CLANG_BIN" "${CLANG_TARGET_ARGS[@]}" $FUSE_CLANG_FLAGS \
        -S -emit-llvm \
        "$src" \
        "${INCLUDE_ARGS[@]}" \
        -o "$part"; then
        clang_failed=1
    fi
    if [ "${clang_failed:-0}" = "1" ]; then
        plc_die "$PLC_E_BUILD" "Clang 生成 IR 失败: $src" \
            "检查源文件编译错误与 FUSE_CLANG_FLAGS" \
            "缺头文件时补充 FUSE_INCLUDE_DIRS"
    fi
    plc_require_file "$part" "Clang TU IR"
    IR_PARTS+=("$part")
    idx=$((idx + 1))
done

if [ "${#IR_PARTS[@]}" -eq 1 ]; then
    cp -f "${IR_PARTS[0]}" "$OUT_LL"
else
    LINK_BIN="$(plc_resolve_tool LLVM_LINK_BIN llvm-link-19 llvm-link-18 llvm-link-17 llvm-link)"
    echo "    llvm-link → $OUT_LL"
    if ! "$LINK_BIN" -S -o "$OUT_LL" "${IR_PARTS[@]}"; then
        plc_die "$PLC_E_BUILD" "llvm-link 合并 IR 失败" \
            "检查多 TU 是否有重复全局符号" \
            "工具: $LINK_BIN"
    fi
fi
plc_require_file "$OUT_LL" "合并后 IR"
rm -rf "$IR_TMP_DIR"

PLC_FUSION_PRE_PASSES="function(mem2reg,instcombine,simplifycfg)"

run_kernel_and_llc() {
    FUSE_LLC_ARCH="${_PLATFORM_LLC_ARCH:-${FUSE_LLC_ARCH:-aarch64}}"
    FUSE_LLC_ATTR="${_PLATFORM_LLC_ATTR:-${FUSE_LLC_ATTR:--fp-armv8,-neon}}"
    echo "🌌 [5/6] PLCFusion Pass（POSIX → plc_* + 自动 DCE）..."
    export PLC_FUSION_UNMAPPED_LOG="$FUSE_WORK_DIR/${FUSE_NAME}.unmapped"
    : > "$PLC_FUSION_UNMAPPED_LOG"

    FUSE_DCE="${FUSE_DCE:-1}"
    FUSE_AUTO_OPT="${FUSE_AUTO_OPT:-1}"
    if [ -n "${FUSE_DCE_ROOTS:-}" ]; then
        PLC_FUSION_ROOTS="$FUSE_DCE_ROOTS"
    elif [ "${FUSE_RUN_MAIN:-0}" = "1" ]; then
        PLC_FUSION_ROOTS="main"
    elif [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
        PLC_FUSION_ROOTS="$FUSE_KTHREAD_ENTRY"
    else
        PLC_FUSION_ROOTS=""
    fi
    export PLC_FUSION_DCE="$FUSE_DCE"
    export PLC_FUSION_ROOTS
    if [ -n "${FUSE_FIXED_POINT:-}" ]; then
        export PLC_FUSION_FIXED_POINT="$FUSE_FIXED_POINT"
    fi
    if [ -n "${FUSE_DETECT_GLOBALS:-}" ] && [ -z "${FUSE_GLOBALIZE_SYMBOLS:-}" ]; then
        export PLC_FUSION_KEEP_GLOBALS="$FUSE_DETECT_GLOBALS"
    else
        export PLC_FUSION_KEEP_GLOBALS="$FUSE_GLOBALIZE_SYMBOLS"
        if [ -n "${FUSE_DETECT_GLOBALS:-}" ] && [ -n "${FUSE_GLOBALIZE_SYMBOLS:-}" ]; then
            export PLC_FUSION_KEEP_GLOBALS="${FUSE_GLOBALIZE_SYMBOLS} ${FUSE_DETECT_GLOBALS}"
        fi
    fi
    if [ -n "${FUSE_HOT_PATH_FUNCTIONS:-}" ]; then
        export PLC_FUSION_HOT_PATH_FUNCTIONS="$FUSE_HOT_PATH_FUNCTIONS"
    elif [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
        export PLC_FUSION_HOT_PATH_FUNCTIONS="$FUSE_KTHREAD_ENTRY"
    else
        unset PLC_FUSION_HOT_PATH_FUNCTIONS
    fi

    [ -n "$PLC_FUSION_ROOTS" ] && echo "    dce_roots=$PLC_FUSION_ROOTS pipeline=$PLC_FUSION_PIPELINE"
    echo "    reason=${PLC_FUSION_PROFILE_REASON:-} opt=$OPT_PASSES low_jitter=${FUSE_LOW_JITTER:-0}"

    OPT_PLUGIN_ARGS=(-load-pass-plugin="$FUSION_SO")
    if [ "${FUSE_LOW_JITTER:-0}" = "1" ]; then
        plc_require_file "$LOW_JITTER_SO" "LowJitter Pass 插件"
        OPT_PLUGIN_ARGS+=(-load-pass-plugin="$LOW_JITTER_SO")
    fi

    if ! "$OPT_BIN" "${OPT_PLUGIN_ARGS[@]}" \
        -passes="$OPT_PASSES" \
        "$OUT_PRE" -S -o "$OUT_KLL"; then
        plc_die "$PLC_E_BUILD" "PLCFusion Pass 失败" \
            "Pass 插件: $FUSION_SO" \
            "pipeline: $OPT_PASSES（见 test/${FUSE_NAME}.pipeline.log）" \
            "DCE 删多了可设 FUSE_PIPELINE=debug 或 PLC_FUSION_DCE=0" \
            "符号未映射见 test/${FUSE_NAME}.unmapped"
    fi
    plc_require_file "$OUT_KLL" "内核化 IR"

    echo "⚙️ [6/6] LLC → ${FUSE_NAME}_kernel.o (${FUSE_LLC_ARCH}, reloc=${FUSE_LLC_RELOC})..."
    if ! "$LLC_BIN" -O3 -relocation-model="$FUSE_LLC_RELOC" \
        -march="$FUSE_LLC_ARCH" -mattr="$FUSE_LLC_ATTR" \
        -filetype=obj "$OUT_KLL" -o "$OUT_OBJ"; then
        plc_die "$PLC_E_BUILD" "LLC 生成 .o 失败" \
            "检查 FUSE_LLC_ARCH / FUSE_LLC_ATTR 是否匹配目标 CPU" \
            "Raspberry Pi: aarch64 + -fp-armv8,-neon"
    fi

    if [ -n "$FUSE_GLOBALIZE_SYMBOLS" ]; then
        OBJCOPY_BIN="$(plc_resolve_objcopy "$FUSE_LLC_ARCH")"
        OBJCOPY_ARGS=()
        for sym in $FUSE_GLOBALIZE_SYMBOLS; do
            OBJCOPY_ARGS+=(--globalize-symbol="$sym")
        done
        if ! "$OBJCOPY_BIN" "${OBJCOPY_ARGS[@]}" "$OUT_OBJ"; then
            plc_die "$PLC_E_BUILD" "objcopy --globalize-symbol 失败" \
                "确认符号在 IR 中存在: $FUSE_GLOBALIZE_SYMBOLS"
        fi
    fi
    if [ "${FUSE_MERGE_LLVM_BSS:-1}" = "1" ]; then
        plc_fuse_merge_llvm_bss "$OUT_OBJ" "$FUSE_LLC_ARCH"
    fi
    OBJCOPY_BIN="$(plc_resolve_objcopy "$FUSE_LLC_ARCH")"
    if command -v "$OBJCOPY_BIN" >/dev/null 2>&1; then
        "$OBJCOPY_BIN" --remove-section=.eh_frame "$OUT_OBJ" 2>/dev/null || true
    fi

    cp -f "$OUT_OBJ" "${OUT_OBJ}_shipped"

    ENTRIES_FILE="$FUSE_WORK_DIR/${FUSE_NAME}.entries"
    grep -E '^define .* @(signalthread|timerthread|semathread|main|worker|thread|plc_cycle|plc_main|plc_logic)\(' "$OUT_KLL" 2>/dev/null | \
        sed -E 's/^define .* @([^ (]+).*/\1/' | sort -u > "$ENTRIES_FILE" || true
    if [ -n "${FUSE_KTHREAD_ENTRY:-}" ]; then
        if [ ! -f "$ENTRIES_FILE" ] || ! grep -qx "${FUSE_KTHREAD_ENTRY}" "$ENTRIES_FILE" 2>/dev/null; then
            echo "${FUSE_KTHREAD_ENTRY}" >> "$ENTRIES_FILE"
        fi
    fi
    if [ -s "$ENTRIES_FILE" ]; then
        echo "    kthread entries: $(tr '\n' ' ' < "$ENTRIES_FILE")"
    else
        plc_warn "未发现融合入口（timerthread/main 等）" \
            "检查 DCE 是否删掉了入口：FUSE_DCE_ROOTS / FUSE_PIPELINE=debug"
    fi
}

echo "🧹 [4/6] IR 预清理..."
plc_run_step "opt pre-clean" "$OPT_BIN" -passes="$PLC_FUSION_PRE_PASSES" \
    "$OUT_LL" -S -o "$OUT_PRE"

FUSE_AUTO_DETECT="${FUSE_AUTO_DETECT:-1}"
if [ "$FUSE_AUTO_DETECT" = "1" ]; then
    echo "🔍 [4b] 自动探测入口 / DCE roots..."
    if bash "$SCRIPT_DIR/plc_fuse_detect__入口探测.sh" "$MANIFEST" "$OUT_PRE" "$SOURCE_PATH"; then
        DETECTED="$FUSE_WORK_DIR/${FUSE_NAME}.detected.env"
        if [ -f "$DETECTED" ]; then
            # shellcheck disable=SC1090
            source "$DETECTED"
            if [ "${FUSE_RUN_MAIN:-0}" != "1" ] && [ -z "${FUSE_KTHREAD_ENTRY:-}" ] && [ -n "${FUSE_DETECT_KTHREAD_ENTRY:-}" ]; then
                FUSE_KTHREAD_ENTRY="$FUSE_DETECT_KTHREAD_ENTRY"
                echo "    auto FUSE_KTHREAD_ENTRY=$FUSE_KTHREAD_ENTRY"
            fi
            if [ -z "${FUSE_DCE_ROOTS:-}" ] && [ -n "${FUSE_DETECT_DCE_ROOTS:-}" ]; then
                FUSE_DCE_ROOTS="$FUSE_DETECT_DCE_ROOTS"
                echo "    auto FUSE_DCE_ROOTS=$FUSE_DCE_ROOTS"
            fi
            if [ -z "${FUSE_HOT_PATH_FUNCTIONS:-}" ] && [ -n "${FUSE_DETECT_PLC_CC_ENTRY:-}" ]; then
                FUSE_HOT_PATH_FUNCTIONS="$FUSE_DETECT_PLC_CC_ENTRY"
                echo "    ast(detect) FUSE_HOT_PATH_FUNCTIONS=$FUSE_HOT_PATH_FUNCTIONS"
            fi
        fi
    else
        if [ "$FUSE_STRICT" = "1" ]; then
            plc_die "$PLC_E_IR" "自动探测失败（FUSE_STRICT=1）" \
                "手动在 manifest 设置 FUSE_KTHREAD_ENTRY / FUSE_DCE_ROOTS"
        fi
        plc_warn "自动探测失败，使用 manifest 中的手动配置"
    fi
fi

echo "📊 [4c] IR 特征分析 + pipeline 选择..."
if [ "${FUSE_WCET_TAIL_PROBE:-0}" = "1" ] \
    && [ "${PLC_FUSION_PIPELINE_POLICY:-}" = "wcet-benchmark" ] \
    && [ -f "$OUT_PRE" ]; then
    echo "🔬 [4c-] WCET tail 短测（静态 3 组）..."
    TAIL_PICK="$SCRIPT_DIR/plc_fusion_wcet_tail_pick__短测选tail.sh"
    TAIL_ENV="$FUSE_WORK_DIR/${FUSE_NAME}.wcet_tail_pick.env"
    if bash "$TAIL_PICK" "$MANIFEST" "$OUT_PRE" && [ -f "$TAIL_ENV" ]; then
        # shellcheck disable=SC1090
        source "$TAIL_ENV"
        echo "    tail_pick=$(basename "$TAIL_ENV") cold=${FUSE_COLD_PASS_SEQUENCE:-}"
    fi
fi
if ! source "$SCRIPT_DIR/plc_fusion_analyze_ir__IR特征分析.sh" "$MANIFEST" "$OUT_PRE" pre; then
    plc_die "$PLC_E_IR" "IR 分析失败" "确认 $OUT_PRE 存在且可读"
fi
if ! source "$SCRIPT_DIR/plc_fusion_pipeline__Pass组合选择.sh" "$MANIFEST"; then
    plc_die "$PLC_E_BUILD" "pipeline 选择失败" \
        "检查 FUSE_PIPELINE 取值（mainline|generic|minimal|debug|size|custom|auto）"
fi
echo "    pipeline=${PLC_FUSION_PIPELINE} unknown=${PLC_FUSION_IR_UNKNOWN_EXTERNS:-0} float=${PLC_FUSION_IR_HAS_FLOAT:-?}"

run_kernel_and_llc

FUSE_AUTO_STUBS="${FUSE_AUTO_STUBS:-1}"
if [ "$FUSE_AUTO_STUBS" = "1" ]; then
    echo "🧩 [6b] 自动合并 runtime 桩..."
    if ! bash "$SCRIPT_DIR/plc_fuse_merge_stubs__桩合并.sh" "$MANIFEST"; then
        if [ "$FUSE_STRICT" = "1" ]; then
            plc_die "$PLC_E_BUILD" "桩合并失败（FUSE_STRICT=1）"
        fi
        plc_warn "桩合并失败，将使用 src/plc_runtime_stubs__POSIX桩.c"
    fi
    FUSE_STUB_LOOP="${FUSE_STUB_LOOP:-1}"
    if [ "$FUSE_STUB_LOOP" = "1" ]; then
        bash "$SCRIPT_DIR/plc_fuse_stub_loop__桩闭环.sh" "$MANIFEST" || \
            plc_warn "桩闭环未完全收敛，见 plc_fuse_report"
    fi
fi

FUSE_AUTO_REFINE="${FUSE_AUTO_REFINE:-1}"
FUSE_DEBUG_THRESHOLD="${FUSE_DEBUG_THRESHOLD:-3}"
if [ "$FUSE_AUTO_REFINE" = "1" ] && [ "${FUSE_PIPELINE:-auto}" = "auto" ] \
    && [ "$PLC_FUSION_PIPELINE" != "debug" ] \
    && [ "${FUSE_WCET_MODE:-0}" != "1" ]; then
    if source "$SCRIPT_DIR/plc_fusion_analyze_ir__IR特征分析.sh" "$MANIFEST" "$OUT_KLL" kernel; then
        if [ "${PLC_FUSION_IR_UNKNOWN_EXTERNS:-0}" -ge "$FUSE_DEBUG_THRESHOLD" ]; then
            echo "🔄 [6c] 缺符号=${PLC_FUSION_IR_UNKNOWN_EXTERNS}，自动 refine → debug pipeline..."
            FUSE_PIPELINE=debug
            if source "$SCRIPT_DIR/plc_fusion_pipeline__Pass组合选择.sh" "$MANIFEST"; then
                export PLC_FUSION_FIXED_POINT="${PLC_FUSION_FIXED_POINT:-${FUSE_FIXED_POINT:-1}}"
                run_kernel_and_llc
                if [ "$FUSE_AUTO_STUBS" = "1" ]; then
                    bash "$SCRIPT_DIR/plc_fuse_merge_stubs__桩合并.sh" "$MANIFEST" || \
                        plc_warn "refine 后桩合并失败，检查 test/${FUSE_NAME}_kernel.ll"
                fi
            else
                plc_warn "refine pipeline 切换失败，保留首次融合结果"
            fi
        fi
    fi
fi

echo "✅ ${OUT_OBJ}（及 ${OUT_OBJ}_shipped）"
bash "$SCRIPT_DIR/plc_fusion_remap_hints__映射建议.sh" "$MANIFEST" || \
    plc_warn "remap 建议生成失败"
bash "$SCRIPT_DIR/plc_fuse_fusion_report__一页报告.sh" "$MANIFEST" || \
    plc_warn "融合报告生成失败（不影响 .o 产物）"
if ! bash "$SCRIPT_DIR/plc_fuse_validate__安全验证器JSON.sh" "$MANIFEST"; then
    if [ "${PLC_FUSE_STRICT_VALIDATE:-${FUSE_STRICT_VALIDATE:-0}}" = "1" ]; then
        plc_die "$PLC_E_BUILD" "JSON 验证未通过（FUSE_STRICT_VALIDATE=1）" \
            "见 test/${FUSE_NAME}.validate.json"
    fi
    plc_warn "JSON 验证未通过（见 test/${FUSE_NAME}.validate.json）"
fi

if [ "${FUSE_WCET_AUTOTUNE:-0}" = "1" ] && [ "${PLC_FUSION_PIPELINE_POLICY:-}" = "wcet-benchmark" ]; then
    echo "📊 [7/7] WCET autotune（FUSE_WCET_AUTOTUNE=1）..."
    if ! WCET_AUTOTUNE_SKIP_INSMOD="${WCET_AUTOTUNE_SKIP_INSMOD:-1}" \
        bash "$SCRIPT_DIR/plc_fusion_wcet_autotune__WCET自动调优.sh" "$MANIFEST"; then
        if [ "$FUSE_STRICT" = "1" ]; then
            plc_die "$PLC_E_BUILD" "WCET autotune 失败（FUSE_STRICT=1）" \
                "见 test/${FUSE_NAME}.wcet_autotune.json"
        fi
        plc_warn "WCET autotune 未完成（见 test/${FUSE_NAME}.wcet_autotune.json）"
    fi
fi

echo "   报告:   test/${FUSE_NAME}.fusion_report"
echo "   映射:   test/${FUSE_NAME}.remap_hints"
echo "   验证:   test/${FUSE_NAME}.validate.json"
echo "   策略:   test/${FUSE_NAME}.pipeline_policy.log"
if [ "${FUSE_WCET_SEARCH:-0}" = "1" ]; then
    echo "   WCET搜索: WCET_AUTOTUNE_SKIP_INSMOD=1 bash scripts/fuse/plc_fusion_wcet_autotune__WCET自动调优.sh $MANIFEST"
else
    echo "   WCET:   bash scripts/fuse/plc_fusion_wcet_sweep__tail对照.sh $MANIFEST"
fi
echo "   覆盖率: bash scripts/plc_fuse_report__覆盖率报告.sh $MANIFEST"
echo "   cyclictest 主线: bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh"
echo "   通用模块:       bash scripts/ignite_fused__通用ko构建.sh $MANIFEST"
