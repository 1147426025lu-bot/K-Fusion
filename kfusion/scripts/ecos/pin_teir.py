# -*- coding: utf-8 -*-
"""从源注释 kfusion:pin <domain> 收紧 TEIR.allowed_domains。"""
from __future__ import print_function

import io
import json
import re
import sys

PIN_RE = re.compile(
    r"kfusion:pin\s+([A-Za-z0-9_]+)\s*\*/\s*void\s+([A-Za-z0-9_]+)\s*\(",
    re.S)


def pins_from_source(text):
    out = {}
    for m in PIN_RE.finditer(text):
        out[m.group(2)] = m.group(1)
    return out


def apply_pins(teir, pins):
    n = 0
    for node in teir.get("nodes") or []:
        pin = pins.get(node.get("name"))
        if not pin:
            continue
        allowed = list(node.get("allowed_domains") or [])
        if pin not in allowed:
            node["blocked_domains"] = list(node.get("blocked_domains") or [])
            node["blocked_domains"].append("%s:pin 不在原允许域" % pin)
            node["allowed_domains"] = []
            continue
        dropped = [d for d in allowed if d != pin]
        node["allowed_domains"] = [pin]
        node["blocked_domains"] = list(node.get("blocked_domains") or [])
        for d in dropped:
            node["blocked_domains"].append("%s:kfusion:pin" % d)
        cbd = node.get("cost_us_by_domain") or {}
        node["cost_us_by_domain"] = {k: v for k, v in cbd.items() if k == pin}
        node["migration_reason"] = "源注释钉在 %s" % pin
        n += 1
    return n


def main():
    if len(sys.argv) != 3:
        print("usage: pin_teir.py teir.json source.c", file=sys.stderr)
        return 2
    teir = json.load(io.open(sys.argv[1], encoding="utf-8"))
    src = io.open(sys.argv[2], encoding="utf-8").read()
    n = apply_pins(teir, pins_from_source(src))
    json.dump(teir, io.open(sys.argv[1], "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("  pin %d 个阶段" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
