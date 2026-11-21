#!/usr/bin/env bash
# build_hand_host__C组共用宿主.sh — 用「同一宿主」编出 C 组（手写读写实现）
#
# 背景（评审第 3 条）：C 组此前是独立手写模块，tick/槽表/计时点都与 D 组不同，
# 于是 C vs D 的尾部差异无法归因。现在两组共用同一份宿主，只有读写实现这一个
# 编译单元不同：
#   D 组 = let_impl.c（契约生成）      → 用 kf_unify（KF_LET_STUB_SEAM=1）直接编
#   C 组 = let_impl.c（本仓库手写版）  → 本脚本：先按 D 编一遍，再替换该文件重编
#
# 产物：
#   <OUT>/kmod_hrtimer/kf_task.ko          （C 组模块）
#   <OUT>/hand_impl_manifest.json          （两份实现的 sha256 + diff 行数，作为证据）
#
# 用法：
#   TIME_CONTRACT=... OUT_DIR=... bash build_hand_host__C组共用宿主.sh \
#        kfusion/examples/ecos/let_pc_shared__LET产消共享.c
set -euo pipefail

KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$KF/.." && pwd)"
SRC="${1:?usage: build_hand_host__C组共用宿主.sh <periodic.c>}"
HAND="$KF/src/kf_let_hand_impl__手写读写实现.c"
export OUT_DIR="${OUT_DIR:-$KF/../kfusion_results/hand_host_$(date +%Y%m%d_%H%M%S)}"
KDIR="${KERNELDIR:-/lib/modules/$(uname -r)/build}"
MODDIR="$OUT_DIR/kmod_hrtimer"

die() { echo "❌ $*"; exit "${2:-1}"; }

[ -f "$HAND" ] || die "缺手写实现：$HAND" 2
[ -d "$KDIR" ] || die "缺 KERNELDIR=$KDIR" 2
mkdir -p "$OUT_DIR"

echo "══ C 组：共用宿主 + 手写读写实现 ══"
echo "  src   $SRC"
echo "  out   $OUT_DIR"

# 1) 先按 D 组的同一套流水线产出宿主（seam 模式：let_run 单独一个编译单元）
echo "-- 1) 生成共用宿主（KF_LET_STUB_SEAM=1）"
KF_LET_STUB_SEAM=1 FORCE_CHOSEN="${FORCE_CHOSEN:-D@linux_kernel}" \
  bash "$KF/scripts/kf_unify__统一流水线.sh" "$SRC" \
  >"$OUT_DIR/build_seam.log" 2>&1 || {
    tail -20 "$OUT_DIR/build_seam.log" >&2
    die "共用宿主构建失败（详见 $OUT_DIR/build_seam.log）" 8
  }
[ -f "$MODDIR/let_impl.c" ] || die "seam 未产出 $MODDIR/let_impl.c（KF_LET_STUB_SEAM 没生效？）" 9
[ -f "$MODDIR/kf_let_host.h" ] || die "seam 未产出 $MODDIR/kf_let_host.h" 9

# 2) 保留生成版作对照，再把实现换成手写版
echo "-- 2) 替换读写实现（生成 → 手写）"
cp "$MODDIR/let_impl.c" "$OUT_DIR/let_impl_generated.c"
cp "$HAND" "$MODDIR/let_impl.c"

GEN_SHA="$(sha256sum "$OUT_DIR/let_impl_generated.c" | awk '{print $1}')"
HAND_SHA="$(sha256sum "$MODDIR/let_impl.c" | awk '{print $1}')"
DIFF_LINES="$(diff -u "$OUT_DIR/let_impl_generated.c" "$MODDIR/let_impl.c" | wc -l || true)"

# 3) 重编（只换了一个 .c，其余目标文件与 D 组同批）
echo "-- 3) 重编 .ko"
make -C "$KDIR" M="$MODDIR" modules >"$OUT_DIR/build_hand.log" 2>&1 || {
    grep -E "error:" "$OUT_DIR/build_hand.log" | head -10 >&2
    die "手写版编译失败（详见 $OUT_DIR/build_hand.log）" 9
  }

# 4) 记账：证明「只换了这一个文件」
KO_SHA="$(sha256sum "$MODDIR/kf_task.ko" | awk '{print $1}')"
cat >"$OUT_DIR/hand_impl_manifest.json" <<JSON
{
  "kind": "hand_impl_swap",
  "arm": "C",
  "source": "$(basename "$SRC")",
  "host": "ecos_fused_linux.c (与 D 组同一份)",
  "impl_file": "let_impl.c",
  "generated_impl_sha256": "$GEN_SHA",
  "hand_impl_sha256": "$HAND_SHA",
  "diff_lines": $DIFF_LINES,
  "ko_sha256": "$KO_SHA",
  "note": "宿主/事件表/计时点/缓冲区/门禁全同；唯一变量 = let_impl.c 是生成还是手写"
}
JSON

echo
echo "  generated impl sha256  $GEN_SHA"
echo "  hand      impl sha256  $HAND_SHA"
echo "  diff 行数             $DIFF_LINES"
echo "  kf_task.ko sha256      $KO_SHA"
echo "  manifest               $OUT_DIR/hand_impl_manifest.json"
echo "✅ C 组模块：$MODDIR/kf_task.ko"
