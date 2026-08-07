# K-Fusion repo path helpers (source from any script)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
KFUSION_ROOT="$REPO_ROOT/kfusion"
CRTOS_UPSTREAM="$REPO_ROOT/crtos/upstream/fixstars"
export REPO_ROOT KFUSION_ROOT CRTOS_UPSTREAM
