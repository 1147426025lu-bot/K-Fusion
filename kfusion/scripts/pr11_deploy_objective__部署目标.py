#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr11_deploy_objective__部署目标.py — PR-11：调度类进部署决策空间。

PR-10D 的结论是「4.7× 来自调度类，不是内核化」；PR-10E-C 的结论是
「毫秒尖峰 100% 在等调度，不在执行」。这两条合起来意味着：**部署决策里
少了一个维度**——只按预测执行时间选部署，会选出一个在 CFS 下必然超期的
方案，而把线程设成 FIFO 就白拿 50µs。

本脚本把这维度补上：

    J(d,s) = α·Q99_resp(d,s) + β·R_miss(d,s) + γ·C_boundary(d)

d 是 STKP 候选（分组×域），s 是调度类（cfs/fifo99）。Q99 与 R_miss 取
PR-10E-A 的 10 轮实测，不做模型外推；测不了的组合直接不参与排名。

三个子命令：
  profile  基线 json → sched_profile.json（每 (部署,调度类) 一格）
  decide   候选集 × 调度类 → decision.json + 表格
  selftest 纯合成用例，不读仓库数据

用法：
  python3 kfusion/scripts/pr11_deploy_objective__部署目标.py profile \
      --baseline kfusion/docs/pr10e_baseline/baseline.json \
      --boundary kfusion/docs/pr10c_boundary/boundary.json \
      -o kfusion/docs/pr11_deploy_objective/sched_profile.json
  python3 kfusion/scripts/pr11_deploy_objective__部署目标.py decide \
      --profile kfusion/docs/pr11_deploy_objective/sched_profile.json \
      --stkp <stkp.json> --alpha 1 --beta 0 --gamma 0 -o decision.json

退出码：0 有可排名结果 / 3 全部不可排名（fail-closed）/ 4 自测失败
"""
from __future__ import print_function

import argparse
import hashlib
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.dirname(KF)


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


do = _load_mod("deploy_objective",
               os.path.join(KF, "scripts", "ecos", "deploy_objective.py"))


def load_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path):
    ap = os.path.abspath(path)
    if ap.startswith(REPO + os.sep):
        return ap[len(REPO) + 1:].replace(os.sep, "/")
    return ap


# --------------------------------------------------------------------------

def cmd_profile(args):
    base_path = args.baseline
    doc = load_json(base_path)
    env = load_json(args.env) if args.env else None
    if env is not None and not env.get("complete"):
        print("[warn] 环境证据不完整（缺 %s）：这份画像会被决策门禁挡住"
              % ",".join(env.get("missing") or []))
    prof = do.aggregate_baseline(doc, source=rel(base_path),
                                 sha256=sha256_file(base_path), env=env)
    if args.shapes:
        shp = load_json(args.shapes)
        extra = do.aggregate_shapes(shp, env=env)
        prof = do.merge_profiles(prof, extra)
        print("合并形状画像：%s" % ", ".join(sorted(extra["deployments"])))
    if args.boundary:
        bdoc = load_json(args.boundary)
        noop = bdoc.get("noop_ioctl") or {}
        p50 = noop.get("roundtrip_p50_med")
        p99 = noop.get("roundtrip_p99_med")
        runs = noop.get("runs") or []
        if p50 is None:
            raise SystemExit("边界文件里没有 noop_ioctl.roundtrip_p50_med")
        prof["boundary"] = {
            "t_ioctl_us": {
                "value": float(p50),
                "evidence": "measured",
                "p50_us": float(p50),
                "p99_us": None if p99 is None else float(p99),
                "samples": sum(int(r.get("cycles") or 0) for r in runs),
                "rounds": len(runs),
                "source_log": rel(args.boundary),
                "statistic": "p50_of_noop_roundtrip",
                "note": "空模块只做 copy_in/out+时间戳+互斥，是同步边界的地板价；"
                        "含内核计算时为上界。非 WCET。",
            }
        }
    else:
        print("[warn] 未给 --boundary：C_boundary 项将不可用（γ>0 时无候选可排名）")
    dump_json(args.out, prof)
    print("wrote %s" % args.out)
    for name, blk in sorted(prof["deployments"].items()):
        sh = blk.get("shape") or {}
        for s, cell in sorted((blk.get("sched") or {}).items()):
            if cell.get("evidence") == "missing":
                print("  %-14s %-7s MISSING %s" % (name, s, cell.get("reason")))
                continue
            q = (cell.get("q99_resp_us") or {}).get("value")
            mr = (cell.get("miss_rate") or {})
            tail = cell.get("tail") or {}
            print("  %-14s %-7s Q99=%-8s miss=%.6f 轮数=%d 拍=%d  "
                  "start_lat_p50=%s%s"
                  % (name, s, q, mr.get("value") or 0.0, cell.get("rounds"),
                     cell.get("n_cycles"),
                     tail.get("start_lat_p50_median_us"),
                     "" if (sh.get("members") or sh.get("n_groups"))
                     else "  [形状键缺失]"))
    for u in prof.get("unmeasured") or []:
        print("  [未测] %s：%s" % (u.get("scenario") or u.get("domain"),
                                   u.get("reason")))
    return 0


def cmd_decide(args):
    prof = do.load_profile(args.profile)
    stkp = load_json(args.stkp)
    cands = stkp.get("candidates") or []
    if not cands:
        raise SystemExit("候选集为空：%s" % args.stkp)
    deadline = args.deadline
    if deadline is None:
        for k in ("deadline_us",):
            if (stkp.get("task") or {}).get(k) is not None:
                deadline = stkp["task"][k]
    doc = do.decide(cands, prof, alpha=args.alpha, beta=args.beta,
                    gamma=args.gamma, miss_bound=args.miss_bound,
                    deadline_us=deadline,
                    scheds=tuple(s.strip() for s in args.scheds.split(",")
                                 if s.strip()),
                    allow_incomplete_env=args.allow_incomplete_env)
    doc["sources"] = {
        "profile": rel(args.profile),
        "profile_sha256": sha256_file(args.profile),
        "stkp": rel(args.stkp),
        "stkp_sha256": sha256_file(args.stkp),
        "stkp_chosen_as_generated": stkp.get("chosen"),
    }
    dump_json(args.out, doc)

    print("J(d,s) = %.3g·Q99 + %.3g·R_miss + %.3g·C_boundary   "
          "(miss_bound=%s)" % (args.alpha, args.beta, args.gamma,
                               args.miss_bound))
    print("%-46s %-8s %-7s %10s %10s %10s %10s"
          % ("候选", "部署", "调度类", "Q99", "R_miss", "C_bd", "J"))
    for e in doc["all_evals"]:
        if e["rankable"]:
            t = e["terms"]
            print("%-46s %-8s %-7s %10.2f %10.6f %10.2f %10.3f"
                  % (str(e["candidate_id"])[:46], e["deployment"], e["sched"],
                     t["Q99_resp_us"], t["R_miss"], t["C_boundary_us"],
                     e["J_us"]))
        else:
            print("%-46s %-8s %-7s  —— 不可排名：%s"
                  % (str(e["candidate_id"])[:46], e["deployment"] or "?",
                     e["sched"], "; ".join(e["missing"])[:70]))
    if doc.get("blocked"):
        print("\n拒绝决策：%s" % doc["blocked"])
        print("（缺平台身份的画像不得进决策；确需对照加 --allow-incomplete-env，"
              "但那种结果不能进论文表）")
        return 3
    if doc["chosen"]:
        c = doc["chosen"]
        print("\n入选：%s  %s @ %s  J=%.3f"
              % (c["candidate_id"], c["deployment"], c["sched"], c["J_us"]))
        return 0
    print("\n无可排名结果（fail-closed）：没有组合同时具备实测 Q99 与 R_miss")
    return 3


# --------------------------------------------------------------------------
# 自测：不读仓库数据，纯合成，覆盖 fail-closed 与排序翻转
# --------------------------------------------------------------------------

def _synth_profile(q99, miss=None, scheds=("cfs", "fifo99"), n_groups=3):
    miss = miss or {}
    prof = {
        "kind": do.PROFILE_KIND, "version": 1,
        "deployments": {}, "boundary": {},
    }
    for dom, scen in (("linux_userspace", "user_only"),
                      ("mixed", "partial_kernel")):
        blocks = {}
        for s in scheds:
            if q99.get((scen, s)) is None:
                continue
            blocks[s] = {
                "rounds": 10, "n_cycles": 50000,
                "q99_resp_us": {"value": float(q99[(scen, s)]),
                                "evidence": "measured", "rounds": 10,
                                "n_cycles": 50000},
                "miss_rate": {"value": float(miss.get((scen, s), 0.0)),
                              "evidence": "measured", "n_cycles": 50000,
                              "rounds": 10,
                              "count": int(miss.get((scen, s), 0.0) * 50000),
                              "ci95_wilson": [0.0, float(miss.get((scen, s), 0.0)) * 3]},
                "tail": {"resp_max_worst_us": 900.0},
            }
        prof["deployments"][scen] = {
            "maps_to_domain": dom,
            "shape": dict(do.DEPLOYMENT_SHAPE[scen]),
            "sched": blocks,
        }
    prof["boundary"] = {"t_ioctl_us": {"value": 2.0, "evidence": "test",
                                      "samples": 15000}}
    prof["env_evidence"] = {
        "kind": "env_evidence", "version": 1, "complete": True, "missing": [],
        "host": {"model": "synthetic", "nproc": 4},
        "kernel": {"release": "test", "preempt_class": "PREEMPT",
                   "config_source": "test"},
        "sched": {"pin_cpu": 3, "rt_runtime_us": "950000"},
        "code": {"git_rev": "test", "source_sha256": "test"},
    }
    return prof


def _cand(cid, domain, n_groups=3, fused=False, n_cross=0, ready=True):
    order = ["stage_sample", "stage_pid", "stage_sink"]
    groups = [{"members": order}] if n_groups == 1 else \
             [{"members": [m]} for m in order[:n_groups]]
    return {"id": cid, "domain": domain, "fused": fused,
            "deployment_ready": ready,
            "plan": {"order": order, "groups": groups},
            "cost": {"handoff_channels": n_cross}}


def cmd_selftest(args):
    fails = []

    def chk(name, cond, detail=""):
        if cond:
            print("  ok   %s" % name)
        else:
            fails.append(name)
            print("  FAIL %s %s" % (name, detail))

    # 1. CFS 尾部很差，FIFO 好 → 应该选 FIFO
    p = _synth_profile({("user_only", "cfs"): 77.0, ("user_only", "fifo99"): 20.0,
                        ("partial_kernel", "cfs"): 78.0,
                        ("partial_kernel", "fifo99"): 22.0},
                       {("user_only", "cfs"): 0.00066,
                        ("partial_kernel", "cfs"): 0.00066})
    d = do.decide([_cand("U", "linux_userspace"), _cand("M", "mixed", n_cross=1)],
                  p, alpha=1.0, beta=0.0, gamma=0.0)
    chk("选 FIFO", d["chosen"] and d["chosen"]["sched"] == "fifo99",
        d["chosen"])
    chk("选用户态（Q99 更低）", d["chosen"] and d["chosen"]["candidate_id"] == "U")

    # 2. beta 拉大 → CFS 因为超期率被压下去（同一组数据，权重变选择变）
    d2 = do.decide([_cand("U", "linux_userspace")], p,
                   alpha=0.0, beta=1.0e6, gamma=0.0)
    chk("beta 主导时 R_miss=0 的 FIFO 赢",
        d2["chosen"] and d2["chosen"]["sched"] == "fifo99")

    # 3. 形状不匹配：全融合候选不得借用 3 组不融合的实测
    d3 = do.decide([_cand("F", "linux_userspace", n_groups=1, fused=True)], p)
    chk("融合形状 fail-closed", d3["chosen"] is None
        and d3["n_rankable"] == 0, d3["n_rankable"])
    chk("形状不匹配写进 missing",
        any("该形状未测" in m for e in d3["all_evals"] for m in e["missing"]))

    # 4. 内核自主部署没有画像 → 不可排名（不许拿用户态的数顶替）
    d4 = do.decide([_cand("K", "linux_kernel")], p)
    chk("内核自主未测 fail-closed", d4["chosen"] is None)

    # 5. 边界单价缺失 + γ>0 且有跨界 → 不可排名
    p5 = _synth_profile({("user_only", "fifo99"): 20.0,
                         ("partial_kernel", "fifo99"): 22.0})
    p5["boundary"] = {}
    d5 = do.decide([_cand("M", "mixed", n_cross=1)], p5, gamma=1.0)
    chk("边界单价缺测 fail-closed", d5["chosen"] is None
        and any("C_boundary" in m for e in d5["all_evals"] for m in e["missing"]))

    # 6. 跨界数为 0 时 γ>0 不因边界单价缺失而拒
    d6 = do.decide([_cand("U", "linux_userspace")], p5, gamma=1.0)
    chk("无跨界时不需要边界单价", d6["chosen"] is not None)

    # 7. miss_bound=upper 用 Wilson 上界，J 只会变大
    p7 = _synth_profile({("user_only", "cfs"): 77.0, ("user_only", "fifo99"): 20.0},
                        {("user_only", "cfs"): 0.00066})
    lo = do.evaluate(p7, "linux_userspace", "cfs", 0, beta=1.0e5,
                     miss_bound="point", cand_shape={"n_groups": 3,
                                                     "fused": False})
    hi = do.evaluate(p7, "linux_userspace", "cfs", 0, beta=1.0e5,
                     miss_bound="upper", cand_shape={"n_groups": 3,
                                                     "fused": False})
    chk("upper ≥ point", hi["J_us"] >= lo["J_us"], (lo["J_us"], hi["J_us"]))

    # 8. 不可部署候选不参与入选
    d8 = do.decide([_cand("U", "linux_userspace", ready=False),
                    _cand("M", "mixed", n_cross=1)], p)
    chk("deployment_ready=False 不入选",
        d8["chosen"] is None or d8["chosen"]["candidate_id"] != "U")

    # 9. Wilson 区间对 0 事件给出非零上界（这正是 CFS 超期率该被质疑的地方）
    ci = do.wilson_interval(0, 50000)
    chk("0 事件上界>0", ci[1] > 0, ci)

    print("\nselftest %s (%d 项失败)"
          % ("PASS" if not fails else "FAIL", len(fails)))
    return 0 if not fails else 4


def main():
    ap = argparse.ArgumentParser(description="PR-11 部署目标 J(d,s)")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("profile", help="基线/形状 json → sched_profile.json")
    p1.add_argument("--baseline", required=True)
    p1.add_argument("--shapes", default=None,
                    help="形状补测 shapes.json（与 baseline 合并，按形状索引）")
    p1.add_argument("--env", default=None,
                    help="环境证据 json；缺失或不完整则决策门禁会拒绝")
    p1.add_argument("--boundary", default=None)
    p1.add_argument("-o", "--out", required=True)

    p2 = sub.add_parser("decide", help="候选 × 调度类 → 决策")
    p2.add_argument("--profile", required=True)
    p2.add_argument("--stkp", required=True)
    p2.add_argument("--alpha", type=float, default=1.0)
    p2.add_argument("--beta", type=float, default=0.0,
                    help="µs / 单位超期率；1e5 表示 1%% 超期折算 1000µs")
    p2.add_argument("--gamma", type=float, default=0.0,
                    help="跨界结构代价权重；Q99 已含往返，>0 是重复计时的策略选择")
    p2.add_argument("--miss-bound", dest="miss_bound", default="point",
                    choices=("point", "upper"))
    p2.add_argument("--scheds", default=",".join(do.SCHED_CLASSES),
                    help="参与决策的调度类，逗号分隔；只给 cfs 可复现「旧决策空间」")
    p2.add_argument("--allow-incomplete-env", dest="allow_incomplete_env",
                    action="store_true",
                    help="环境证据不完整也允许决策（只用于对照，不得进论文表）")
    p2.add_argument("--deadline", type=float, default=None)
    p2.add_argument("-o", "--out", required=True)

    sub.add_parser("selftest", help="纯合成自测")

    args = ap.parse_args()
    if args.cmd == "profile":
        return cmd_profile(args)
    if args.cmd == "decide":
        return cmd_decide(args)
    if args.cmd == "selftest":
        return cmd_selftest(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
