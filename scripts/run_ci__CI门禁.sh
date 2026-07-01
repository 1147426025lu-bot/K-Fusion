#!/bin/bash
# ============================================================================
# run_ci__CI门禁.sh — 无 insmod 的编译/融合/覆盖率 CI 门禁
# ============================================================================
# 功能: 编译 Pass → 融合 cyclictest + signaltest → plc_fuse_check 门禁
# 用法: bash scripts/run_ci__CI门禁.sh
# 环境: SKIP_BUILD=1  跳过 cmake/make；MANIFESTS="..." 自定义清单列表
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
FUSE="$SCRIPT_DIR/plc_fuse__内核化主流程.sh"
CHECK="$SCRIPT_DIR/plc_fuse_check__覆盖率门禁.sh"
VALIDATE="$SCRIPT_DIR/plc_fuse_validate__安全验证器JSON.sh"
WCET="$SCRIPT_DIR/plc_fusion_wcet_sweep__tail对照.sh"

DEFAULT_MANIFESTS=(
    "$PRJ/manifests/manifest_cyclictest__主线压测.env"
    "$PRJ/manifests/manifest_cyclictest__多TU压测.env"
    "$PRJ/manifests/manifest_signaltest__信号测试.env"
    "$PRJ/manifests/manifest_ptsematest__互斥锁测试.env"
    "$PRJ/manifests/manifest_github_rt_periodic__周期demo.env"
    "$PRJ/manifests/manifest_github_rt_periodic_multitu__多TU.env"
    "$PRJ/manifests/manifest_plc_cc_gpio__PLC示例.env"
    "$PRJ/manifests/manifest_plc_cc_pure_logic__纯逻辑.env"
    "$PRJ/manifests/manifest_plc_cc_temp_control__温控.env"
    "$PRJ/manifests/manifest_plc_cc_isolation__隔离测试.env"
    "$PRJ/manifests/manifest_plc_cc_dither__抖动测试.env"
    "$PRJ/manifests/manifest_plc_cc_hello__入门.env"
)

export PLC_FUSE_STRICT_MISSING=1
export PLC_FUSE_STRICT_VALIDATE=1
export PLC_FUSION_FIXED_POINT=1
# CI 不修改 manifests/（fill-empty 仅用于 onboarding，见 plc_ast_apply_manifest --dry-run）
export FUSE_AST_APPLY_SUGGEST=0

LITE_MANIFESTS=(
    "$PRJ/manifests/manifest_github_rt_periodic__周期demo.env"
    "$PRJ/manifests/manifest_github_rt_periodic_multitu__多TU.env"
    "$PRJ/manifests/manifest_plc_cc_gpio__PLC示例.env"
    "$PRJ/manifests/manifest_plc_cc_pure_logic__纯逻辑.env"
    "$PRJ/manifests/manifest_plc_cc_temp_control__温控.env"
    "$PRJ/manifests/manifest_plc_cc_isolation__隔离测试.env"
    "$PRJ/manifests/manifest_plc_cc_dither__抖动测试.env"
    "$PRJ/manifests/manifest_plc_cc_hello__入门.env"
)

if [ -n "${MANIFESTS:-}" ]; then
    # shellcheck disable=SC2206
    MANIFEST_LIST=($MANIFESTS)
elif [ "${CI_LITE:-0}" = "1" ]; then
    MANIFEST_LIST=("${LITE_MANIFESTS[@]}")
    echo "    mode=CI_LITE (${#MANIFEST_LIST[@]} manifests, 无 rt-tests)"
else
    MANIFEST_LIST=("${DEFAULT_MANIFESTS[@]}")
fi

echo "=== PLCFusion CI 门禁 ==="
echo "    project=$PRJ"
echo "    manifests=${#MANIFEST_LIST[@]} (+ validate strict + wcet sweep)"

echo "🔗 [1a] fuse 符号链接门禁..."
bash "$SCRIPT_DIR/run_fuse_symlink_check__fuse符号链接门禁.sh"

echo "🔗 [1a1] deploy profile 符号链接门禁..."
bash "$SCRIPT_DIR/run_deploy_symlink_check__deploy符号链接门禁.sh"

echo "🧹 [1a2] 仓库整洁门禁..."
bash "$SCRIPT_DIR/run_repo_clean_check__仓库整洁门禁.sh"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
    echo "🛠️ [1/3] 编译 PLCFusionPass + PLCLowJitterPass + plc_ast..."
    if ! (cd "$PRJ/build" && cmake .. >/dev/null && make PLCFusionPass PLCLowJitterPass plc_ast -j"$(nproc)" >/dev/null); then
        plc_die "$PLC_E_BUILD" "Pass / plc_ast 编译失败"
    fi
else
    echo "⏭️  SKIP_BUILD=1，跳过 Pass 编译"
fi
plc_require_file "$PRJ/build/PLCFusionPass.so" "Pass 插件"
plc_require_file "$PRJ/build/PLCLowJitterPass.so" "LowJitter Pass 插件"
plc_require_file "$PRJ/build/plc_ast" "plc_ast 分析器"

echo "🔬 [1b] plc-cc AST 门禁..."
bash "$SCRIPT_DIR/run_plc_cc_ast_ci__plc-cc分析门禁.sh"

echo "📋 [1c] manifest 建议 smoke（gpio）..."
bash "$SCRIPT_DIR/plc_ast_suggest_manifest__manifest建议.sh" \
    "$PRJ/examples/plc-cc__低抖动示例/gpio_blink__GPIO闪烁.c" \
    "$PRJ/test/ci_suggest_gpio.manifest.suggest.env" >/dev/null
grep -q FUSE_KTHREAD_ENTRY "$PRJ/test/ci_suggest_gpio.manifest.suggest.env"

echo "📊 [1d] 全 manifest AST 矩阵..."
bash "$SCRIPT_DIR/run_ci_ast_manifest_matrix__CI_AST矩阵.sh"

echo "📋 [1e] Pass 策略矩阵..."
bash "$SCRIPT_DIR/run_ci_pipeline_policy__CI_Pass策略矩阵.sh"

echo "📋 [1f] manifest onboarding 门禁..."
bash "$SCRIPT_DIR/run_manifest_onboarding_check__manifest清单门禁.sh"

echo "📋 [1g] apply-manifest dry-run smoke（gpio）..."
bash "$SCRIPT_DIR/plc_ast_apply_manifest__应用manifest建议.sh" \
    "$PRJ/manifests/manifest_plc_cc_gpio__PLC示例.env" --dry-run >/dev/null

echo "🔮 [1h] AST → pipeline plan smoke（gpio）..."
export PLC_FUSION_IR_UNKNOWN_EXTERNS=0 PLC_FUSION_IR_LINES=1000
export PLC_FUSION_IR_HAS_FLOAT=0 PLC_FUSION_OBJ_BYTES=0
out=$(bash "$SCRIPT_DIR/fuse/plc_fusion_pipeline__Pass组合选择.sh" \
    "$PRJ/manifests/manifest_plc_cc_gpio__PLC示例.env")
echo "$out" | grep -q 'ast:plc_entry'

echo "🔮 [2/3] 融合..."
for m in "${MANIFEST_LIST[@]}"; do
    plc_require_file "$m" "manifest"
    echo "    -> $(basename "$m")"
    bash "$FUSE" "$m"
done

echo "✅ [3/4] 覆盖率门禁..."
for m in "${MANIFEST_LIST[@]}"; do
    echo "    check $(basename "$m")"
    bash "$CHECK" "$m"
done

echo "✅ [4/4] JSON 验证..."
for m in "${MANIFEST_LIST[@]}"; do
    echo "    validate $(basename "$m")"
    bash "$VALIDATE" "$m"
done

CYCLIC="$PRJ/manifests/manifest_cyclictest__主线压测.env"
if [ -f "$CYCLIC" ]; then
    echo "📊 WCET tail 对照（cyclictest 主线）..."
    WCET_SWEEP_RUN_FUSE=0 bash "$WCET" "$CYCLIC"
    echo "📊 WCET autotune 静态（cyclictest 主线）..."
    WCET_AUTOTUNE_SKIP_INSMOD=1 bash "$SCRIPT_DIR/fuse/plc_fusion_wcet_autotune__WCET自动调优.sh" "$CYCLIC" \
        || plc_warn "WCET autotune 未完成（可忽略于无 pre.ll 环境）"
fi

echo "📦 全类 .ko 链接编译（无 insmod）..."
KO_BUILD_FORCE_FUSE=0 bash "$SCRIPT_DIR/run_ko_build__全类ko编译.sh"

echo "🧪 功能门禁（kernel.o + modpost）..."
bash "$SCRIPT_DIR/run_functional_ci__功能门禁.sh"

if [ "${CI_INSMOD:-0}" = "1" ]; then
    if sudo -n true 2>/dev/null; then
        echo "🔌 功能门禁 insmod 短测（plc_cc_hello + github_rt_periodic）..."
        FUNCTIONAL_INSMOD=1 \
        FUNCTIONAL_MANIFESTS="$PRJ/manifests/manifest_plc_cc_hello__入门.env $PRJ/manifests/manifest_github_rt_periodic__周期demo.env" \
            bash "$SCRIPT_DIR/run_functional_ci__功能门禁.sh"
    else
        echo "⏭️  CI_INSMOD=1 但无免密 sudo，跳过 insmod（Pi 上可 sudo -v 后重跑）"
    fi
fi

if [ "${CI_PLATFORM_X86:-0}" = "1" ]; then
    echo "🖥️  x86_64 平台交叉构建验证（无 insmod）..."
    PLC_PLATFORM=x86_64 bash "$SCRIPT_DIR/platform/validate_build__平台构建验证.sh"
fi

echo ""
echo "✅ CI 门禁全部通过（${#MANIFEST_LIST[@]} 个 manifest + validate + wcet sweep + ko build + functional${CI_PLATFORM_X86:+ + x86 cross})"
