# K-Fusion repo path helpers (source from any script)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
KFUSION_ROOT="$REPO_ROOT/kfusion"
CRTOS_ROOT="$REPO_ROOT/crtos"
CRTOS_UPSTREAM="$CRTOS_ROOT/upstream/fixstars"
CRTOS_JAILHOUSE="$CRTOS_ROOT/upstream/jailhouse"
export REPO_ROOT KFUSION_ROOT CRTOS_ROOT CRTOS_UPSTREAM CRTOS_JAILHOUSE
