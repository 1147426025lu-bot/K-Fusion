#!/bin/bash
# 恢复 rt_host_isolate__CPU隔离.sh 的 L2/L3 改动
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/rt_host_isolate__CPU隔离.sh" teardown
