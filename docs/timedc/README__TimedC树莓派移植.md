# Timed C (KTC) on Raspberry Pi 5 / aarch64

Upstream [timed-c/ktc](https://github.com/timed-c/ktc) ships prebuilt `lib/libktc.a` (x86-64) and `lib/libktcrasp.a` (32-bit ARM). On **Pi 5 aarch64** you must rebuild the runtime and the OCaml compiler locally.

## Quick start

```bash
# 1) Install OCaml/opam, build KTC, rebuild aarch64 runtime libs
bash scripts/timedc/install_ktc_rpi5__安装KTC.sh

# 2) Smoke test (soft sdelay demo)
bash scripts/timedc/run_demo__运行示例.sh

# 3) Paper-style 1 ms periodic loop (same CPU isolation as cyclictest)
MEASURE_KIND=soak DURATION_MIN=15 bash scripts/timedc/run_paper_timedc_periodic__论文TimedC周期.sh
```

## Native vs `--rasp`

| Mode | When | Command |
|------|------|---------|
| **Native POSIX (Pi 5)** | KTC runs **on** the Pi; output links with `libktc.a` | `./ktc --enable-ext0 --link source.c` |
| **`--rasp` cross** | x86 host → 32-bit armhf Pi (legacy README) | Not used on aarch64 Pi |

On this platform we compile **natively** with `gcc` (aarch64). Do **not** pass `--rasp` unless you intentionally target the old 32-bit rasp runtime.

## Troubleshooting

### `opam init` / curl exit code 56

Transient network failure while downloading `https://opam.ocaml.org/index.tar.gz` (common on Pi Wi‑Fi). Retry:

```bash
opam init --disable-sandboxing -y --bare
# or re-run the install script (includes retries):
bash scripts/timedc/install_ktc_rpi5__安装KTC.sh
```

### `No package named cil`

Upstream README is outdated. Use **goblint-cil 1.7.3** on an **OCaml 4.14** opam switch (`ktc-4.14`); the install script handles this.

### Link duplicate symbols (`list_pr`, `boolvar`, …)

KTC-generated `.cil.c` defines runtime globals from `cillib.h`; `libktc.a` must compile `fprofile.c` with `-DKTC_RUNTIME_LIB` so those symbols stay `extern` in the archive only. The runtime rebuild script does this automatically.

### Modern kernel headers / `__int128` parse errors

KTC/CIL 1.7.3 cannot parse glibc 2.41 uapi on Pi 5. The install path uses `scripts/timedc/ktc_gcc_wrapper.sh` (`-include ktc_pi_header_shim.h`, `-U__SIZEOF_INT128__`) automatically when compiling Timed C sources.

- `third_party/ktc/timedc-lib/src/fprofile.c`: add `__aarch64__` `sched_setattr` syscall numbers (274/275).
- `scripts/timedc/build_runtime_aarch64__重编运行时库.sh`: rebuild `libktc.a` / `libktcrasp.a` from source.

## PLCFusion vs Timed C (paper)

| | PLCFusion (3 baselines) | Timed C |
|--|-------------------------|---------|
| 源码 | `test/rt-tests/src/cyclictest/cyclictest.c` | 同源参数；交付为 `examples/timedc/cyclictest_paper__论文同源.c` |
| 用户态 | `cyclictest -p99 -i1000 ...` | KTC → `sdelay(1,ms)` 周期 + 同协议测量 |
| 矩阵脚本 | `run_paper_baseline_matrix__论文基线矩阵.sh` | 含 `timedc` 行（`SKIP_TIMEDC=1` 可跳过） |

```bash
# 四路矩阵：userspace | baseline_ko | timedc | fused  × soak/stress
PAPER_RUNS=3 DURATION_MIN=5 bash scripts/paper/run_paper_baseline_matrix__论文基线矩阵.sh

# 仅 Timed C 单次
MEASURE_KIND=soak DURATION_MIN=15 bash scripts/paper/run_paper_timedc__论文TimedC.sh
```

## Directory layout

```
third_party/ktc/          # upstream clone (gitignored build artifacts)
examples/timedc/          # paper workloads
scripts/timedc/           # install / build / run helpers
results/paper/timedc/     # measurement logs (via paper_results_dir)
```
