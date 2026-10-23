#!/bin/bash
# ecos_demo__效果摘要端到端.sh — IR → 效果摘要 → 契约 → 门禁（一条命令跑完）
#
# 它验证的是 ECoS 里最关键的一步：
#   **效果不是人声明的，是编译器从 IR 推出来的**，而门禁据此拒绝非法部署。
#
# 用法（Pi4 上）：
#   bash kfusion/scripts/ecos_demo__效果摘要端到端.sh
#
# 环境变量（一般不用改）：
#   CLANG=clang-16  OPT=opt-16  PYTHON=python3
#   PLCEffectSummary.so 默认取 kfusion/analysis/build/PLCEffectSummary.so
set -u

KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLANG="${CLANG:-clang-16}"
OPT="${OPT:-opt-16}"
PY="${PYTHON:-python3}"
SO="${PLCEFFECTSUMMARY_SO:-$KF/analysis/build/PLCEffectSummary.so}"
OUT="${OUT_DIR:-$KF/analysis/build/ecos_demo}"

fail=0
step() { echo; echo "──── $* ────"; }

mkdir -p "$OUT"

step "0) 前置检查"
for f in "$SO"; do
    [ -f "$f" ] || { echo "❌ 缺 $f（先编译分析插件）"; exit 2; }
done
command -v "$CLANG" >/dev/null || { echo "❌ 缺 $CLANG"; exit 2; }
command -v "$OPT"   >/dev/null || { echo "❌ 缺 $OPT"; exit 2; }
echo "ok: $CLANG / $OPT / $SO"

step "1) C → IR"
# 必须禁用内联：实测 cyclictest 在 -O2 下只剩 3 个 define（全被内联），
# 效果摘要会退化成"每模块几个巨型函数"，阶段级契约就无从谈起。
# 禁内联后同一份代码是 13 个函数。
"$CLANG" -S -emit-llvm -O2 -fno-inline -fno-inline-functions -fno-builtin \
    "$KF/examples/ecos/plc_chain__三阶段示例.c" -o "$OUT/plc_chain.ll" || exit 3
echo "写出 $OUT/plc_chain.ll"

step "2) IR → 效果摘要（编译器推导，不是人写的）"
PLC_EFFECT_SUMMARY_OUT="$OUT/effect_summary.json" \
    "$OPT" -load-pass-plugin "$SO" -passes=plc-effect-summary \
    "$OUT/plc_chain.ll" -S -o /dev/null || exit 4
"$PY" - "$OUT/effect_summary.json" <<'PYEOF'
import io, json, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8"))
for name in ("sensor_prepare", "fir_filter", "pid_step", "chain_main"):
    info = d["functions"].get(name)
    if info is None:
        print(f"  {name:18s} (缺失)")
        continue
    print(f"  {name:18s} {','.join(info['effects']):34s} ctx={','.join(info['allowed_contexts'])}")
PYEOF

step "3) 摘要 + 拓扑 → 契约（人手写的 stages.json 里没有 effects 字段）"
"$PY" "$KF/scripts/effect_to_contract__摘要转契约.py" \
    --summary "$OUT/effect_summary.json" \
    --skeleton "$KF/contracts/examples/plc_chain.stages.json" \
    --out "$OUT/plc_chain.contract.json" || exit 5

step "4) 门禁：linux_kernel 域（预期 REJECT：sensor_prepare 要 MMIO）"
"$PY" "$KF/scripts/contract_check__契约校验.py" "$OUT/plc_chain.contract.json" \
    --platforms "$KF/contracts/examples/platform_caps.json" --platform linux_kernel
rc_lk=$?
echo "退出码: $rc_lk（非 0 = 正确拒绝）"
[ $rc_lk -ne 0 ] || { echo "❌ linux_kernel 竟然通过了——allow_mmio=false 没生效"; fail=1; }

step "5) 门禁：nuttx 域（预期 PASS：allow_mmio=true）"
"$PY" "$KF/scripts/contract_check__契约校验.py" "$OUT/plc_chain.contract.json" \
    --platforms "$KF/contracts/examples/platform_caps.json" --platform nuttx
rc_nx=$?
echo "退出码: $rc_nx"
[ $rc_nx -eq 0 ] || { echo "❌ nuttx 域被拒了——检查 nuttx 的 allow_mmio"; fail=1; }

step "结论"
if [ $fail -eq 0 ]; then
    echo "✅ 端到端成立："
    echo "   ① effects 由 LLVM 从 IR 推出（sensor_prepare 命中 mmio）"
    echo "   ② 同一份契约在 linux_kernel 被拒、在 nuttx 通过"
    echo "   ③ 这就是「sensor_prepare 必须留在 NuttX 侧」的自动依据"
    echo
    echo "产物都在 $OUT/"
else
    echo "❌ 有步骤不符合预期"
fi
exit $fail
