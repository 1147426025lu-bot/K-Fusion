#!/bin/bash
# Initialize Fixstars cRTOS submodules (HTTPS, no git@ SSH).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../../plcfusion/scripts/lib/repo_paths__仓库路径.sh"
PRJ="$REPO_ROOT"
ROOT="$CRTOS_UPSTREAM"

[ -d "$ROOT/.git" ] || bash "$SCRIPT_DIR/clone_crtos_reference__克隆cRTOS参考.sh"

cd "$ROOT"
git config submodule.cRTOS-nuttx.url https://github.com/fixstars/cRTOS-nuttx.git
git config submodule.cRTOS-loader.url https://github.com/fixstars/cRTOS-loader.git
git config submodule.cRTOS-drivers.url https://github.com/fixstars/cRTOS-drivers.git
git config submodule.cRTOS-nuttx-apps.url https://github.com/fixstars/cRTOS-nuttx-apps.git
git config submodule.cRTOS-jailhouse.url https://github.com/fixstars/cRTOS-jailhouse.git

git submodule update --init --depth 1 crtos-jailhouse crtos-loader crtos-drivers crtos-nuttx crtos-nuttx-apps

echo "✅ cRTOS submodules:"
ls -d crtos-*/ 2>/dev/null
