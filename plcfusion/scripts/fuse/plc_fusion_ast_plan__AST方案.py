#!/usr/bin/env python3
"""
plc_fusion_ast_plan__AST方案.py — 从 plc_ast JSON 生成 fusion_plan + shell export

读取 test/${FUSE_NAME}.plc_ast.json 或 .fusion_ast.json，
输出较优 Pass profile 建议与 manifest 填空提示。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def load_manifest_keys(path: Path | None) -> dict[str, str]:
    if not path or not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m:
            continue
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[m.group(1)] = val
    return out


def pick_ast_json(work_dir: Path, fuse_name: str, override: str | None) -> Path | None:
    if override:
        p = Path(override)
        return p if p.is_file() else None
    for name in (f"{fuse_name}.plc_ast.json", f"{fuse_name}.fusion_ast.json"):
        p = work_dir / name
        if p.is_file():
            return p
    return None


def join_roots(entry: str, call_graph: dict[str, Any]) -> str:
    callees = call_graph.get(entry) or []
    names = [entry]
    for c in callees:
        if isinstance(c, str) and c and c not in names:
            names.append(c)
    return ",".join(names)


RT_THREAD_ENTRIES = frozenset(
    {"timerthread", "fifothread", "signalthread", "semathread", "main"}
)


def entry_trustworthy(entry: str, entries: list[str]) -> bool:
    if entry.startswith("plc_"):
        return True
    if entry in RT_THREAD_ENTRIES:
        return True
    return any(e.startswith("plc_") for e in entries)


def build_candidates(
    profile: str | None,
    reason: str | None,
    data: dict[str, Any],
    manifest: dict[str, str],
) -> list[dict[str, Any]]:
    """最多 3 条 Pass profile 候选（rank 1 为首选）。"""
    wcet_mode = manifest.get("FUSE_WCET_MODE") == "1"
    float_in_cycle = bool(data.get("float_in_cycle"))
    blocking_errors = int(data.get("blocking_error_count", 0) or 0)
    if "blocking_error_count" not in data:
        cycle_issues = data.get("cycle_issues") or []
        blocking_errors = sum(
            1
            for i in cycle_issues
            if i.get("kind") == "blocking" and i.get("severity") == "error"
        )
    fusion_crit = int(data.get("fusion_critical_count") or 0)
    sched_crit = int(data.get("sched_critical_count") or 0)

    cands: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(rank: int, prof: str, why: str, conf: str = "heuristic") -> None:
        if prof in seen:
            return
        seen.add(prof)
        cands.append({"rank": rank, "profile": prof, "reason": why, "confidence": conf})

    if fusion_crit > 0 or sched_crit > 0:
        add(1, "minimal", f"fusion_crit={fusion_crit} sched_crit={sched_crit}", "high")
        add(2, "debug", "inspect_symbols", "medium")
        return cands[:3]

    if profile:
        add(1, profile, reason or "primary", "heuristic")

    if wcet_mode:
        if profile == "wcet" and (blocking_errors > 0 or float_in_cycle):
            add(2, "hotpath", "lighter_tail_under_load", "medium")
        elif profile != "wcet":
            add(2, "wcet", "FUSE_WCET_MODE=1", "medium")
        if profile not in ("hotpath", "minimal"):
            add(3, "hotpath", "fallback_light", "low")

    if not cands and wcet_mode:
        add(1, "wcet", "FUSE_WCET_MODE=1", "medium")
        add(2, "hotpath", "fallback_light", "low")

    if not cands:
        add(1, "generic", "default", "low")

    cands.sort(key=lambda c: c["rank"])
    for i, c in enumerate(cands[:3], start=1):
        c["rank"] = i
    return cands[:3]


def build_plan(data: dict[str, Any], manifest: dict[str, str]) -> dict[str, Any]:
    entry = (data.get("entry") or "").strip()
    entries = [e for e in (data.get("entries_found") or []) if isinstance(e, str)]
    suggest = data.get("manifest_suggestions") or {}
    call_graph = data.get("call_graph") or {}

    fusion_crit = int(data.get("fusion_critical_count") or 0)
    fusion_warn = int(data.get("fusion_warn_count") or 0)
    fusion_eligible = bool(data.get("fusion_eligible", True))
    float_in_cycle = bool(data.get("float_in_cycle"))
    float_anywhere = bool(data.get("float_anywhere"))
    has_pthread = bool(data.get("has_pthread_create"))
    has_main = bool(data.get("has_main"))

    cycle_issues = data.get("cycle_issues") or []
    blocking_errors = sum(
        1
        for i in cycle_issues
        if i.get("kind") == "blocking" and i.get("severity") == "error"
    )
    cycle_errors = sum(1 for i in cycle_issues if i.get("severity") == "error")
    sched_crit = int(data.get("sched_critical_count") or 0)
    sched_warn = int(data.get("sched_warn_count") or 0)
    sched_ok = data.get("sched_ok", sched_crit == 0)

    wcet_mode = manifest.get("FUSE_WCET_MODE") == "1"
    run_main = manifest.get("FUSE_RUN_MAIN") == "1"
    fuse_name = manifest.get("FUSE_NAME", "")

    profile: str | None = None
    reason: str | None = None
    confidence = "heuristic"

    if fusion_crit > 0:
        profile, reason = "minimal", f"fusion_crit={fusion_crit}"
        confidence = "high"
    elif sched_crit > 0:
        profile, reason = "minimal", f"sched_crit={sched_crit}"
        confidence = "high"
    elif not fusion_eligible:
        profile, reason = "debug", "fusion_ineligible"
        confidence = "high"
    else:
        plc_entries = [e for e in entries if e.startswith("plc_")]
        if entry.startswith("plc_") or plc_entries:
            if wcet_mode:
                profile, reason = "wcet", "plc_entry+wcet"
            else:
                profile, reason = "mainline", "plc_entry"
        elif float_in_cycle and wcet_mode:
            profile, reason = "wcet", "float_in_cycle"
        elif float_in_cycle:
            profile, reason = "hotpath", "float_in_cycle"
        elif blocking_errors > 0 and wcet_mode:
            profile, reason = "hotpath", f"cycle_blocking={blocking_errors}"
        elif cycle_errors > 0 and wcet_mode:
            profile, reason = "hotpath", f"cycle_errors={cycle_errors}"
        elif has_pthread and has_main and run_main and not wcet_mode:
            profile, reason = "generic", "pthread_main"

    manifest_hints: dict[str, str] = {}
    if entry and not manifest.get("FUSE_KTHREAD_ENTRY") and entry_trustworthy(entry, entries):
        manifest_hints["FUSE_KTHREAD_ENTRY"] = entry

    roots = ""
    if entry_trustworthy(entry, entries):
        roots = str(suggest.get("FUSE_DCE_ROOTS") or suggest.get("FUSE_HOT_PATH_FUNCTIONS") or "")
        if not roots and entry and isinstance(call_graph, dict):
            roots = join_roots(entry, call_graph)
    if roots and entry_trustworthy(entry, entries):
        if not manifest.get("FUSE_HOT_PATH_FUNCTIONS"):
            manifest_hints["FUSE_HOT_PATH_FUNCTIONS"] = roots
        if not manifest.get("FUSE_DCE_ROOTS"):
            manifest_hints["FUSE_DCE_ROOTS"] = roots

    if float_anywhere and not manifest.get("FUSE_FIXED_POINT"):
        manifest_hints["FUSE_FIXED_POINT"] = "1"

    low_jitter: bool | None = None
    if fuse_name.startswith("plc_cc_") or entry.startswith("plc_"):
        low_jitter = True
    elif wcet_mode and profile in ("wcet", "hotpath", "mainline"):
        low_jitter = True

    fixed_point: bool | None = None
    if float_in_cycle or float_anywhere:
        fixed_point = True

    candidates = build_candidates(profile, reason, {**data, "blocking_error_count": blocking_errors}, manifest)

    return {
        "version": 2,
        "tool": "plc_fusion_ast_plan",
        "profile_suggestion": profile,
        "reason": reason,
        "confidence": confidence if profile else "none",
        "candidates": candidates,
        "manifest_hints": manifest_hints,
        "pass_plan": {
            "profile": profile,
            "low_jitter": low_jitter,
            "fixed_point": fixed_point,
        },
        "ast_summary": {
            "entry": entry,
            "fusion_eligible": fusion_eligible,
            "fusion_critical_count": fusion_crit,
            "fusion_warn_count": fusion_warn,
            "float_in_cycle": float_in_cycle,
            "float_anywhere": float_anywhere,
            "has_pthread_create": has_pthread,
            "has_main": has_main,
            "cycle_error_count": cycle_errors,
            "blocking_error_count": blocking_errors,
            "sched_critical_count": sched_crit,
            "sched_warn_count": sched_warn,
            "sched_ok": sched_ok,
        },
    }


def shell_export(plan: dict[str, Any], ast_path: Path, plan_path: Path) -> str:
    s = plan["ast_summary"]
    lines = [
        "PLC_FUSION_AST_LOADED=1",
        f"PLC_FUSION_AST_JSON={ast_path}",
        f"PLC_FUSION_AST_PLAN_JSON={plan_path}",
        f"PLC_FUSION_AST_ENTRY={s.get('entry', '')}",
        f"PLC_FUSION_AST_FUSION_ELIGIBLE={1 if s.get('fusion_eligible') else 0}",
        f"PLC_FUSION_AST_FUSION_CRIT={s.get('fusion_critical_count', 0)}",
        f"PLC_FUSION_AST_FUSION_WARN={s.get('fusion_warn_count', 0)}",
        f"PLC_FUSION_AST_FLOAT_IN_CYCLE={1 if s.get('float_in_cycle') else 0}",
        f"PLC_FUSION_AST_FLOAT_ANYWHERE={1 if s.get('float_anywhere') else 0}",
        f"PLC_FUSION_AST_HAS_PTHREAD={1 if s.get('has_pthread_create') else 0}",
        f"PLC_FUSION_AST_HAS_MAIN={1 if s.get('has_main') else 0}",
        f"PLC_FUSION_AST_CYCLE_ERRORS={s.get('cycle_error_count', 0)}",
    ]
    prof = plan.get("profile_suggestion") or ""
    reason = plan.get("reason") or ""
    lines.append(f"PLC_FUSION_AST_SUGGEST_PROFILE={prof}")
    lines.append(f"PLC_FUSION_AST_PLAN_REASON={reason}")
    cands = plan.get("candidates") or []
    if cands:
        lines.append(f"PLC_FUSION_AST_CANDIDATE_2={(cands[1]['profile'] if len(cands) > 1 else '')}")
        lines.append(f"PLC_FUSION_AST_CANDIDATE_3={(cands[2]['profile'] if len(cands) > 2 else '')}")
    pp = plan.get("pass_plan") or {}
    lj = pp.get("low_jitter")
    fp = pp.get("fixed_point")
    lines.append(f"PLC_FUSION_AST_SUGGEST_LOW_JITTER={'auto' if lj is None else (1 if lj else 0)}")
    lines.append(
        f"PLC_FUSION_AST_SUGGEST_FIXED_POINT={'auto' if fp is None else (1 if fp else 0)}"
    )
    lines.append(f"PLC_FUSION_AST_FLOAT_ANYWHERE={1 if s.get('float_anywhere') else 0}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="AST → fusion_plan")
    ap.add_argument("--fuse-name", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--manifest", default="")
    ap.add_argument("--ast-json", default="")
    ap.add_argument("--plan-out", default="")
    ap.add_argument("--export", action="store_true", help="print bash exports")
    ap.add_argument("--json", action="store_true", help="print fusion_plan JSON")
    args = ap.parse_args()

    work = Path(args.work_dir)
    manifest = load_manifest_keys(Path(args.manifest) if args.manifest else None)
    if args.fuse_name and not manifest.get("FUSE_NAME"):
        manifest["FUSE_NAME"] = args.fuse_name

    ast_path = pick_ast_json(work, args.fuse_name, args.ast_json or None)
    if not ast_path:
        if args.export:
            print("PLC_FUSION_AST_LOADED=0")
        return 0

    data = json.loads(ast_path.read_text(encoding="utf-8"))
    plan = build_plan(data, manifest)
    plan["ast_source"] = str(ast_path)

    plan_path = Path(args.plan_out) if args.plan_out else work / f"{args.fuse_name}.fusion_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    if args.export:
        print(shell_export(plan, ast_path, plan_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
