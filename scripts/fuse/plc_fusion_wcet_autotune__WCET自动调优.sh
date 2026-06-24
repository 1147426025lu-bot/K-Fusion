#!/bin/bash
# ============================================================================
# plc_fusion_wcet_autotune__WCET自动调优.sh — sweep + 短 insmod WCET 自动调优原型
# ============================================================================
# 功能:
#   1. 运行/复用 tail sweep（静态 IR/.o 对照）
#   2. 对每个 variant 短 insmod 采样 abs_max_ns（可跳过）
#   3. 选出 WCET 最优 variant，写入 .autotune.env / .wcet_autotune.json
#   4. 可选将 winner .o 提升为 _kernel.o_shipped
# 用法:
#   bash scripts/plc_fusion_wcet_autotune__WCET自动调优.sh \
#     manifests/manifest_cyclictest__主线压测.env
# 环境:
#   WCET_AUTOTUNE_SKIP_INSMOD=1   仅静态 sweep（无 sudo）
#   WCET_PROBE_SEC=30             每 variant 采样秒数（默认 30）
#   WCET_AUTOTUNE_VARIANTS=       只测指定 variant 名（逗号分隔）
#   WCET_AUTOTUNE_APPLY=1         将 winner 复制为 _kernel.o_shipped（默认 1）
#   WCET_AUTOTUNE_GREEDY=1        在默认 6 组外再试 4 组变异 tail
#   WCET_AUTOTUNE_GENETIC=1       改用遗传搜索（RTSS 2025 风格，见 plc_fusion_wcet_genetic）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-manifests/manifest_cyclictest__主线压测.env}}"
SWEEP="$SCRIPT_DIR/plc_fusion_wcet_sweep__tail对照.sh"
PROBE="$SCRIPT_DIR/plc_fusion_wcet_probe__短测探针.sh"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST" "FUSE_NAME" ""

WORK="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}"
SWEEP_JSON="$WORK/${FUSE_NAME}.wcet_sweep.json"
SWEEP_DIR="$WORK/.${FUSE_NAME}_wcet_sweep"
OUT_JSON="$WORK/${FUSE_NAME}.wcet_autotune.json"
OUT_ENV="$WORK/${FUSE_NAME}.autotune.env"
PROBE_SEC="${WCET_PROBE_SEC:-30}"
SKIP_INSMOD="${WCET_AUTOTUNE_SKIP_INSMOD:-0}"
APPLY="${WCET_AUTOTUNE_APPLY:-1}"
FILTER="${WCET_AUTOTUNE_VARIANTS:-}"

echo "=== WCET autotune: ${FUSE_NAME} ==="
echo "    manifest=$MANIFEST"
echo "    probe_sec=$PROBE_SEC insmod=$([ "$SKIP_INSMOD" = 1 ] && echo skip || echo on)"

if [ "${WCET_AUTOTUNE_GENETIC:-0}" = "1" ]; then
    GENETIC="$SCRIPT_DIR/plc_fusion_wcet_genetic__遗传WCET调优.sh"
    WCET_GENETIC_SKIP_INSMOD="$SKIP_INSMOD" \
    WCET_PROBE_SEC="$PROBE_SEC" \
    WCET_GENETIC_APPLY="$APPLY" \
    WCET_GENETIC_POP="${WCET_GENETIC_POP:-8}" \
    WCET_GENETIC_GEN="${WCET_GENETIC_GEN:-4}" \
    bash "$GENETIC" "$MANIFEST"
    # 统一输出文件名供下游使用
    GEN_JSON="$WORK/${FUSE_NAME}.wcet_genetic.json"
    GEN_ENV="$WORK/${FUSE_NAME}.genetic.env"
    if [ -f "$GEN_JSON" ]; then
        cp -f "$GEN_JSON" "$OUT_JSON"
    fi
    if [ -f "$GEN_ENV" ]; then
        cp -f "$GEN_ENV" "$OUT_ENV"
    fi
    echo "✅ WCET autotune (genetic) 完成"
    exit 0
fi

WCET_SWEEP_RUN_FUSE="${WCET_SWEEP_RUN_FUSE:-0}" bash "$SWEEP" "$MANIFEST"
plc_require_file "$SWEEP_JSON" "sweep json"

# 可选 greedy：在 sweep 后再生成 4 个变异 variant（仅 opt+llc，不重复 fuse）
if [ "${WCET_AUTOTUNE_GREEDY:-0}" = "1" ]; then
    echo "🧬 greedy 变异 tail（4 组）..."
    PRE="$WORK/${FUSE_NAME}_pre.ll"
    BUILD_DIR="$PROJECT_ROOT/build"
    FUSION_SO="$BUILD_DIR/PLCFusionPass.so"
    OPT_BIN="$(plc_resolve_tool OPT_BIN opt-19 opt-18 opt-17 opt)"
    LLC_BIN="$(plc_resolve_tool LLC_BIN llc-19 llc-18 llc-17 llc)"
    export PLC_FUSION_DCE="${FUSE_DCE:-1}"
    export PLC_FUSION_FLOAT_KILL=1
    export PLC_FUSION_BLACKHOLE=1
    export PLC_FUSION_KEEP_GLOBALS="${FUSE_GLOBALIZE_SYMBOLS:-}"
    export PLC_FUSION_ROOTS="${FUSE_DCE_ROOTS:-${FUSE_KTHREAD_ENTRY:-}}"
    export PLC_FUSION_HOT_PATH_FUNCTIONS="${FUSE_HOT_PATH_FUNCTIONS:-${FUSE_KTHREAD_ENTRY:-}}"
    export PLC_FUSION_WCET_HOT_FUNCTIONS="${PLC_FUSION_HOT_PATH_FUNCTIONS:-}"
    mapfile -t greedy_specs < <(python3 "$SCRIPT_DIR/plc_fusion_wcet_passes__Pass序列库.py" --greedy-specs)
    for spec in "${greedy_specs[@]}"; do
        name="${spec%%|*}"
        rest="${spec#*|}"
        kernel="${rest%%|*}"
        tail="${rest#*|}"
        opt_passes="$kernel"
        [ -n "$tail" ] && opt_passes="${opt_passes},${tail}"
        kll="$SWEEP_DIR/${name}.ll"
        obj="$SWEEP_DIR/${name}.o"
        echo "    ▶ greedy $name"
        "$OPT_BIN" -load-pass-plugin="$FUSION_SO" -passes="$opt_passes" "$PRE" -S -o "$kll" 2>/dev/null || continue
        "$LLC_BIN" -O3 -relocation-model=pic \
            -march="${FUSE_LLC_ARCH:-aarch64}" -mattr="${FUSE_LLC_ATTR:--fp-armv8,-neon}" \
            -filetype=obj "$kll" -o "$obj" 2>/dev/null || continue
    done
fi

python3 - "$SWEEP_JSON" "$SWEEP_DIR" "$OUT_JSON" "$OUT_ENV" "$MANIFEST" "$FUSE_NAME" \
    "$SKIP_INSMOD" "$PROBE_SEC" "$PROBE" "$MANIFEST" "$FILTER" "$APPLY" "$WORK" << 'PY'
import json, os, subprocess, sys
from datetime import datetime, timezone

(sweep_json, sweep_dir, out_json, out_env, manifest, fuse_name,
 skip_insmod, probe_sec, probe_sh, manifest2, filter_names, apply, work) = sys.argv[1:]

skip_insmod = skip_insmod == "1"
apply = apply == "1"
filter_set = {x.strip() for x in filter_names.split(",") if x.strip()}

with open(sweep_json, encoding="utf-8") as f:
    sweep = json.load(f)

variants = []
for v in sweep.get("variants", []):
    if v.get("timerthread_ok") != 1:
        continue
    if filter_set and v["name"] not in filter_set:
        continue
    obj_path = os.path.join(sweep_dir, f"{v['name']}.o")
    if not os.path.isfile(obj_path):
        continue
    variants.append({**v, "obj_path": obj_path})

# greedy extras on disk
if os.path.isdir(sweep_dir):
    for fn in sorted(os.listdir(sweep_dir)):
        if not fn.endswith(".o"):
            continue
        name = fn[:-2]
        if not name.startswith("greedy+"):
            continue
        if filter_set and name not in filter_set:
            continue
        if any(x["name"] == name for x in variants):
            continue
        variants.append({
            "name": name,
            "kernel": "greedy",
            "tail": "",
            "obj_path": os.path.join(sweep_dir, fn),
            "ir_lines": 0,
            "obj_bytes": os.path.getsize(os.path.join(sweep_dir, fn)),
            "hot_entries": 1,
            "timerthread_ok": 1,
        })

results = []
best = None
best_key = None

if skip_insmod:
    for v in variants:
        rec = dict(v)
        rec["abs_max_ns"] = v["obj_bytes"]
        rec["metric"] = "obj_bytes_proxy_no_insmod"
        rec["probe_cycles"] = None
        rec["probe_error"] = None
        results.append(rec)
    no_tail = [r for r in results if not r.get("tail")]
    pool = no_tail or results
    best = min(pool, key=lambda x: x["abs_max_ns"])
    best_key = best["abs_max_ns"]
else:
    for v in variants:
        rec = dict(v)
        rec["abs_max_ns"] = None
        rec["probe_cycles"] = None
        rec["probe_error"] = None

        stats = os.path.join(work, f".wcet_probe_{v['name']}.stats")
        env = {**os.environ, "WCET_PROBE_STATS": stats, "WCET_PROBE_TAG": v["name"]}
        try:
            out = subprocess.run(
                ["bash", probe_sh, manifest2, v["obj_path"], probe_sec],
                capture_output=True, text=True, timeout=int(probe_sec) + 120, env=env,
            )
            for line in out.stdout.splitlines():
                if line.startswith("abs_max_ns="):
                    rec["abs_max_ns"] = int(line.split("=", 1)[1])
                if line.startswith("WCET_PROBE:") and "cycles=" in line:
                    part = [p for p in line.split() if p.startswith("cycles=")]
                    if part:
                        rec["probe_cycles"] = int(part[0].split("=", 1)[1])
            if rec["abs_max_ns"] is None:
                rec["probe_error"] = (out.stderr or out.stdout)[-500:]
        except Exception as e:
            rec["probe_error"] = str(e)
        rec["metric"] = "abs_max_ns"

        results.append(rec)
        if rec["abs_max_ns"] is None:
            continue
        key = rec["abs_max_ns"]
        if best is None or key < best_key:
            best = rec
            best_key = key

doc = {
    "fuse_name": fuse_name,
    "manifest": manifest,
    "generated": datetime.now(timezone.utc).astimezone().isoformat(),
    "probe_sec": int(probe_sec),
    "skip_insmod": skip_insmod,
    "metric": results[0]["metric"] if results else "none",
    "winner": best,
    "results": results,
}

with open(out_json, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2, ensure_ascii=False)
    f.write("\n")

if best:
    kernel = best.get("kernel", "")
    tail = best.get("tail", "")

    def _parse_tail(t):
        cold, mod = [], []
        if not t:
            return cold, mod
        buf = t
        fn_parts = []
        while "function(" in buf:
            i = buf.index("function(")
            j = buf.index(")", i)
            inner = buf[i + len("function(") : j]
            k = 0
            while k < len(inner):
                if inner.startswith("loop-mssa(", k):
                    end = inner.index(")", k)
                    fn_parts.append(inner[k : end + 1])
                    k = end + 1
                    if k < len(inner) and inner[k] == ",":
                        k += 1
                else:
                    end = inner.find(",", k)
                    if end == -1:
                        end = len(inner)
                    tok = inner[k:end].strip()
                    if tok:
                        fn_parts.append(tok)
                    k = end + 1 if end < len(inner) else len(inner)
            buf = buf[:i] + buf[j + 1 :]
        cold = fn_parts
        for chunk in buf.split(","):
            chunk = chunk.strip()
            if chunk:
                mod.append(chunk)
        return cold, mod

    cold_atoms, mod = _parse_tail(tail)
    cold_env = "|".join(cold_atoms)
    lines = [
        f"# PLCFusion autotune winner — generated {doc['generated']}",
        f"# metric={doc['metric']} value={best_key}",
        f"# variant={best['name']}",
        f"FUSE_COLD_PASS_SEQUENCE={cold_env}",
        f"FUSE_MODULE_PASS_SEQUENCE={','.join(mod)}",
    ]
    if kernel == "plc-kernelize-hotpath" and not tail:
        lines = [
            f"# PLCFusion autotune winner — hotpath (no tail)",
            f"FUSE_PIPELINE=hotpath",
            f"FUSE_WCET_MODE=1",
        ]
    elif kernel == "plc-kernelize-wcet" or kernel.startswith("paper"):
        lines += ["FUSE_PIPELINE=wcet", "FUSE_WCET_MODE=1"]
    elif kernel == "plc-kernelize-mainline" and not tail:
        lines = [
            f"# PLCFusion autotune winner — mainline (no tail)",
            f"FUSE_PIPELINE=mainline",
        ]
    elif kernel not in ("greedy", ""):
        lines += [
            "FUSE_PIPELINE=custom",
            f"FUSE_KERNEL_PASS={kernel}",
        ]
    with open(out_env, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    if apply and not skip_insmod:
        import shutil
        shipped = os.path.join(work, f"{fuse_name}_kernel.o")
        shipped_s = shipped + "_shipped"
        shutil.copy2(best["obj_path"], shipped)
        shutil.copy2(best["obj_path"], shipped_s)
        doc["applied_kernel_o"] = shipped
        doc["applied_kernel_o_shipped"] = shipped_s
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")

print(f"    autotune.json={out_json}")
if best:
    print(f"    winner={best['name']} {doc['metric']}={best_key}")
    print(f"    autotune.env={out_env}")
else:
    print("    winner=none (all probes failed — see results[].probe_error)")
    sys.exit(1)
PY

echo "✅ WCET autotune 完成"
