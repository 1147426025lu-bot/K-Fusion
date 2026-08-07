#!/bin/bash
# ============================================================================
# run_ci_pi__Pi板级CI.sh — Raspberry Pi / PREEMPT_RT 板级 CI 子集
# ============================================================================
# 用法（Pi 上）:
#   sudo -v
#   CI_WCET_PROBE=1 bash scripts/run_ci_pi__Pi板级CI.sh
#
# GitHub self-hosted runner 标签建议: [self-hosted, Linux, ARM64, rpi5]
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PRJ="$(plc_project_root)"
# shellcheck source=platform/plc_source_platform__加载平台.sh
source "$SCRIPT_DIR/platform/plc_source_platform__加载平台.sh"

echo "=== K-Fusion Pi 板级 CI (platform=${PLATFORM_ID:-?}) ==="

export CI_WCET_PROBE="${CI_WCET_PROBE:-1}"
export CI_CYCLICTEST_MULTITU="${CI_CYCLICTEST_MULTITU:-1}"
export CI_REQUIRE_LLVM19="${CI_REQUIRE_LLVM19:-0}"
export FUSE_STRICT="${FUSE_STRICT:-1}"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
    PASS_TARGET="$(plc_fusion_pass_target)"
    echo "🛠️  编译 ${PASS_TARGET}..."
    (cd "$PRJ/build" && cmake .. >/dev/null && make "$PASS_TARGET" PLCLowJitterPass -j"$(nproc)" >/dev/null)
fi

bash "$PRJ/backend/test/run_pass_unit_tests__Pass单元测试.sh"
bash "$SCRIPT_DIR/run_ci_wcet_per_function__函数级WCET门禁.sh" \
    "$PRJ/manifests/manifest_github_rt_periodic__周期demo.env"
bash "$SCRIPT_DIR/run_ci_cyclictest_multitu__多TU门禁.sh"
bash "$SCRIPT_DIR/run_ci_wcet_probe__板级探针门禁.sh"

echo "✅ Pi 板级 CI 子集通过"
