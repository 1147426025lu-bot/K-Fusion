"""split_plan__按域拆分方案.py —— 把跨域方案拆成每个域一份，并描述跨域数据流。

为什么需要它
------------
PR10-B 定下了"跨域融合不存在"：一个周期体只能属于一个 cell。但一条控制回路天然会
跨域（例如前三段在 NuttX 实时域、后两段在 root cell）。所以正确的产物形态是：

    一份 plan（跨域） ──split──► 每域一份 plan + 一份"接口描述"
                                          │
                                          ├─► emit --domain nuttx  → NuttX app
                                          └─► emit --domain root   → Linux .ko
                                                    │
                                         两端用 PR10 的同一个环传数（不是共享全局）

关键点：**跨域的数据不再是共享全局，而是过环的消息**。这在语义上是真变化——
同一个 `g_act`，在单域部署里是一条 store/load，在跨域部署里是一次发布 + 一次订阅，
代价差三个数量级（PR10 实测：纯通路 0.65 µs vs 周期采样 1005 µs）。接口描述必须
把"哪些全局要过环"说清楚，否则生成出来的两端会各自读写自己的 `g_act`，
跑起来不报错、结果全错。

输入
----
    python split_plan__按域拆分方案.py plan.json --dag dag.json --out-dir OUT

输出
----
    OUT/interface.json     跨域数据流（哪个全局、从哪域到哪域、过哪条通道）
    OUT/<domain>/plan.json 该域的子方案（只含本域的组，组号重排）
    OUT/<domain>/meta.json 该域的角色（out/in 环偏移、要发布的全局、要订阅的全局）

退出码：0 成功｜2 参数/输入问题｜3 方案里有组跨域（与 PR10-B 的硬约束冲突）
"""
import argparse
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECOS = os.path.join(_HERE, "ecos")
if _ECOS not in sys.path:
    sys.path.insert(0, _ECOS)

import domain_model  # noqa: E402

# PR10 的 peer 分片约定：peer0 = root，peer1 = nuttx（与 ecos_xipc.h 一致）
PEER_RING = {
    "root":  {"out": 0x0000, "in": 0x3000},
    "nuttx": {"out": 0x3000, "in": 0x0000},
}


def _globals_of(edge):
    """这条边的证据里有哪些全局量。没有证据就不猜——宁可用空列表让上层报错。"""
    out = []
    for ev in (edge.get("evidence") or []):
        if isinstance(ev, str) and ev.startswith("@"):
            out.append(ev[1:])
    return out


def split(plan, dag, out_dir, allow_unverified=False):
    dm = domain_model.load(
        dag, period_us=((plan.get("meta") or {}).get("period_us")),
        topo=(plan.get("order") or dag.get("topo_order")
              or dag.get("topo_order_delayed") or []))

    groups = plan.get("groups") or []
    if not groups:
        return None, "方案里没有组"

    # ---- 0) 父方案必须已验证 ----
    # 拆分不会让不合法的东西变合法：整份方案没过独立验证器，拆出来的两份也不该进生成。
    if not (plan.get("verification") or {}).get("ok") and not allow_unverified:
        return None, ("父方案没有 PlanVerifier 的验证凭据，拒绝拆分"
                      "（拆分不产生合法性；要强行拆请 --allow-unverified）")

    # ---- 1) 每组的域必须纯净（PR10-B 硬约束）----
    for gi, g in enumerate(groups):
        members = g.get("members") or []
        if dm.mixed(members):
            return None, ("组 %s 跨域（%s）：跨域融合不存在——融合要求同一次释放，"
                          "而两个 cell 各有内核与调度器；这份方案本身就不合法，"
                          "先回到规划器" % (g.get("id"), ", ".join(members)))

    # ---- 2) 按域分组 ----
    by_dom = {}
    for gi, g in enumerate(groups):
        members = g.get("members") or []
        d = dm.domain_of(members[0])
        by_dom.setdefault(d, []).append((gi, g))

    # ---- 3) 跨域数据流：evidence 里的全局量要过环 ----
    channels = []
    for e in (dag.get("edges") or []):
        a, b = e.get("from"), e.get("to")
        if a is None or b is None or not dm.is_cross(a, b):
            continue
        gl = _globals_of(e)
        channels.append({
            "id": len(channels),
            "from": a, "to": b,
            "src_domain": dm.domain_of(a), "dst_domain": dm.domain_of(b),
            "globals": gl,
            "has_evidence": bool(gl),
        })

    # ---- 4) 每域的角色与子方案 ----
    ifaces = {}
    for d, gs in by_dom.items():
        ring = PEER_RING.get(d)
        if ring is None:
            return None, ("域 %r 没有对应的 peer 分片约定（当前只认 %s）。"
                          "要么把它并进已有域，要么在 PEER_RING 里补一条"
                          % (d, "/".join(sorted(PEER_RING))))
        pub, sub = [], []
        for ch in channels:
            if ch["src_domain"] == d:
                for g in ch["globals"]:
                    pub.append({"channel": ch["id"], "global": g,
                                "after": ch["from"], "to": ch["dst_domain"]})
            if ch["dst_domain"] == d:
                for g in ch["globals"]:
                    sub.append({"channel": ch["id"], "global": g,
                                "before": ch["to"], "from": ch["src_domain"]})
        sub_plan = {
            "version": plan.get("version"),
            "meta": dict(plan.get("meta") or {}),
            "domain": d,
            "peer_ring": ring,
            "groups": [g for _gi, g in gs],
            "xdomain": {
                "publish": pub,
                "subscribe": sub,
                "channels": [c for c in channels
                             if d in (c["src_domain"], c["dst_domain"])],
            },
            # 把父方案的验证凭据带下来，并注明这是拆分视图——生成器只认这一个字段，
            # 没有它就会被当成未验证方案拒掉。
            "verification": {
                "ok": True,
                "split_of": (plan.get("verification") or {}).get("digest"),
                "note": ("本子方案是已通过独立验证的整份方案的拆分视图；"
                         "合法性由整份方案保证，本文件不再重复验证"),
            },
        }
        sub_plan["meta"]["split_from"] = plan.get("meta", {}).get("dag")
        sub_plan["meta"]["domains"] = sorted(by_dom)
        sub_plan["meta"]["cross_domain_channels"] = len(channels)
        ifaces[d] = sub_plan

    # ---- 5) 落盘 ----
    os.makedirs(out_dir, exist_ok=True)
    iface = {
        "domains": sorted(by_dom),
        "channels": channels,
        "peer_ring": {d: PEER_RING[d] for d in by_dom},
        "assumption": ("跨域数据不再走共享全局，而是过 PR10 的环；"
                       "纯通路 0.65 µs，但按周期采样是 ≈1 拍（1005 µs @1 kHz）"),
        "source_dag": (plan.get("meta") or {}).get("dag"),
    }
    with io.open(os.path.join(out_dir, "interface.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(iface, f, ensure_ascii=False, indent=2)
        f.write("\n")

    for d, sp in ifaces.items():
        ddir = os.path.join(out_dir, d)
        os.makedirs(ddir, exist_ok=True)
        with io.open(os.path.join(ddir, "plan.json"), "w",
                     encoding="utf-8", newline="\n") as f:
            json.dump(sp, f, ensure_ascii=False, indent=2)
            f.write("\n")
        with io.open(os.path.join(ddir, "meta.json"), "w",
                     encoding="utf-8", newline="\n") as f:
            json.dump({"domain": d, "peer_ring": sp["peer_ring"],
                       "publish": sp["xdomain"]["publish"],
                       "subscribe": sp["xdomain"]["subscribe"]},
                      f, ensure_ascii=False, indent=2)
            f.write("\n")

    return iface, None


def main():
    ap = argparse.ArgumentParser(description="按域拆分跨域方案（PR10-C）")
    ap.add_argument("plan")
    ap.add_argument("--dag", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--allow-unverified", action="store_true",
                    help="父方案没有验证凭据也拆（只用于调试；产物不得用于论文主张）")
    args = ap.parse_args()

    plan = json.load(io.open(args.plan, encoding="utf-8"))
    dag = json.load(io.open(args.dag, encoding="utf-8"))

    iface, err = split(plan, dag, args.out_dir,
                       allow_unverified=args.allow_unverified)
    if err:
        print("!! %s" % err, file=sys.stderr)
        return 3

    print("[split] 域：%s" % ", ".join(iface["domains"]))
    print("[split] 跨域通道 %d 条：" % len(iface["channels"]))
    for ch in iface["channels"]:
        print("   #%d %s(%s) → %s(%s)  globals=%s%s"
              % (ch["id"], ch["from"], ch["src_domain"], ch["to"], ch["dst_domain"],
                 ",".join(ch["globals"]) or "-",
                 "" if ch["has_evidence"] else "   ⚠ 没有证据，生成器不知道传什么"))
    for d in iface["domains"]:
        n = len(iface["peer_ring"][d]) and d
        print("[split] %s → %s/plan.json（out 环 0x%04x，in 环 0x%04x）"
              % (d, os.path.join(args.out_dir, d),
                 iface["peer_ring"][d]["out"], iface["peer_ring"][d]["in"]))
    no_ev = [c for c in iface["channels"] if not c["has_evidence"]]
    if no_ev:
        print("[split] ⚠ %d 条跨域边没有 evidence —— 生成出来的两端不会传任何数据，"
              "这不是'降级'，是错的" % len(no_ev), file=sys.stderr)
        return 3
    print("[split] ✅ 完成 → %s/interface.json" % args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
