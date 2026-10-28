#!/usr/bin/env bash
# gate6_selftest__自测.sh — 跑 Gate 6 消融矩阵
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KF="$(cd "$HERE/../.." && pwd)"
python3 "$KF/scripts/paper/gate6_ablation__消融反例.py"
