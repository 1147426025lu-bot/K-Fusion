#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dag_plot__DAG绘图.py — 把 dag_build 产出的 dag.json 画成图（只依赖 matplotlib）

为什么不用 graphviz：板上和开发机都没有 `dot`，而 matplotlib 已经是本项目
（三域 CDF 图）的既有依赖。分层用最长路径，边按 `kind` 上色：

    call      灰虚线      data(RAW)  蓝实线
    anti      橙点线      conflict   紫粗线（双箭头，无方向）
    candidate 红虚线      （未证明）

分层规则：只按**产生顺序**的边（call/data/anti）做最长路径分层；
`conflict` 不参与分层（无方向），但会画出来。有环时仍能画，标题标出。

用法：
    python dag_plot__DAG绘图.py dag.json -o dag.png [--title "…"] [--no-evidence]
"""
import argparse
import io
import json
import sys
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# 中文字体：与三域 CDF 图同一套（Linux 上回落到 Noto/DejaVu）
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                          "Noto Sans CJK SC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

KIND_STYLE = {
    "call":      dict(color="#6b7280", ls="--", lw=1.2, label="call", arrow="->"),
    "data":      dict(color="#1d4ed8", ls="-",  lw=1.8, label="data (RAW)",
                      arrow="->"),
    "anti":      dict(color="#b45309", ls=":",  lw=1.8, label="anti (WAR)",
                      arrow="->"),
    "conflict":  dict(color="#7c3aed", ls="-",  lw=2.4,
                      label="conflict (WAW，无方向)", arrow="<->"),
    "candidate": dict(color="#b91c1c", ls="--", lw=1.2,
                      label="candidate（未证明）", arrow="->"),
}
WRAP = 24          # 标签每行字符数（中英混排按字符数近似）
FONT = 7.4


def layer_nodes(nodes, edges):
    """最长路径分层；有环时松弛会被截断（不会死循环）。"""
    names = [n["name"] for n in nodes]
    pred = {n: set() for n in names}
    for e in edges:
        if e["kind"] == "conflict":
            continue
        if e["from"] in pred and e["to"] in pred:
            pred[e["to"]].add(e["from"])

    depth = {n: 0 for n in names}
    for _ in range(len(names)):
        changed = False
        for n in names:
            for p in pred[n]:
                if depth[n] < depth[p] + 1:
                    depth[n] = depth[p] + 1
                    changed = True
        if not changed:
            break
    return depth


def node_label(n):
    head = n["name"]
    if n.get("fns") and n["fns"] != [n["name"]]:
        head += "\n[" + ", ".join(n["fns"]) + "]"
    eff = ", ".join(n.get("effects") or []) or "-"
    lines = [head] + (textwrap.wrap(eff, WRAP) or ["-"])
    if n.get("contexts"):
        lines.append("ctx: " + ", ".join(n["contexts"]))
    if n.get("interface_unresolved"):
        lines.append("[接口未闭合: " + ", ".join(n["interface_unresolved"]) + "]")
    return "\n".join(lines)


def draw(dag, out, title, show_evidence=True):
    """全部布局用**英寸**坐标：1 数据单位 = 1 英寸，字号与箱宽可直接换算。"""
    nodes = dag["nodes"]
    edges = list(dag["edges"]) + list(dag.get("candidates") or [])
    depth = layer_nodes(nodes, dag["edges"])

    layers = {}
    for n in nodes:
        layers.setdefault(depth[n["name"]], []).append(n)
    for v in layers.values():
        v.sort(key=lambda n: n["name"])

    labels = {n["name"]: node_label(n) for n in nodes}

    def width_of(s):
        # 字宽（以"一个汉字"为单位）：CJK 1.0，ASCII 0.55
        return sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in s)

    char_in = FONT / 72.0                       # 一个汉字的宽 ≈ 字号
    maxw = max(width_of(ln) for lab in labels.values() for ln in lab.split("\n"))
    maxlines = max(len(lab.split("\n")) for lab in labels.values())
    bw = maxw * char_in + 0.30
    bh = maxlines * char_in * 1.42 + 0.20
    gap_x, gap_y = 0.38, 0.72

    pos = {}
    for d, ns in layers.items():
        span = len(ns) * bw + (len(ns) - 1) * gap_x
        for i, n in enumerate(ns):
            pos[n["name"]] = (-span / 2 + bw / 2 + i * (bw + gap_x),
                              -d * (bh + gap_y))

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x0, x1 = min(xs) - bw / 2 - 0.30, max(xs) + bw / 2 + 0.30
    y0, y1 = min(ys) - bh / 2 - 0.55, max(ys) + bh / 2 + 0.40
    fig_w, fig_h = max(6.6, x1 - x0), max(3.0, y1 - y0)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)

    for n in nodes:
        x, y = pos[n["name"]]
        flag = bool(n.get("interface_unresolved"))
        ax.add_patch(FancyBboxPatch(
            (x - bw / 2, y - bh / 2), bw, bh,
            boxstyle="round,pad=0.02,rounding_size=0.07",
            linewidth=1.1, edgecolor="#4338ca" if not flag else "#b45309",
            facecolor="#eef2ff" if not flag else "#fff7e6", zorder=3))
        ax.text(x, y, labels[n["name"]], ha="center", va="center",
                fontsize=FONT, zorder=4, linespacing=1.25)

    dense = len(edges) > 12
    seen_kind = set()
    for e in edges:
        a, b = e["from"], e["to"]
        if a not in pos or b not in pos:
            continue
        st = KIND_STYLE[e["kind"]]
        (xa, ya), (xb, yb) = pos[a], pos[b]
        down = ya > yb
        y1e = ya - bh / 2 if down else ya + bh / 2
        y2e = yb + bh / 2 if down else yb - bh / 2
        ax.annotate("", xy=(xb, y2e), xytext=(xa, y1e),
                    arrowprops=dict(arrowstyle=st["arrow"], color=st["color"],
                                    linestyle=st["ls"], linewidth=st["lw"],
                                    shrinkA=1, shrinkB=1,
                                    connectionstyle="arc3,rad=0.05"), zorder=2)
        seen_kind.add(e["kind"])
        if not show_evidence or e["kind"] not in ("data", "anti", "conflict"):
            continue
        ev = e["evidence"][: 1 if dense else 2]
        lab = ", ".join(ev)
        if e.get("evidence_count", 0) > len(ev):
            lab += " …"
        ax.text((xa + xb) / 2, (y1e + y2e) / 2, lab, fontsize=5.9,
                color=st["color"], ha="center", va="bottom", zorder=5,
                bbox=dict(boxstyle="round,pad=0.10", fc="white", ec="none",
                          alpha=0.75))

    handles = [plt.Line2D([], [], color=KIND_STYLE[k]["color"],
                          ls=KIND_STYLE[k]["ls"], lw=KIND_STYLE[k]["lw"],
                          label=KIND_STYLE[k]["label"])
               for k in ("call", "data", "anti", "conflict", "candidate")
               if k in seen_kind]
    if handles:
        ax.legend(handles=handles, loc="upper center", fontsize=7.0,
                  frameon=False, ncol=min(3, len(handles)),
                  bbox_to_anchor=(0.5, 1.02))

    ax.set_title(title, fontsize=9.6, pad=22)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out, dpi=170)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dag_json")
    ap.add_argument("-o", "--out", default="dag.png")
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-evidence", action="store_true",
                    help="不打边上的区域标签（节点很多时更清爽）")
    args = ap.parse_args()

    dag = json.load(io.open(args.dag_json, encoding="utf-8"))
    meta = dag.get("meta") or {}
    task = meta.get("task") or {}
    title = args.title or "任务 DAG（%s 视图%s）" % (
        meta.get("mode", "?"), "：%s" % task["name"] if task.get("name") else "")
    if dag.get("cycles"):
        title += "  （有环）"
    draw(dag, args.out, title, show_evidence=not args.no_evidence)
    print("写出", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
