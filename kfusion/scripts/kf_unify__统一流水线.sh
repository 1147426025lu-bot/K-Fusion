#!/usr/bin/env bash
# kf_unify__统一流水线.sh — 统一架构主入口（U-1～U-3）
#
# 一条链：C → AST/TEIR/STKP → plan（含 ownership/handoffs）
#        → PLCFusionPass → stage_impl.S/.o → 专用宿主 → .ko
#
# 验收：产出目录里必须有 kernel_stage.manifest.json，且 final_ko.sha256 非空；
#       不得出现可供 kbuild 编译的 stage_impl.c（那等于绕过 Pass）。
#
# 用法：
#   bash kfusion/scripts/kf_unify__统一流水线.sh \
#        kfusion/examples/ecos/four_stage_chain__四阶段示例.c
#
# 常用环境变量：
#   FORCE_CHOSEN=D@linux_kernel   强制全融合内核（A/B 用户态；C/D 内核；默认 STKP 自选）
#   HOST_MODEL=single_worker|per_group   内核宿主（默认 single_worker）
#   OUT_DIR=...                   产物目录
#   SKIP_HOST=1                   不跑用户态试跑
#   KERNEL_STATE_SYMS=g_raw,...   额外保留的状态符号
set -euo pipefail

KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${1:?usage: kf_unify__统一流水线.sh <periodic.c>}"

export CLANG="${CLANG:-clang-19}"
export OPT="${OPT:-opt-19}"
export LLC_BIN="${LLC_BIN:-llc-19}"
export KERNELDIR="${KERNELDIR:-/lib/modules/$(uname -r)/build}"
export KFUSION_PASS_SO="${KFUSION_PASS_SO:-$KF/build/KFusionPass.so}"
export PLATFORM="${PLATFORM:-linux_kernel}"
export SKIP_HOST="${SKIP_HOST:-1}"
export HOST_MODEL="${HOST_MODEL:-single_worker}"
export OUT_DIR="${OUT_DIR:-$KF/../kfusion_results/unify_$(date +%Y%m%d_%H%M%S)}"

die() { echo "❌ $*"; exit "${2:-1}"; }

[ -f "$KFUSION_PASS_SO" ] || die "缺 Pass：$KFUSION_PASS_SO" 2
[ -d "$KERNELDIR" ] || die "缺 KERNELDIR=$KERNELDIR（modules_prepare？）" 2
command -v "$CLANG" >/dev/null || die "缺 $CLANG" 2
command -v "$OPT" >/dev/null || die "缺 $OPT" 2
command -v "$LLC_BIN" >/dev/null || die "缺 $LLC_BIN" 2

mkdir -p "$OUT_DIR"
echo "══ K-Fusion 统一流水线 ══"
echo "  src       $SRC"
echo "  out       $OUT_DIR"
echo "  clang/opt $CLANG / $OPT / $LLC_BIN"
echo "  KERNELDIR $KERNELDIR"
echo "  Pass      $KFUSION_PASS_SO"
echo "  FORCE     ${FORCE_CHOSEN:-(STKP 自选)}"
echo "  HOST      $HOST_MODEL"

bash "$KF/scripts/ecos_from_c__从C编译.sh" "$SRC" || die "ecos_from_c 失败" 3

DOMAIN="$(python3 -c "import json; print((json.load(open('$OUT_DIR/stkp.json')).get('chosen_record') or {}).get('domain') or '')")"
echo
echo "── 统一验收（U-3）──"
echo "  domain=$DOMAIN"

if [ "$DOMAIN" = "linux_kernel" ]; then
  MAN="$OUT_DIR/kmod_hrtimer/kernel_stage.manifest.json"
  [ -f "$MAN" ] || die "缺 manifest：$MAN（Pass 链未跑）" 4
  if [ -f "$OUT_DIR/kmod_hrtimer/stage_impl.c" ]; then
    die "禁止存在 stage_impl.c（会让 kbuild 绕过 Pass 编译原 C）" 5
  fi
  [ -f "$OUT_DIR/kmod_hrtimer/stage_impl.S" ] || die "缺 stage_impl.S" 6
  KO="$(ls "$OUT_DIR/kmod_hrtimer"/*.ko 2>/dev/null | head -1 || true)"
  [ -n "$KO" ] || die "未产出 .ko（检查 kbuild / KERNELDIR）" 7
  python3 - "$MAN" "$KO" <<'PY' || die "manifest 与 .ko 对不上" 8
import json, hashlib, sys
man = json.load(open(sys.argv[1]))
ko = sys.argv[2]
h = hashlib.sha256(open(ko, "rb").read()).hexdigest()
fk = (man.get("final_ko") or {})
if not fk.get("sha256"):
    raise SystemExit("manifest 未回填 final_ko")
if fk["sha256"] != h:
    raise SystemExit("final_ko.sha256 与磁盘 .ko 不一致")
print("  ok Pass roots=%s" % ",".join(man.get("kernel_roots") or []))
print("  ok stage_obj sha=%s" % (man.get("sha256") or {}).get("stage_obj", "")[:16])
print("  ok .ko %s (%s…)" % (ko, h[:16]))
print("  ownership keys in plan: check plan.json")
PY
elif [ "$DOMAIN" = "mixed" ]; then
  MAN="$OUT_DIR/uku_stage/kernel_stage.manifest.json"
  [ -f "$MAN" ] || die "缺 UKU manifest：$MAN" 4
  if [ -f "$OUT_DIR/kmod_uku/stage_impl.c" ]; then
    die "禁止 UKU 目录存在 stage_impl.c" 5
  fi
  KO="$(ls "$OUT_DIR/kmod_uku"/*.ko 2>/dev/null | head -1 || true)"
  [ -n "$KO" ] || die "未产出 UKU .ko" 7
  echo "  ok UKU .ko $KO"
else
  echo "  skip .ko 验收（入选用户态；要用内核请 FORCE_CHOSEN=…@linux_kernel）"
fi

# plan 字段（U-6：认 verified_plan / provenance.verified，不再依赖 soft enrich）
python3 - "$OUT_DIR/plan.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
prov = p.get("provenance") or {}
en = (p.get("meta") or {}).get("enriched") or {}
verified = bool(prov.get("verified")) or bool((p.get("verification") or {}).get("ok"))
print("  plan ownership=%s handoffs=%s release_owner=%s verified=%s"
      % (len(p.get("state_ownership") or {}),
         len(p.get("handoffs") or {}),
         p.get("release_owner"),
         verified))
if not verified and not en:
    raise SystemExit("plan 未经 verified_plan 门禁（也无 soft enrich）")
if not p.get("state_ownership"):
    raise SystemExit("plan 缺 state_ownership")
PY

echo
echo "✅ 统一流水线完成 → $OUT_DIR"
