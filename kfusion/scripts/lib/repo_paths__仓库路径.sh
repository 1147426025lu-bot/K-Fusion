# K-Fusion repo path helpers (source from any script)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
KFUSION_ROOT="$REPO_ROOT/kfusion"
# 历史脚本仍读 PLCFUSION_ROOT / RESULTS_ROOT
PLCFUSION_ROOT="${PLCFUSION_ROOT:-$KFUSION_ROOT}"
RESULTS_ROOT="${RESULTS_ROOT:-/home/pi/kfusion_results}"
CRTOS_ROOT="$REPO_ROOT/crtos"
CRTOS_UPSTREAM="$CRTOS_ROOT/upstream/starstars"
CRTOS_JAILHOUSE="$CRTOS_ROOT/upstream/jailhouse"
export REPO_ROOT KFUSION_ROOT PLCFUSION_ROOT RESULTS_ROOT
export CRTOS_ROOT CRTOS_UPSTREAM CRTOS_JAILHOUSE
