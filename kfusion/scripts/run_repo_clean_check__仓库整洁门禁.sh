#!/bin/bash
# ============================================================================
# run_repo_clean_check__仓库整洁门禁.sh — 仓库不应跟踪生成物
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"

REPO="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
cd "$REPO"

FAIL=0
check_no_track() {
    local pat="$1"
    local desc="$2"
    local hits
    hits=$(git ls-files "$pat" 2>/dev/null || true)
    if [ "$pat" = 'kfusion/test/*_runtime_stubs.c' ]; then
        hits=$(echo "$hits" | grep -v '^kfusion/test/plc_runtime_stubs\.c$' || true)
    fi
    if [ -n "$hits" ]; then
        echo "    FAIL tracked $desc:"
        echo "$hits" | sed 's/^/      /'
        FAIL=1
    fi
}

echo "=== 仓库整洁门禁 (repo=$REPO) ==="
check_no_track 'kfusion/test/*.validate.json' 'validate.json'
check_no_track 'kfusion/test/*.fusion_report' 'fusion_report'
check_no_track 'kfusion/test/*.detected.env' 'detected.env'
check_no_track 'kfusion/test/*.host_profile.env' 'host_profile.env'
check_no_track 'kfusion/test/*.entries' 'entries'
check_no_track 'kfusion/test/*.remap_hints' 'remap_hints'
check_no_track 'kfusion/test/*.unmapped' 'unmapped'
check_no_track 'kfusion/test/*.wcet_sweep.tsv' 'wcet_sweep.tsv'
check_no_track 'kfusion/test/*.discover_tu.log' 'discover_tu.log'
check_no_track 'kfusion/test/*.preflight.log' 'preflight.log'
check_no_track 'kfusion/test/*.pipeline*.log' 'pipeline logs'
check_no_track 'kfusion/test/*.ir_analysis.log' 'ir_analysis.log'
check_no_track 'kfusion/test/*_runtime_stubs.c' 'generated runtime stubs'
check_no_track 'kfusion/test/.official_cycletest_wcet_*' 'wcet sweep cache'
check_no_track 'kfusion/test/platform_*' 'platform test workspace'
check_no_track 'kfusion/test/quick_validate' 'quick_validate workspace'
check_no_track 'kfusion/build/*' 'build artifacts'

bash "$SCRIPT_DIR/run_fuse_symlink_check__fuse符号链接门禁.sh" >/dev/null

if [ "$FAIL" -ne 0 ]; then
    plc_die "${PLC_E_VALIDATE:-11}" "仓库含不应跟踪的生成物" \
        "运行: git rm --cached <paths> 并确认 .gitignore"
fi
echo "✅ 仓库整洁门禁通过"
