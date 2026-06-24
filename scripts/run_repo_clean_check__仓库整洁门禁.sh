#!/bin/bash
# ============================================================================
# run_repo_clean_check__仓库整洁门禁.sh — 仓库不应跟踪生成物
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
PRJ="$(plc_project_root)"
cd "$PRJ"

FAIL=0
check_no_track() {
    local pat="$1"
    local desc="$2"
    local hits
    hits=$(git ls-files "$pat" 2>/dev/null || true)
    if [ "$pat" = 'test/*_runtime_stubs.c' ]; then
        hits=$(echo "$hits" | grep -v '^test/plc_runtime_stubs\.c$' || true)
    fi
    if [ -n "$hits" ]; then
        echo "    FAIL tracked $desc:"
        echo "$hits" | sed 's/^/      /'
        FAIL=1
    fi
}

echo "=== 仓库整洁门禁 ==="
check_no_track 'test/*.validate.json' 'test validate.json'
check_no_track 'test/*.fusion_report' 'test fusion_report'
check_no_track 'test/*_runtime_stubs.c' 'generated runtime stubs'
check_no_track 'test/.official_cycletest_wcet_*' 'wcet sweep cache'
check_no_track 'build/*' 'build artifacts'

bash "$SCRIPT_DIR/run_fuse_symlink_check__fuse符号链接门禁.sh" >/dev/null

if [ "$FAIL" -ne 0 ]; then
    plc_die "${PLC_E_VALIDATE:-11}" "仓库含不应跟踪的生成物" \
        "运行 git rm --cached 并确认 .gitignore"
fi
echo "✅ 仓库整洁门禁通过"
