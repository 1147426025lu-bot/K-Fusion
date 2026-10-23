#!/bin/bash
# effect_summary_selftest__自测.sh — 核对 EffectSummaryAnalysis 的输出与 .ll 里的 EXPECT 注释
#
# 用法（在 Pi 或任何有 LLVM 16 的机器上）：
#   bash kfusion/analysis/test/effect_summary_selftest__自测.sh [opt 路径] [插件 .so 路径]
#
# 设计说明：断言写在 .ll 里（`; EXPECT: fn eff1,eff2` / `; EXPECT_NOT: fn eff`），
# 而不是写死在这个脚本里——用例和被断言对象放一起，改用例时不会忘记改断言。
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPT="${1:-/usr/bin/opt-16}"
SO="${2:-$(cd "$HERE/.." && pwd)/build/PLCEffectSummary.so}"
LL="$HERE/effect_summary_basic.ll"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ ! -f "$SO" ]; then
    echo "❌ 找不到插件 $SO"
    echo "   编译：clang++-16 -shared -fPIC -O2 -fno-rtti \$(llvm-config-16 --cxxflags) \\"
    echo "           -o $SO $HERE/../EffectSummaryAnalysis__副作用摘要.cpp"
    exit 2
fi

echo "=== 运行 plc-effect-summary ==="
PLC_EFFECT_SUMMARY_OUT="$TMP/out.json" "$OPT" -load-pass-plugin "$SO" \
    -passes=plc-effect-summary "$LL" -S -o /dev/null 2>"$TMP/err.txt"
RC=$?
if [ $RC -ne 0 ]; then
    echo "❌ opt 失败 (rc=$RC)"
    cat "$TMP/err.txt"
    exit 1
fi
[ -s "$TMP/out.json" ] || { echo "❌ 没有产出 JSON"; cat "$TMP/err.txt"; exit 1; }

echo "=== 核对 EXPECT / EXPECT_NOT ==="
PY="${PYTHON:-python3}"
"$PY" - "$TMP/out.json" "$LL" <<'PYEOF'
import io, json, re, sys

summary = json.load(io.open(sys.argv[1], encoding="utf-8"))
funcs = summary.get("functions", {})
ll = io.open(sys.argv[2], encoding="utf-8").read()

exp_pos, exp_neg = [], []
for ln in ll.splitlines():
    m = re.match(r"^\s*;\s*EXPECT:\s*(\w+)\s+(.+?)\s*$", ln)
    if m:
        exp_pos.append((m.group(1), [e.strip() for e in m.group(2).split(",")]))
    m = re.match(r"^\s*;\s*EXPECT_NOT:\s*(\w+)\s+(.+?)\s*$", ln)
    if m:
        exp_neg.append((m.group(1), [e.strip() for e in m.group(2).split(",")]))

fail = 0
checked = 0
for fn, want in exp_pos:
    info = funcs.get(fn)
    if info is None:
        print(f"❌ {fn}: 摘要里没有这个函数")
        fail += 1
        continue
    got = set(info.get("effects") or [])
    checked += 1
    missing = [e for e in want if e not in got]
    if missing:
        print(f"❌ {fn}: 期望 {want}，实际 {sorted(got)}（缺 {missing}）")
        fail += 1
    else:
        print(f"✅ {fn}: {sorted(got)}")

for fn, bad in exp_neg:
    info = funcs.get(fn)
    if info is None:
        print(f"❌ {fn}: 摘要里没有这个函数")
        fail += 1
        continue
    got = set(info.get("effects") or [])
    checked += 1
    hit = [e for e in bad if e in got]
    if hit:
        print(f"❌ {fn}: 不应含 {hit}，实际 {sorted(got)}")
        fail += 1
    else:
        print(f"✅ {fn}: 不含 {bad}（实际 {sorted(got)}）")

print()
print(f"检查 {checked} 条，失败 {fail} 条")
sys.exit(1 if fail else 0)
PYEOF
PYRC=$?

echo
if [ $PYRC -eq 0 ]; then
    echo "=== 摘要全文（供人工复核）==="
    cat "$TMP/out.json"
fi
exit $PYRC
