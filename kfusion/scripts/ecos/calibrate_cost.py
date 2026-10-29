# -*- coding: utf-8 -*-
"""PR-18B：残差校准。只写 calibration 层。

    Δ̂(x) = Q99_train(x) − R̂_static(d)
    Q̂99,cal(x) = R̂_static(d) + Δ̂(x)

x = (域, 成员, 相位, 释放次数, 调度类, 负载, 平台)。键不一致 →
insufficient_evidence，禁止把 FIFO 的 Δ 套到 CFS、把 load=0 套到 load=4、
把三释放套到单释放。

这是经验统计预测器，不是 WCET；阶段 p99 不可加。训练轮不得读验证轮。
"""
from __future__ import print_function

import copy
import hashlib
import json

import feedback_profile as fb

MODEL = "residual_add_v1"
CALIBRATION_ID = "pr18b-residual-add-v1"

TRANSFER_FIELDS = (
    "domain", "members", "phases_us", "n_releases", "n_groups", "fused",
    "sched", "load", "platform",
)


def key_id(match_key):
    slim = {}
    for k in TRANSFER_FIELDS:
        slim[k] = (match_key or {}).get(k)
    return json.dumps(slim, sort_keys=True, separators=(",", ":"))


def insufficient(reason, match_key=None):
    return {
        "status": "insufficient_evidence",
        "model": MODEL,
        "calibration_id": CALIBRATION_ID,
        "reason": reason,
        "delta_hat_us": None,
        "q99_cal_us": None,
        "match_key": match_key,
        "note": "不得借用其他形状/调度类/负载/平台的残差",
    }


def fit_one(rhat, q99_train, p99s, train_rounds, match_key, hashes=None):
    if rhat is None:
        return insufficient("无静态 R̂", match_key)
    if q99_train is None:
        return insufficient("训练轮无 Q99", match_key)
    p99s = [float(x) for x in (p99s or [])]
    delta = float(q99_train) - float(rhat)
    span = (max(p99s) - min(p99s)) if p99s else None
    hid = hashlib.sha256()
    hid.update(CALIBRATION_ID.encode("utf-8"))
    hid.update(key_id(match_key).encode("utf-8"))
    hid.update(json.dumps(sorted(train_rounds or []), separators=(",", ":")).encode("utf-8"))
    for k in ("src_sha256", "plan_sha256"):
        hid.update(((hashes or {}).get(k) or "").encode("utf-8"))
    return {
        "status": "fitted",
        "model": MODEL,
        "calibration_id": CALIBRATION_ID,
        "instance_sha256": hid.hexdigest()[:16],
        "train_rounds": sorted(int(x) for x in (train_rounds or [])),
        "n_train": len(p99s),
        "q99_train_us": round(float(q99_train), 3),
        "rhat_static_us": round(float(rhat), 3),
        "delta_hat_us": round(delta, 3),
        "q99_cal_us": round(float(rhat) + delta, 3),
        "p99_across_us": ([min(p99s), max(p99s)] if p99s else None),
        "uncertainty_span_us": None if span is None else round(span, 3),
        "match_key": copy.deepcopy(match_key),
        "claim": "经验残差；不是 WCET；不可跨匹配键迁移",
    }


def lookup(table, match_key):
    """精确键命中。table: {key_id: calibration}。"""
    rec = (table or {}).get(key_id(match_key))
    if rec is None:
        return insufficient("无该匹配键的训练残差", match_key)
    return copy.deepcopy(rec)


def apply_q99(static, calib):
    """返回校准后的 Q99 预测，不修改 static。"""
    static = static or {}
    calib = calib or {}
    rhat = static.get("predicted_R_us")
    if calib.get("status") != "fitted" or rhat is None:
        return None
    if calib.get("rhat_static_us") is not None and abs(
            float(calib["rhat_static_us"]) - float(rhat)) > 1e-6:
        return None
    return round(float(rhat) + float(calib["delta_hat_us"]), 3)


def fit_table(rows, train_rounds, hashes=None):
    """rows 来自 build_report，其 measurement 必须已经是训练轮画像。"""
    table = {}
    for r in rows or []:
        mk = r.get("match_key") or {}
        meas = r.get("measurement") or {}
        static = r.get("static_prediction") or {}
        if meas.get("status") != "measured":
            table[key_id(mk)] = insufficient(meas.get("reason") or "未测", mk)
            continue
        q = meas.get("q99_resp_us")
        across = meas.get("q99_across_rounds") or []
        p99s = list(across) if across else ([q] if q is not None else [])
        table[key_id(mk)] = fit_one(
            static.get("predicted_R_us"), q, p99s,
            train_rounds, mk, hashes=hashes)
    return table


def apply_to_report(report, table):
    """只改 calibration；static / measurement / error(相对静态) 保持原样。"""
    out = copy.deepcopy(report)
    applied = []
    for r in out.get("rows") or []:
        static = r.get("static_prediction")
        meas = r.get("measurement")
        calib = lookup(table, r.get("match_key"))
        slim = {k: v for k, v in r.items()
                if k not in ("static_prediction", "measurement",
                             "calibration", "error")}
        rec = fb.attach_layers(slim, static, meas, calib)
        rec["error"] = r.get("error")  # 静态误差，18A 口径
        qcal = apply_q99(static, calib)
        rec["calibrated"] = {
            "q99_cal_us": qcal,
            "holdout_minus_cal_us": (
                None if (qcal is None or (meas or {}).get("status") != "measured")
                else round(float(meas["q99_resp_us"]) - float(qcal), 3)),
        }
        applied.append(rec)
    out["rows"] = applied
    out["calibration_model"] = MODEL
    out["calibration_id"] = CALIBRATION_ID
    out["claim"] = (
        "calibration 由训练轮残差得到；未覆盖 static_prediction/measurement；非 WCET"
    )
    return out


def holdout_summary(rows):
    xs = []
    for r in rows or []:
        d = (r.get("calibrated") or {}).get("holdout_minus_cal_us")
        if d is None:
            continue
        mk = r.get("match_key") or {}
        xs.append((int(mk.get("load") or 0), mk.get("sched"),
                   int(mk.get("n_releases") or 0), d))
    if not xs:
        return {"n": 0}
    abserr = [abs(t[3]) for t in xs]
    return {
        "n": len(xs),
        "mae_us": round(sum(abserr) / float(len(abserr)), 3),
        "max_abs_us": round(max(abserr), 3),
        "note": "holdout Q99 − (R̂+Δ̂_train)；同键才有值，跨键不会出现在这里",
    }
