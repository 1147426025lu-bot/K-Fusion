#!/bin/bash
# Clone/build Linux Foundation rt-tests (same source as PLCFusion cyclictest manifest).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RT_TESTS="${RT_TESTS_DIR:-$PROJECT_ROOT/test/rt-tests}"
GIT_URL="${RT_TESTS_GIT_URL:-git://git.kernel.org/pub/scm/utils/rt-tests/rt-tests.git}"
DEPTH="${RT_TESTS_GIT_DEPTH:-1}"

if [ ! -d "$RT_TESTS/.git" ]; then
    echo "=== clone rt-tests → $RT_TESTS ==="
    git clone --depth "$DEPTH" "$GIT_URL" "$RT_TESTS"
fi

CYCLIC_SRC="$RT_TESTS/src/cyclictest/cyclictest.c"
if [ ! -f "$CYCLIC_SRC" ]; then
    echo "missing $CYCLIC_SRC" >&2
    exit 1
fi

if [ "${BUILD_CYCLICTEST:-0}" = "1" ] && [ ! -x "$RT_TESTS/cyclictest" ]; then
    echo "=== build rt-tests cyclictest ==="
    make -C "$RT_TESTS" -j"$(nproc)" cyclictest
fi

echo "rt-tests ok: $CYCLIC_SRC"
