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

exp_pos, exp_neg, exp_rd, exp_wr, exp_wr_ge = [], [], [], [], []
for ln in ll.splitlines():
    m = re.match(r"^\s*;\s*EXPECT:\s*(\w+)\s+(.+?)\s*$", ln)
    if m:
        exp_pos.append((m.group(1), [e.strip() for e in m.group(2).split(",")]))
        continue
    m = re.match(r"^\s*;\s*EXPECT_NOT:\s*(\w+)\s+(.+?)\s*$", ln)
    if m:
        exp_neg.append((m.group(1), [e.strip() for e in m.group(2).split(",")]))
        continue
    m = re.match(r"^\s*;\s*EXPECT_READS:\s*(\w+)\s+(\S+)\s*$", ln)
    if m:
        exp_rd.append((m.group(1), m.group(2)))
        continue
    m = re.match(r"^\s*;\s*EXPECT_WRITES:\s*(\w+)\s+(\S+)\s*$", ln)
    if m:
        exp_wr.append((m.group(1), m.group(2)))
        continue
    m = re.match(r"^\s*;\s*EXPECT_WRITES_GE:\s*(\w+)\s+(\d+)\s*$", ln)
    if m:
        exp_wr_ge.append((m.group(1), int(m.group(2))))

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

for fn, region in exp_rd:
    info = funcs.get(fn)
    if info is None:
        print(f"❌ {fn}: 摘要里没有这个函数")
        fail += 1
        continue
    got = set(info.get("reads") or [])
    checked += 1
    if region not in got:
        print(f"❌ {fn}: reads 应含 {region}，实际 {sorted(got) or '[]'}")
        fail += 1
    else:
        print(f"✅ {fn}: reads 含 {region}")

for fn, region in exp_wr:
    info = funcs.get(fn)
    if info is None:
        print(f"❌ {fn}: 摘要里没有这个函数")
        fail += 1
        continue
    got = set(info.get("writes") or [])
    checked += 1
    if region not in got:
        print(f"❌ {fn}: writes 应含 {region}，实际 {sorted(got) or '[]'}")
        fail += 1
    else:
        print(f"✅ {fn}: writes 含 {region}")

for fn, at_least in exp_wr_ge:
    info = funcs.get(fn)
    if info is None:
        print(f"❌ {fn}: 摘要里没有这个函数")
        fail += 1
        continue
    got = set(info.get("writes") or [])
    checked += 1
    if len(got) < at_least:
        print(f"❌ {fn}: writes 应有 ≥{at_least} 个不同区域，实际 {sorted(got)}")
        fail += 1
    else:
        print(f"✅ {fn}: writes 有 {len(got)} 个不同区域 {sorted(got)}")

# 别名一致性：split_pairs 必须为 0（命名太细会漏真依赖）
astat = summary.get("alias_stats") or {}
if astat:
    checked += 1
    split = astat.get("split_pairs", 0)
    coarse = astat.get("coarse_pairs", 0)
    pairs = astat.get("pairs_checked", 0)
    avail = astat.get("aa_available")
    if not avail:
        print("❌ alias_stats: aa_available=false（AA 没拿到，统计无效）")
        fail += 1
    elif split:
        print(f"❌ alias_stats: split_pairs={split} > 0 —— 命名太细，会漏真依赖")
        fail += 1
    else:
        print(f"✅ alias_stats: 检查 {pairs} 对，coarse={coarse}（安全侧），split=0")

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
