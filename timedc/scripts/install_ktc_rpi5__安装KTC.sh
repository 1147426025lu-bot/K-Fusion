#!/bin/bash
# Install OCaml/opam deps, build KTC compiler, rebuild aarch64 runtime libs on Pi.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=timedc_common__TimedC公共.sh
source "$SCRIPT_DIR/timedc_common__TimedC公共.sh"

ROOT="$(timedc_root)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

wait_for_dpkg_lock() {
    local waited=0
    while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
        || sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1; do
        if [ "$waited" -eq 0 ]; then
            echo "Waiting for other apt/dpkg to finish..."
        fi
        sleep 5
        waited=$((waited + 5))
        if [ "$waited" -ge 1800 ]; then
            echo "Timed out waiting for dpkg lock (>30 min)." >&2
            echo "Check: sudo fuser /var/lib/dpkg/lock-frontend" >&2
            exit 1
        fi
    done
}

if [ ! -d "$ROOT/.git" ]; then
    mkdir -p "$(dirname "$ROOT")"
    git clone --depth 1 https://github.com/timed-c/ktc.git "$ROOT"
fi

bash "$SCRIPT_DIR/apply_ktc_overlay__应用KTC覆盖.sh"

need_sudo=0
for pkg in ocaml ocaml-native-compilers opam; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        need_sudo=1
        break
    fi
done

if [ "$need_sudo" = 1 ]; then
    wait_for_dpkg_lock
    echo "Installing OCaml toolchain (sudo required)..."
    sudo apt-get update
    sudo apt-get install -y \
        ocaml ocaml-native-compilers opam m4 mercurial ocaml-findlib ocamlbuild \
        libnum-ocaml-dev libgmp-dev pkg-config
fi

opam_init_with_retry() {
    local attempt max_attempts=5
    for attempt in $(seq 1 "$max_attempts"); do
        echo "=== opam init (attempt ${attempt}/${max_attempts}) ==="
        if opam init --disable-sandboxing -y --bare; then
            return 0
        fi
        echo "opam init failed (often curl 56 on slow links); retrying in 10s..." >&2
        sleep 10
    done
    echo "opam init failed after ${max_attempts} attempts." >&2
    echo "Try manually: curl -fL --retry 5 -o /tmp/index.tar.gz https://opam.ocaml.org/index.tar.gz" >&2
    echo "Then: opam init --disable-sandboxing -y --bare" >&2
    return 1
}

opam_install_with_retry() {
    local pkg="$1"
    local attempt max_attempts=5
    for attempt in $(seq 1 "$max_attempts"); do
        if opam install -y "$pkg"; then
            return 0
        fi
        echo "opam install $pkg failed; retrying (${attempt}/${max_attempts})..." >&2
        sleep 10
    done
    return 1
}

link_goblint_cil_perl() {
    local opam_lib
    opam_lib="$(opam var lib 2>/dev/null)/perl5"
    if [ ! -d "$opam_lib/App" ]; then
        echo "goblint-cil Perl libs not found under $opam_lib" >&2
        return 1
    fi
    mkdir -p "$ROOT/cil/bin" "$ROOT/cil/lib"
    ln -sfn "$opam_lib/App" "$ROOT/cil/lib/App"
}

build_goblint_cil_from_source() {
    timedc_eval_opam
    local src
    src="$(opam var prefix)/../.opam-switch/sources/goblint-cil.1.7.3"
    if [ ! -d "$src" ]; then
        OPAMYES=1 OPAMASSUMEDEPEXTS=1 opam install -y goblint-cil.1.7.3
        src="$(opam var prefix)/../.opam-switch/sources/goblint-cil.1.7.3"
    fi
    echo "=== rebuild goblint-cil 1.7.3 from source (Usedef/liveness link fix) ==="
    cd "$src"
    ./configure --prefix="$(opam var prefix)" >/dev/null
    make -j"$(nproc)"
    make install >/dev/null
}

install_opam_deps() {
    timedc_eval_opam
    if ! opam switch show >/dev/null 2>&1; then
        opam switch create "$TIMEDC_OPAM_SWITCH" 4.14.2 --yes
    fi
    timedc_eval_opam
    if ! dpkg -s libgmp-dev >/dev/null 2>&1; then
        sudo apt-get install -y libgmp-dev pkg-config
    fi
    if ! opam list --installed goblint-cil 2>/dev/null | grep -q "1.7.3"; then
        OPAMYES=1 OPAMASSUMEDEPEXTS=1 opam_install_with_retry "goblint-cil.1.7.3"
    fi
    for pkg in yojson csv; do
        if ! opam list --installed "$pkg" 2>/dev/null | grep -q "^${pkg}[[:space:]]"; then
            OPAMYES=1 OPAMASSUMEDEPEXTS=1 opam_install_with_retry "$pkg"
        fi
    done
    build_goblint_cil_from_source
    link_goblint_cil_perl
}

if [ ! -d "$HOME/.opam" ]; then
    opam_init_with_retry
fi

install_opam_deps

echo "=== make KTC (OCaml compiler) ==="
cd "$ROOT"
timedc_eval_opam
make clean >/dev/null 2>&1 || true
make

if [ ! -x "$ROOT/bin/ktcexe" ]; then
    echo "KTC build failed: missing $ROOT/bin/ktcexe" >&2
    exit 1
fi

bash "$SCRIPT_DIR/build_runtime_aarch64__重编运行时库.sh"

echo
echo "KTC ready:"
echo "  bin/ktc -> $ROOT/bin/ktc"
echo "  try: bash scripts/timedc/run_demo__运行示例.sh"
