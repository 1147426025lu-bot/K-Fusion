#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-9C：所有生成分支共用的时间/版本门禁。

采样、提交钉扎与 TEIR 数据版本（same-job / previous-job / age=1）必须在
STKP 入选方案、LET 旁路方案、codegen 之前用同一入口检查。

LET 事件不能单独把「融合前拉」变成可部署；要部署仍须通过无 LET 的窗口检查，
或由 STKP 已验证的分组来生成。
"""
from __future__ import print_function

import argparse
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, "..", ".."))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def check(teir, plan, contract=None):
    teir_v = _load(os.path.join(KF, "verify", "plan_verify.py"), "teir_verify")
    let = _load(os.path.join(HERE, "let_decide.py"), "let_decide")
    ok_teir, d_teir = teir_v.verify(teir, plan)
    d_pin = []
    d_raw = []
    hydrated = None
    if contract:
        hydrated = let.hydrate_pins(contract, teir)
        ph = let.phase_map(teir)
        has_let = bool(plan.get("let_events"))
        d_pin = let.pin_violations(plan, ph, hydrated, apply_let=has_let)
        d_raw = let.pin_violations(plan, ph, hydrated, apply_let=False)
        d_raw.extend(let.period_ok(plan, hydrated))
        d_raw.extend(let.window_ok(hydrated))
    deploy_ok = bool(ok_teir) and not d_raw
    return {
        "teir_ok": bool(ok_teir),
        "teir_diagnostics": d_teir,
        "pin_ok_with_let": not d_pin,
        "pin_diagnostics": d_pin,
        "window_ok_without_let": not d_raw,
        "window_diagnostics": d_raw,
        "deploy_ok": deploy_ok,
        "ok": deploy_ok,
        "contract_pinned": sorted((hydrated or {}).get("pins") or {})
        if hydrated else [],
    }


def main():
    ap = argparse.ArgumentParser(description="共用时间/版本门禁")
    ap.add_argument("--teir", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--contract", default=None)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()
    teir = load_json(args.teir)
    plan = load_json(args.plan)
    contract = load_json(args.contract) if args.contract else None
    rep = check(teir, plan, contract)
    if args.out:
        d = os.path.dirname(os.path.abspath(args.out))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print("contract_gate teir_ok=%s window_raw=%s deploy_ok=%s" % (
        rep["teir_ok"], rep["window_ok_without_let"], rep["deploy_ok"]))
    for d in (rep.get("teir_diagnostics") or [])[:4]:
        print("  TEIR %s %s" % (d.get("code"), d.get("where") or ""))
    for d in (rep.get("window_diagnostics") or [])[:4]:
        print("  WIN %s %s" % (d.get("code"), d.get("where") or ""))
    return 0 if rep["deploy_ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
