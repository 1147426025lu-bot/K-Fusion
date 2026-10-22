#!/usr/bin/env bash
# nuttx_target__NuttX目标构建桥.sh
#
# 目的：把 NuttX 版 cyclictest 经 K-Fusion 内核化后，变成能直接链进 NuttX 的 .o，
#       且「跑 pass 的机器」完全不需要 NuttX 源码树。
#
# 为什么需要这座桥：两张卡分工，且不能同时插
#   - NuttX 卡（Debian 11）：有 NuttX 源码树 + aarch64-none-elf 工具链，没有 LLVM
#   - LLVM 卡（Debian 13）：有 clang/opt/llc 19，没有 NuttX
#   中间只传 135 KB 的预处理文件和 18 KB 的目标文件。
#
# 四个阶段（①③④ 在 NuttX 卡，② 在 LLVM 卡）：
#   ① emit      gcc -E                → cyclictest_nuttx.i（自包含，0 个 #include）
#                 + 归一化 GCC 扩展属性 → cyclictest_nuttx.clang.i（clang 才吃）
#   ② fuse      clang → IR → opt(pass) → llc → fused.o
#                 同时产出 base.o（同 IR、不过 pass）用于 A/B 归因
#   ③ stub      gcc 编 plc_runtime_stubs__NuttX桩.c → stubs.o
#                 ld -r 合并 → cyclictest_combined.o（符号收敛回 27 个 POSIX）
#   ④ install   替换 apps/benchmarks/cyclictest/*.o → make → 校验 ELF
#
# 实测（2026-10-19，Pi4B / LLVM 19.1.7 / clang 19）：
#   cyclictest.c 1115 行 → .i 135 KB → fused.o 18120 B（base.o 20008 B，−9.4%）
#   浮点指令 3 → 0（FixedPoint Pass），调用点 70 处走 plc_* 抽象层
#   未定义符号：27 POSIX（gcc）→ 17 plc_* + 5 libc（fused）→ 合并桩后回到 27
#
# 用法：
#   ./nuttx_target__NuttX目标构建桥.sh emit
#   ./nuttx_target__NuttX目标构建桥.sh fuse   [--pass...] [--roots main] [--hot testthread]
#   ./nuttx_target__NuttX目标构建桥.sh stub [--direct-time]
#   ./nuttx_target__NuttX目标构建桥.sh install [--dry-run]
#   ./nuttx_target__NuttX目标构建桥.sh restore
#   ./nuttx_target__NuttX目标构建桥.sh verify <.o>
#
set -euo pipefail

KF_ROOT="${KF_ROOT:-/root/K-Fusion/kfusion}"
NUTTX_BUILD="${NUTTX_BUILD:-/root/kfusion-pi4/build/nuttx-build}"
APP_DIR="${APP_DIR:-$NUTTX_BUILD/apps/benchmarks/cyclictest}"
SRC_C="${SRC_C:-$APP_DIR/cyclictest.c}"
STUB_C="${STUB_C:-$KF_ROOT/src/plc_runtime_stubs__NuttX桩.c}"
TOOLCHAIN="${TOOLCHAIN:-/opt/arm/arm-gnu-toolchain-13.2.Rel1-aarch64-aarch64-none-elf/bin}"
WORK="${WORK:-$KF_ROOT/test/nuttx_target}"
I_RAW="$WORK/cyclictest_nuttx.i"
I_FILE="$WORK/cyclictest_nuttx.clang.i"
O_FUSED="$WORK/fused.o"
O_BASE="$WORK/base.o"
O_STUB="$WORK/stubs.o"
O_COMBINED="$WORK/cyclictest_combined.o"

FUSION_SO="${FUSION_SO:-$KF_ROOT/build/KFusionPass.so}"
LOW_JITTER_SO="${LOW_JITTER_SO:-$KF_ROOT/build/PLCLowJitterPass.so}"
OPT_LEVEL="${OPT_LEVEL:--O2}"
# 编译器可按版本覆盖（单卡合并后，本机可能装的是 clang-16/opt-16/llc-16）
# FP_CONTRACT=off 是为绕开 FixedPoint 的 fmuladd bug；定点化关闭时可设 on（允许 FMA 融合）
FP_CONTRACT="${FP_CONTRACT:-fast}"
CLANG_BIN="${CLANG_BIN:-clang}"
OPT_BIN="${OPT_BIN:-opt}"
LLC_BIN="${LLC_BIN:-llc}"
TARGET="${TARGET:-aarch64-none-elf}"
CPU="${CPU:-cortex-a72}"
LLC_MATTR="${LLC_MATTR:--fp-armv8,-neon}"
ROOTS="${ROOTS:-main}"                       # DCE 根：NuttX 版 cyclictest 的入口是 main
HOT_FUNCS="${HOT_FUNCS:-testthread}"
# NuttX builtin 入口符号：<app目录名>_main（Application.mk 会传 -Dmain=$(PROGNAME)_main）
ENTRY_SYM="${ENTRY_SYM:-$(basename "${APP_DIR:-x}")_main}"         # LowJitter 作用对象：测量线程
OPT_PASSES_KERNEL="${OPT_PASSES_KERNEL:-plc-fusion,plc-low-jitter}"

log() { printf '\033[1;36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*" >&2; }
ok()  { printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }

# NuttX 能提供给 cyclictest 的全部符号（27 个，2026-10-19 实测 gcc 编译结果）
# 追加 __errno / vfprintf：NuttX 桩用到、原程序没用到，已在 nuttx ELF 里确认存在（T）
ALLOWED="calloc clock_gettime clock_nanosleep close fprintf free getoptargp getoptindp
getopt_long getpid gettid ioctl lib_get_stream malloc memset open perror poll printf
pthread_create pthread_join pthread_self pthread_setschedparam puts strcmp strtol usleep
__errno vfprintf"

# 校验 .o 的外部依赖是否都能由 NuttX 解析。
#
# 判据分两级，优先用「真实 ELF 符号表」而不是硬编码清单：
#   ① 强判据：每个未定义符号都必须在 nuttx ELF 的符号表里存在（T/W/D/B/R/C）
#   ② 兜底：ELF 不存在时退回 27 符号白名单（2026-10-19 gcc 直编实测）
#
# 为什么不用纯白名单：白名单来自「原版 cyclictest 的 27 个依赖」，
# 但 NuttX 桩会用到 vfprintf / __errno 这类 NuttX 有、原程序没用的符号，
# 硬编码会把合法依赖误判成违规（实测踩过）。
check_symbols() {
  local obj="$1" nmbin="${2:-nm}" und have unexpected missing s
  [ -f "$obj" ] || die "找不到 $obj"
  und=$("$nmbin" -u "$obj" | awk '{print $NF}' | sort -u)
  [ -n "$und" ] || die "$obj 没有未定义符号——.o 可能没编成功"
  echo "  未定义符号数: $(echo "$und" | wc -l)"

  # plc_* 必须全部被桩解析掉，残留即为致命错误
  local left_plc
  left_plc=$(echo "$und" | grep '^plc_' || true)
  [ -z "$left_plc" ] || { echo "  ⚠ 未解析的 plc_*: $(echo "$left_plc" | tr '\n' ' ')"; return 4; }

  local elf="$NUTTX_BUILD/nuttx/nuttx"
  if [ -f "$elf" ] && command -v "${nmbin}" >/dev/null 2>&1; then
    have=$("$nmbin" "$elf" 2>/dev/null | awk '$2 ~ /^[TWDBCRV]$/ {print $3}' | sort -u)
    if [ -n "$have" ]; then
      unexpected=$(comm -23 <(echo "$und") <(echo "$have"))
      if [ -n "$unexpected" ]; then
        echo "  ⚠ NuttX ELF 里没有的符号: $(echo "$unexpected" | tr '\n' ' ')"
        return 2
      fi
      echo "  ✓ 全部符号均可由 NuttX ELF 解析"
      return 0
    fi
  fi

  # 兜底：静态白名单
  echo "  （未能读 ELF 符号表，退回静态白名单）"
  unexpected=""; missing=""
  for s in $und; do
    echo "$ALLOWED" | tr -s ' \n' '\n\n' | grep -qxF "$s" || unexpected="$unexpected $s"
  done
  for s in $ALLOWED; do
    echo "$und" | grep -qxF "$s" || missing="$missing $s"
  done
  [ -z "$unexpected" ] || { echo "  ⚠ 白名单外符号: $unexpected"; return 2; }
  [ -z "$missing" ]    || { echo "  ⚠ 缺少: $missing"; return 3; }
  return 0
}

# ---------------- ① emit ----------------
stage_emit() {
  log "阶段 ①：预处理 + 属性归一化（只用 gcc，不需要 LLVM）"
  export PATH="$TOOLCHAIN:$PATH"
  command -v aarch64-none-elf-gcc >/dev/null || die "找不到 aarch64-none-elf-gcc"
  [ -f "$SRC_C" ] || die "找不到源码 $SRC_C"
  mkdir -p "$WORK"

  log "源码: $SRC_C ($(wc -l < "$SRC_C") 行)"
  # EMIT_DEFS：额外传给预处理器的宏。典型用法：
  #   EMIT_DEFS='-DBM2_SCAN_ATTR=__attribute__((noinline))'
  # 让实时任务函数保持独立（冻结 / WCET 对照实验的前提）。
  aarch64-none-elf-gcc -E "$SRC_C" -o "$I_RAW" \
    -I"$NUTTX_BUILD/nuttx/include" -I"$NUTTX_BUILD/apps/include" \
    -D__NuttX__ -D__KERNEL__=0 -mcpu="$CPU" -fno-builtin -D_GNU_SOURCE \
    ${EMIT_DEFS:-}
  local inc; inc=$(grep -c '^#include' "$I_RAW" || true)
  [ "$inc" -eq 0 ] || die "$I_RAW 仍有 $inc 个 #include，不自包含"
  ok ".i 生成: $I_RAW ($(stat -c%s "$I_RAW") 字节, 0 个 #include)"

  # GCC 的 __malloc__(deallocator, idx) 是 GCC 扩展，clang 报
  #   "__malloc__ attribute takes no arguments"
  # 归一化成 clang 接受的 __malloc__。这一步是「gcc 预处理 + clang 编译」的固有代价，
  # 根治办法是用 clang 预处理（需在 NuttX 卡装 clang），暂不划算。
  cp "$I_RAW" "$I_FILE"
  sed -i -E 's/__malloc__\([^)]*\)/__malloc__/g' "$I_FILE"
  ok "属性归一化: $(grep -cE '__malloc__\([^)]*\)' "$I_FILE" || true) 处残留（应为 0）"

  aarch64-none-elf-gcc -x cpp-output -c "$I_FILE" -o /tmp/_selfcheck.o \
    -nostdinc -mcpu="$CPU" -Os -fno-builtin -ffreestanding 2>/dev/null \
    || die "自包含复验失败（-nostdinc 编不过）"
  ok "自包含复验通过（-nostdinc 可编，未定义符号 $(aarch64-none-elf-nm -u /tmp/_selfcheck.o | wc -l) 个）"
  rm -f /tmp/_selfcheck.o

  echo
  echo "拷到 LLVM 卡： $I_FILE"
  echo "sha256: $(sha256sum "$I_FILE" | awk '{print $1}')"
}

# ---------------- ② fuse ----------------
stage_fuse() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --pass)  FUSION_SO="$2"; shift 2 ;;
      --low)   LOW_JITTER_SO="$2"; shift 2 ;;
      --roots) ROOTS="$2"; shift 2 ;;
      --hot)   HOT_FUNCS="$2"; shift 2 ;;
      --opt)   OPT_LEVEL="$2"; shift 2 ;;
      *) die "fuse 未知参数: $1" ;;
    esac
  done
  log "阶段 ②：内核化（本机不需要 NuttX）"
  [ -f "$I_FILE" ] || die "找不到 $I_FILE —— 先在 NuttX 卡跑 emit 并拷过来"
  [ -f "$FUSION_SO" ] || die "找不到 $FUSION_SO"
  command -v "$CLANG_BIN" >/dev/null || die "找不到 $CLANG_BIN"
  command -v "$OPT_BIN"   >/dev/null || die "找不到 $OPT_BIN"
  command -v "$LLC_BIN"   >/dev/null || die "找不到 $LLC_BIN"
  mkdir -p "$WORK"
  local F="$WORK/fuse_work"; rm -rf "$F"; mkdir -p "$F"

  log "clang: $($CLANG_BIN --version | head -1)"

  # ⚠ --hot 的语义是「冻结这个函数，让它的机器码不再被后端优化」
  #   （PLCLowJitterPass 加 noinline + optnone，RTSS 风格的时间可预测性）。
  #   若把 main 当 hot 函数，等于冻结整份程序（含全部热循环），
  #   实测执行时间 +17.6%、p50 回退 1µs —— 那不是 pass 的代价，是冻结的代价。
  if printf '%s' "$HOT_FUNCS" | tr ',' '\n' | grep -qx 'main'; then
    warn "--hot 含 main：整份程序会被 optnone 冻结，执行时间约 +18%。"
    warn "   想要「pass 的真实代价」就别给 --hot；想要「WCET 冻结」才给，且别给 main。"
  fi
  log "roots=$ROOTS hot=$HOT_FUNCS passes=$OPT_PASSES_KERNEL"

  log "[1/4] clang → IR"
  # -ffp-contract 控制 FMA 融合（三种取值的实测差异，2026-10-21）：
  #   off  → 不融合；FP 指令最多（67 条），最慢。
  #   on   → 仅同一表达式内融合，且会生成 llvm.fmuladd intrinsic
  #          → 触发 FixedPoint Pass 的 fmuladd bug（操作数转 i64 但签名不改 → opt abort）。
  #   fast → 跨语句融合（= gcc 默认行为），产出 `fmul contract`，**不生成 fmuladd**，
  #          因此既最快、也不会触发定点化那个 bug。
  # 默认 fast：与 gcc 默认一致，且不会触发 fmuladd bug（实测比 off 快 4.2%）。
  # 若需要严格 IEEE 结合顺序（逐位一致），显式传 FP_CONTRACT=on（注意会生成 fmuladd）。
  "$CLANG_BIN" --target="$TARGET" -mcpu="$CPU" "$OPT_LEVEL" -fno-builtin -ffreestanding \
    -fno-stack-protector -ffp-contract="$FP_CONTRACT" -x cpp-output -S -emit-llvm "$I_FILE" -o "$F/cyc.ll"

  log "[2/4] opt 预清理"
  "$OPT_BIN" -passes="function(mem2reg,instcombine,simplifycfg)" "$F/cyc.ll" -S -o "$F/cyc.pre.ll"

  log "[3/4] opt + 插件（$OPT_PASSES_KERNEL）"
  # 注意：插件只注册了 ParsingCallback，-fpass-plugin 不会把 pass 加入管线，
  #       必须走 opt -passes=。这是与「直接 clang -fpass-plugin」的关键区别。
  PLC_FUSION_ROOTS="$ROOTS" \
  PLC_FUSION_HOT_PATH_FUNCTIONS="$HOT_FUNCS" \
  "$OPT_BIN" -load-pass-plugin="$FUSION_SO" -load-pass-plugin="$LOW_JITTER_SO" \
      -passes="$OPT_PASSES_KERNEL" "$F/cyc.pre.ll" -S -o "$F/cyc.kll"

  local nf_pre nf_kll nplc
  nf_pre=$(grep -cE '^define ' "$F/cyc.pre.ll")
  nf_kll=$(grep -cE '^define ' "$F/cyc.kll")
  log "IR: $(stat -c%s "$F/cyc.pre.ll") → $(stat -c%s "$F/cyc.kll") 字节；函数 $nf_pre → $nf_kll"
  grep -E '^define ' "$F/cyc.kll" | sed -E 's/^define.*@([^ (]+).*/\1/' | tr '\n' ' ' | sed 's/^/  保留函数: /'; echo

  # 判据不是「删了多少函数」——roots 设为入口时，可达函数本就该全部保留。
  # 真正的判据是 pass 有没有动手：IR 必须变化，且出现 plc_* 重映射。
  if cmp -s "$F/cyc.pre.ll" "$F/cyc.kll"; then
    die "IR 与输入完全一致——pass 未执行。检查 -passes= 名字是否正确（应为 plc-fusion），
  以及是否误用了 -fpass-plugin（插件只注册 ParsingCallback，不会自动加入管线）"
  fi
  nplc=$(grep -cE '@plc_[a-z_]+\(' "$F/cyc.kll" || true)
  [ "$nplc" -gt 0 ] || die "IR 里没有 plc_* 调用——重映射未生效，检查 symmap 配置"
  ok "pass 已执行（plc_* 调用点 $nplc 处，函数 $nf_pre → $nf_kll）"

  log "[4/4] llc → 目标文件（fused + base 对照）"
  "$LLC_BIN" -O3 -relocation-model=static -march=aarch64 -mattr="$LLC_MATTR" \
      -filetype=obj "$F/cyc.pre.ll" -o "$O_BASE"
  "$LLC_BIN" -O3 -relocation-model=static -march=aarch64 -mattr="$LLC_MATTR" \
      -filetype=obj "$F/cyc.kll" -o "$O_FUSED"

  ok "base.o  = $(stat -c%s "$O_BASE") 字节（同 IR，未过 pass）"
  ok "fused.o = $(stat -c%s "$O_FUSED") 字节（过 pass）"
  echo
  echo "=== fused.o 符号（应全是 plc_* + 少量 libc）==="
  nm -u "$O_FUSED" | awk '{print $NF}' | sort -u | tr '\n' ' '; echo
  echo "  新增 vs base: $(comm -13 <(nm -u "$O_BASE" | awk '{print $NF}' | sort -u) <(nm -u "$O_FUSED" | awk '{print $NF}' | sort -u) | tr '\n' ' ')"
  echo
  echo "下一步：把 fused.o 拷回 NuttX 卡，执行： $0 stub"
}

# ---------------- ③ stub（NuttX 卡） ----------------
stage_stub() {
  local direct_time=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --direct-time) direct_time=1; shift ;;
      *) die "stub 未知参数: $1" ;;
    esac
  done

  log "阶段 ③：编 NuttX 桩并合并"
  export PATH="$TOOLCHAIN:$PATH"
  [ -f "$O_FUSED" ] || die "找不到 $O_FUSED —— 先把 fuse 阶段产物拷回来"
  [ -f "$STUB_C" ]  || die "找不到桩文件 $STUB_C"

  # --direct-time：把两个时间调用直接别名到 NuttX 原函数，去掉 ABI 中间层。
  #
  # 实测（2026-10-20，Pi4B）：带中间层 5.958 µs / 直接别名 4.926 µs，差 1.032 µs/周期。
  # testthread 每周期 4 次时间调用 → 单次包装 ≈258 ns（一次调用 + timespec 结构体拷贝）。
  #
  # 注意：这不是「在桩里写 static inline」能做到的 —— fused.o 已固定生成对
  # plc_ktime_get_ts 的外部调用，内联需要改调用方。等价做法有两种：
  #   (a) 链接期符号别名（这里采用，产物与 (b) 完全相同）
  #   (b) 让 pass 直接生成 clock_gettime 调用（需改 pass 并在 LLVM 卡重编）
  local stub_defs="" fuse_obj="$O_FUSED"
  if [ "$direct_time" = "1" ]; then
    log "--direct-time：plc_ktime_get_ts→clock_gettime, plc_clock_nanosleep→clock_nanosleep"
    fuse_obj="$WORK/fused.direct.o"
    cp "$O_FUSED" "$fuse_obj"
    aarch64-none-elf-objcopy \
      --redefine-sym plc_ktime_get_ts=clock_gettime \
      --redefine-sym plc_clock_nanosleep=clock_nanosleep "$fuse_obj"
    stub_defs="-DKF_NO_TIME_STUBS"
  fi

  # shellcheck disable=SC2086
  aarch64-none-elf-gcc -c "$STUB_C" -o "$O_STUB" $stub_defs \
    -I"$NUTTX_BUILD/nuttx/include" -I"$NUTTX_BUILD/apps/include" \
    -D__NuttX__ -mcpu="$CPU" -Os -fno-builtin -ffreestanding
  ok "stubs.o = $(stat -c%s "$O_STUB") 字节${stub_defs:+（不含时间桩）}"

  # NuttX 的 Application.mk 编译 MAINSRC 时会传 -Dmain=$(PROGNAME)_main，
  # 所以编出来的 .o 必须导出 cyclictest_main，builtin_list 才找得到。
  # emit 阶段若已带 -Dmain=cyclictest_main，这里判定为无需改名。
  local obj_in="$fuse_obj" fout fm fcm
  fout=$(aarch64-none-elf-nm "$fuse_obj" 2>/dev/null || true)
  fm=$(printf '%s\n' "$fout" | grep -cE ' T main$' || true)
  fcm=$(printf '%s\n' "$fout" | grep -cE " T $ENTRY_SYM\$" || true)
  if [ "${fm:-0}" -ge 1 ] && [ "${fcm:-0}" -eq 0 ]; then
    obj_in="$WORK/fused.renamed.o"
    aarch64-none-elf-objcopy --redefine-sym "main=$ENTRY_SYM" "$fuse_obj" "$obj_in"
    ok "入口改名: main → $ENTRY_SYM（NuttX builtin 要求）"
  fi

  aarch64-none-elf-ld -r "$obj_in" "$O_STUB" -o "$O_COMBINED"
  ok "合并: cyclictest_combined.o = $(stat -c%s "$O_COMBINED") 字节"

  # 同上：不要用 `nm | grep -q`（pipefail + SIGPIPE 会误判）
  local cmbout chit
  cmbout=$(aarch64-none-elf-nm "$O_COMBINED" 2>/dev/null || true)
  chit=$(printf '%s\n' "$cmbout" | grep -cE " T $ENTRY_SYM\$" || true)
  [ "${chit:-0}" -ge 1 ] || die "合并后的 .o 里没有 $ENTRY_SYM —— builtin_list 会链接失败"

  echo "=== 合并后符号校验（必须恰好 27 个 POSIX）==="
  check_symbols "$O_COMBINED" aarch64-none-elf-nm || die "合并后符号不合格：桩没覆盖全，或引入了新依赖"
  ok "通过 —— 可以装进 NuttX"
}

# ---------------- ④ install ----------------
stage_install() {
  local dry=0
  [ "${1:-}" = "--dry-run" ] && dry=1
  log "阶段 ④：装回 NuttX 并重链"
  [ -d "$APP_DIR" ] || die "找不到 $APP_DIR"
  [ -f "$O_COMBINED" ] || die "找不到 $O_COMBINED —— 先跑 stub"
  export PATH="$TOOLCHAIN:$PATH"

  echo "=== 装前校验 ==="
  check_symbols "$O_COMBINED" aarch64-none-elf-nm || die "产物符号不合格，拒绝安装"

  local objs; objs=$(ls "$APP_DIR"/*.o 2>/dev/null | grep -v "\.orig_make$" || true)
  [ -n "$objs" ] || die "$APP_DIR 下没有 .o —— 先完整 build 一次"

  if [ "$dry" -eq 1 ]; then echo "[dry-run] 将覆盖:"; echo "$objs"; return 0; fi

  local f
  for f in $objs; do [ -f "$f.orig_make" ] || cp -p "$f" "$f.orig_make"; done
  ok "原件已备份为 *.orig_make"

  for f in $objs; do cp "$O_COMBINED" "$f"; touch "$f"; done
  ok "已替换 $(echo "$objs" | wc -l) 个目标文件"

  log "重链（make -j$(nproc)）"
  ( cd "$NUTTX_BUILD/nuttx" && make -j"$(nproc)" ) 2>&1 | tail -6

  # builtin app 的入口经 -Dmain=$(PROGNAME)_main 改名，NuttX 里应出现 cyclictest_main
  #
  # ⚠ 这里不能用 `nm | grep -q`：本脚本开了 set -o pipefail，
  #   grep -q 命中即退出 → nm 收到 SIGPIPE(141) → 整条管道判失败 → 误报"app 丢了"。
  #   改用 grep -c（读完全部输入，不会提前关闭管道）。
  local nmout nhit
  nmout=$(aarch64-none-elf-nm "$NUTTX_BUILD/nuttx/nuttx" 2>/dev/null || true)
  nhit=$(printf '%s\n' "$nmout" | grep -cE " T ($ENTRY_SYM|main)\$" || true)
  [ "${nhit:-0}" -ge 1 ] || die "ELF 里没有 $ENTRY_SYM —— 链接把 app 丢了"
  ok "$ENTRY_SYM 已在 ELF 中"
  ls -l "$NUTTX_BUILD/nuttx/nuttx.bin"
  echo "nuttx.bin sha256: $(sha256sum "$NUTTX_BUILD/nuttx/nuttx.bin" | awk '{print $1}')"
  echo
  echo "下一步：create/load/start cell，经 COM11 跑与论文完全相同的命令："
  echo "  cyclictest -n 0 -m 0 -q -p 200 -h 200 -l 100000 -i 1000"
}

# ---------------- restore ----------------
stage_restore() {
  log "还原原始目标文件并重链"
  export PATH="$TOOLCHAIN:$PATH"
  local n=0 f
  for f in "$APP_DIR"/*.o; do
    if [ -f "$f.orig_make" ]; then cp -p "$f.orig_make" "$f"; n=$((n+1)); fi
  done
  [ "$n" -gt 0 ] || { echo "没有 *.orig_make 备份，无需还原"; return 0; }
  ok "已还原 $n 个文件"
  touch "$SRC_C"
  ( cd "$NUTTX_BUILD/nuttx" && make -j"$(nproc)" ) 2>&1 | tail -4
  echo "nuttx.bin sha256: $(sha256sum "$NUTTX_BUILD/nuttx/nuttx.bin" | awk '{print $1}')"
  echo "（NuttX 构建会写入编译时间戳，hash 每次重建都不同；比对请用 size 而非 hash）"
  ls -l "$NUTTX_BUILD/nuttx/nuttx.bin" | awk '{print "  大小: " $5 " 字节（基线约 368640）"}'
}

# ---------------- 入口 ----------------
cmd="${1:-}"; shift || true
case "$cmd" in
  emit)    stage_emit ;;
  fuse)    stage_fuse "$@" ;;
  stub)    stage_stub "$@" ;;
  install) stage_install "$@" ;;
  restore) stage_restore ;;
  verify)  [ -n "${1:-}" ] || die "用法: $0 verify <.o>"; check_symbols "$1" nm && ok "符号校验通过" ;;
  *)
    sed -n '3,40p' "$0" | sed 's/^# \{0,1\}//'
    exit 1 ;;
esac
